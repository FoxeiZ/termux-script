from __future__ import annotations

from typing import TYPE_CHECKING, override

from .base import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.manager import PluginManager
    from lib.plugin.metadata import PluginMetadata


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
            interval_value = getattr(self.__class__, "interval", 0)
        self.interval = int(interval_value) if interval_value is not None else 0

    def wait(self, timeout: int) -> bool:
        return self._stop_event.wait(timeout)

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def on_stop(self) -> None:
        """Called when the plugin is stopped. Useful for cleanup."""

    @override
    def stop(self) -> None:
        """Stop the plugin."""
        super().stop()
        self.on_stop()

    def _start(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.start()
            except Exception as e:
                self.logger.error("plugin %s failed: %s", self.name, e)
                if not self.restart_on_failure:
                    self.logger.info("plugin %s will not restart (restart_on_failure=False)", self.name)
                    break
            if self.wait(self.interval):
                break
