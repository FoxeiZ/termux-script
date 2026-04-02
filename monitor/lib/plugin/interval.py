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
    async def _start(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.start()
                self._attempts = 0

            except asyncio.CancelledError:
                self.logger.info("plugin %s task was cancelled", self.name)
                raise

            except Exception as e:
                self._attempts += 1
                self.logger.error("plugin %s failed: %s", self.name, e, stack_info=True)

                if not self.restart_on_failure:
                    self.logger.info("plugin %s will not restart (restart_on_failure=False)", self.name)
                    break

                if self._max_retries != -1 and self._attempts >= self._max_retries:
                    self.logger.error("max retries reached for plugin %s; giving up", self.name)
                    break

                await self.wait_backoff()

            if self.interval <= 0:
                raise RuntimeError("interval must be a positive integer")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
                break
