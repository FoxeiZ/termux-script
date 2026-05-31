from __future__ import annotations

import asyncio
import contextlib
import secrets
from abc import abstractmethod
from typing import TYPE_CHECKING

from ..notifier import DiscordNotifier
from ..utils import get_logger

if TYPE_CHECKING:
    from logging import Logger

    from ..config import ConfigLoader, ConfigT


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        name: str
        logger: Logger
        webhook_url: str
        config: ConfigLoader[ConfigT]
        _max_retries: int
        _task: asyncio.Task[None] | None
        notifier: DiscordNotifier | None

    __slots__ = (
        "_attempts",
        "_base_delay",
        "_max_backoff",
        "_max_retries",
        "_restart_on_failure",
        "_stop_event",
        "_task",
        "config",
        "logger",
        "name",
        "notifier",
        "webhook_url",
    )

    def __init__(self, config: ConfigLoader[ConfigT]) -> None:
        self.logger = get_logger(config.name)
        self.config = config

        self._task = None
        self._stop_event = asyncio.Event()
        self._attempts = 0

        self.webhook_url = config.webhook_url or ""
        self.name = config.name
        self._restart_on_failure = config.restart_on_failure
        base_delay = config.base_delay
        self._base_delay = max(0.1, base_delay)
        self._max_backoff = config.max_backoff
        self._max_retries = config.max_retries
        self.notifier = None
        if self.webhook_url:
            self.notifier = DiscordNotifier(
                webhook_url=self.webhook_url,
                plugin_name=self.name,
                logger=self.logger,
                retry_delay=self._base_delay,
            )

    @property
    def restart_on_failure(self) -> bool:
        """Return whether the plugin should restart on failure."""
        return self._restart_on_failure

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
        if self._stop_event.is_set():
            self.logger.debug("stop event already set for plugin %s, skipping restart backoff", self.name)
            return

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

                if self._stop_event.is_set():
                    self.logger.debug("stop requested for plugin %s, skipping restart", self.name)
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
