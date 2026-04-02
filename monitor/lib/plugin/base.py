from __future__ import annotations

import asyncio
import contextlib
import secrets
from abc import abstractmethod
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict, cast, overload

from .notifier import DiscordNotifier

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from logging import Logger

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        metadata: PluginMetadata
        name: str
        manager: PluginManager
        logger: Logger
        webhook_url: str
        _cls_name: ClassVar[str]
        _cls_base_delay: ClassVar[int | None]
        _cls_max_backoff: ClassVar[int | None]
        _cls_max_retries: ClassVar[int | None]
        _cls_requires_root: ClassVar[bool]
        _cls_restart_on_failure: ClassVar[bool]
        _max_retries: int
        _requires_root: bool
        _task: asyncio.Task[None] | None
        notifier: DiscordNotifier | None

    __slots__ = (
        "_attempts",
        "_base_delay",
        "_max_backoff",
        "_max_retries",
        "_requires_root",
        "_restart_on_failure",
        "_stop_event",
        "_task",
        "logger",
        "manager",
        "metadata",
        "name",
        "notifier",
        "webhook_url",
    )

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ) -> None:
        self.manager = manager
        self.metadata = metadata
        self.logger = logger
        self._task = None
        self._stop_event = asyncio.Event()
        self._attempts = 0

        self.webhook_url = self._resolve_params(
            params=[
                (metadata, "webhook_url", None),
                (self.__class__, "_class_webhook_url", None),
                (self.manager, "webhook_url", None),
            ],
            default="",
            caster=str,
            checker=lambda value: bool(value.strip()),
        )
        self.name = self._resolve_params(
            params=[
                (metadata, "name", None),
                (self.__class__, "_cls_name", None),
                (self.manager, "default_plugin_name", None),
            ],
            default=self.__class__.__name__,
            caster=str,
            checker=lambda value: bool(value.strip()),
        )

        self._requires_root = self._resolve_params(
            params=[
                (metadata, "requires_root", None),
                (self.__class__, "_cls_requires_root", None),
                (self.manager, "requires_root", None),
            ],
            default=False,
            caster=None,
            checker=lambda value: isinstance(value, bool),
        )
        self._restart_on_failure = self._resolve_params(
            params=[
                (metadata, "restart_on_failure", None),
                (self.__class__, "_cls_restart_on_failure", None),
                (self.manager, "restart_on_failure", None),
            ],
            default=False,
            caster=None,
            checker=lambda value: isinstance(value, bool),
        )
        base_delay = self._resolve_params(
            params=[
                (metadata, "base_delay", None),
                (metadata.kwargs, "base_delay", None),
                (self.__class__, "_cls_base_delay", None),
                (self.manager, "base_delay", None),
            ],
            default=1,
            caster=int,
            checker=lambda value: value >= 1,
        )
        self._base_delay = max(0.1, base_delay)
        self._max_backoff = self._resolve_params(
            params=[
                (metadata, "max_backoff", None),
                (metadata.kwargs, "max_backoff", None),
                (self.__class__, "_cls_max_backoff", None),
                (self.manager, "max_backoff", None),
            ],
            default=300,
            caster=int,
            checker=lambda value: value >= 1,
        )
        self._max_retries = self._resolve_params(
            params=[
                (metadata, "max_retries", None),
                (metadata.kwargs, "max_retries", None),
                (self.__class__, "_cls_max_retries", None),
                (self.manager, "max_retries", None),
            ],
            default=0,
            caster=int,
            checker=lambda value: value >= -1,
        )

        self.notifier = None
        if self.webhook_url:
            self.notifier = DiscordNotifier(
                webhook_url=self.webhook_url,
                plugin_name=self.name,
                retry_delay=self.manager.retry_delay,
                logger=self.logger,
            )

    @overload
    def _resolve_params[T](
        self,
        params: Sequence[tuple[object, str, T | None] | tuple[T | None]],
        default: T,
        caster: type[T] | Callable[[object], T] | None = None,
        checker: Callable[[T], bool] | None = None,
    ) -> T: ...

    @overload
    def _resolve_params[T](
        self,
        params: Sequence[tuple[object, str, T | None] | tuple[T | None]],
        default: Literal[None],
        caster: type[T] | Callable[[object], T] | None = None,
        checker: Callable[[T], bool] | None = None,
    ) -> T | None: ...

    def _resolve_params[T](
        self,
        params: Sequence[tuple[object, str, T | None] | tuple[T | None]],
        default: T | None,
        caster: type[T] | Callable[[object], T] | None = None,
        checker: Callable[[T], bool] | None = None,
    ) -> T | None:
        """Resolve the first valid value from ordered candidates.

        :param params: Ordered candidates as either `(source, name, default_value)` for attribute
            lookup or `(value,)` for a direct value.
        :param default: Value returned when no candidate passes validation.
        :param caster: Converter applied before checking. If `None`, the raw value is checked directly.
        :param checker: Return `True` for the value to be accepted. If `None`, all parsed values are accepted.
        """
        for item in params:
            if len(item) == 3:
                source, name, default_value = item
                source_value = source if name == "" else getattr(source, name, default_value)
            else:
                source_value = item[0]

            self.logger.info(
                "resolving param from source %s: got value %s", item[0] if len(item) == 3 else "direct", source_value
            )

            if source_value is None and caster is not None:
                self.logger.info("skipping param because value is None and caster is defined")
                continue

            try:
                normalized_value = cast("T", source_value) if caster is None else caster(source_value)
                self.logger.info("normalized value: %s", normalized_value)
            except (TypeError, ValueError):
                self.logger.info("failed to normalize value: %s", source_value)
                continue

            if checker is None or checker(normalized_value):
                self.logger.info("param value %s passed validation", normalized_value)
                return normalized_value

        self.logger.debug("no valid param found; returning default value: %s", default)
        return default

    def __init_subclass__(
        cls,
        name: str = "",
        requires_root: bool = False,
        restart_on_failure: bool = False,
        max_retries: int | None = None,
        base_delay: int | None = None,
        max_backoff: int | None = None,
    ) -> None:
        super().__init_subclass__()
        cls._cls_name = name or cls.__name__
        cls._cls_requires_root = requires_root
        cls._cls_restart_on_failure = restart_on_failure
        cls._cls_max_retries = max_retries
        cls._cls_base_delay = base_delay
        cls._cls_max_backoff = max_backoff

    if TYPE_CHECKING:

        class ClassParams(TypedDict):
            name: str
            requires_root: bool
            restart_on_failure: bool
            max_retries: int | None
            base_delay: int | None
            max_backoff: int | None

    @classmethod
    def _get_class_params(cls) -> ClassParams:
        return {
            "name": getattr(cls, "_cls_name", cls.__name__),
            "requires_root": getattr(cls, "_cls_requires_root", False),
            "restart_on_failure": getattr(cls, "_cls_restart_on_failure", False),
            "max_retries": getattr(cls, "_cls_max_retries", None),
            "base_delay": getattr(cls, "_cls_base_delay", None),
            "max_backoff": getattr(cls, "_cls_max_backoff", None),
        }

    @property
    def restart_on_failure(self) -> bool:
        """Return whether the plugin should restart on failure."""
        return self._restart_on_failure

    @property
    def requires_root(self) -> bool:
        """Return whether the plugin requires root privileges."""
        return self._requires_root

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @task.setter
    def task(self, task: asyncio.Task[None] | None) -> None:
        self._task = task
        self.logger.info("plugin %s task set to %s", self.name, task)

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    async def wait_backoff(self) -> None:
        backoff = min(self._base_delay * (2 ** (self._attempts - 1)), self._max_backoff)
        jitter = backoff * 0.1
        delay = backoff + (((secrets.randbelow(2001) / 1000.0) - 1.0) * jitter)
        delay = max(0.1, delay)

        self.logger.info(
            "waiting for %.1fs before restarting plugin %s (attempt %d)",
            delay,
            self.name,
            self._attempts,
        )

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)

    def _log_start_failure(self, exception: Exception) -> None:
        self.logger.error("plugin %s failed: %s", self.name, exception)

    def _on_restart_disabled(self) -> None:
        """Called when the plugin fails and restart_on_failure is disabled."""

    def _exit_after_successful_start(self) -> bool:
        """Whether the plugin should exit after a successful start() call."""
        return True

    async def _wait_before_next_cycle(self) -> bool:
        """Return `True` to stop the loop."""
        return False

    async def _start(self) -> None:
        """Start the plugin. This method get called by the manager, don't call it directly.

        You mostly want to override `start()` instead of this method,
        but you can if you want to customize the start behavior.
        """
        while not self._stop_event.is_set():
            started_successfully = False
            try:
                await self.start()
                self._attempts = 0
                started_successfully = True

            except asyncio.CancelledError:
                self.logger.info("plugin %s task was cancelled", self.name)
                raise

            except Exception as e:
                self._attempts += 1
                self._log_start_failure(e)

                if not self.restart_on_failure:
                    self._on_restart_disabled()
                    break

                if self._max_retries != -1 and self._attempts >= self._max_retries:
                    self.logger.error("max retries reached for plugin %s; giving up", self.name)
                    break

                await self.wait_backoff()

            if started_successfully and self._exit_after_successful_start():
                return

            if await self._wait_before_next_cycle():
                break

    @abstractmethod
    async def start(self) -> None:
        """The main entry point for the plugin.
        This method should be overridden by the plugin implementation.

        If the plugin runs indefinitely, this method should block until the plugin is stopped.

        If the plugin want to retry on failure, it should raise an exception on failure. Don't catch exceptions here.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the plugin. Default implementation sets the stop event to signal the plugin to stop.
        Override method should call super().stop() to ensure the stop event is set.
        """
        self._stop_event.set()

    def force_stop(self) -> None:
        """Force stop the plugin by cancelling its asyncio task."""
        self.logger.warning("force stopping plugin %s", self.name)
        if self._task and not self._task.done():
            self._task.cancel()
