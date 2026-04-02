from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, override

from .base import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class IntervalPlugin(Plugin):
    if TYPE_CHECKING:
        interval: int

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ) -> None:
        super().__init__(manager, metadata, logger)
        interval_value = metadata.kwargs.get("interval")
        if interval_value is None and metadata.args:
            interval_value = metadata.args[0]
        if interval_value is None:
            interval_value = getattr(self.__class__, "interval", 60)
        self.interval = int(interval_value) if interval_value is not None else 60

    async def wait(self, timeout: float | int) -> bool:
        """Wait for the specified timeout or until the plugin is stopped.
        Returns True if the plugin was stopped, False if the timeout was reached.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return True
        return False

    def on_stop(self) -> None:
        """Called when the plugin is stopped. Useful for cleanup."""

    @override
    def stop(self) -> None:
        """Stop the plugin."""
        super().stop()
        self.on_stop()

    @override
    def _log_start_failure(self, exception: Exception) -> None:
        self.logger.error("plugin %s failed: %s", self.name, exception, stack_info=True)

    @override
    def _on_restart_disabled(self) -> None:
        self.logger.info("plugin %s will not restart (restart_on_failure=False)", self.name)

    @override
    def _exit_after_successful_start(self) -> bool:
        return False

    @override
    async def _wait_before_next_cycle(self) -> bool:
        if self.interval <= 0:
            raise RuntimeError("interval must be a positive integer")
        return await self.wait(self.interval)
