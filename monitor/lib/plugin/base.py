from __future__ import annotations

import asyncio
import contextlib
import secrets
from abc import abstractmethod
from typing import TYPE_CHECKING

from .notifier import DiscordNotifier

if TYPE_CHECKING:
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
        _requires_root: bool
        _task: asyncio.Task[None] | None
        notifier: DiscordNotifier | None

    __slots__ = (
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
        self.webhook_url = metadata.webhook_url
        self.name = metadata.name
        self.logger = logger

        self._requires_root = metadata.requires_root
        self._restart_on_failure = metadata.restart_on_failure
        self._task = None
        self._stop_event: asyncio.Event = asyncio.Event()

        self.notifier = None
        if self.webhook_url:
            self.notifier = DiscordNotifier(
                webhook_url=self.webhook_url,
                plugin_name=self.name,
                retry_delay=self.manager.retry_delay,
                logger=self.logger,
            )

    def __init_subclass__(
        cls,
        name: str = "",
        requires_root: bool = False,
        restart_on_failure: bool = False,
    ) -> None:
        super().__init_subclass__()
        cls.name = name or cls.__name__
        cls._requires_root = requires_root
        cls._restart_on_failure = restart_on_failure

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

    async def _start(self) -> None:
        """Start the plugin. This method get called by the manager, don't call it directly."""
        attempts = 0
        base_delay = max(1, self.manager.retry_delay)
        max_retries = self.manager.max_retries
        max_backoff = 300

        while not self._stop_event.is_set():
            try:
                await self.start()
                attempts = 0
                return

            except asyncio.CancelledError:
                self.logger.info("plugin %s task was cancelled", self.name)
                raise

            except Exception as e:
                attempts += 1
                self.logger.error("plugin %s failed: %s", self.name, e)
                self.logger.exception(e)

                if not self.restart_on_failure:
                    break

                if max_retries > 0 and attempts >= max_retries:
                    self.logger.error("max retries reached for plugin %s; giving up", self.name)
                    break

                backoff = min(base_delay * (2 ** (attempts - 1)), max_backoff)
                jitter = backoff * 0.1
                delay = backoff + (((secrets.randbelow(2001) / 1000.0) - 1.0) * jitter)
                delay = max(0.1, delay)

                self.logger.info(
                    "restarting plugin %s in %.1fs (attempt %d/%s)",
                    self.name,
                    delay,
                    attempts,
                    str(max_retries) if max_retries > 0 else "∞",
                )

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)

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
