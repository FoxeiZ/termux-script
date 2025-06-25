from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, override

from .base import Plugin

if TYPE_CHECKING:
    from lib.manager import PluginManager


class IntervalPlugin(Plugin):
    if TYPE_CHECKING:
        interval: int
        # _stop_requested: bool
        _stop_event: threading.Event

    def __init__(
        self,
        manager: PluginManager,
        interval: int,
        webhook_url: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(manager, webhook_url, **kwargs)
        self.interval = interval

        # self._stop_requested = False
        self._stop_event = threading.Event()

    def wait(self, timeout: int) -> bool:
        return self._stop_event.wait(timeout)

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def on_stop(self) -> None:
        """Called when the plugin is stopped. Useful for cleanup."""
        pass

    @override
    def stop(self) -> None:
        """Stop the plugin."""
        self.on_stop()
        self._stop_event.set()

    def _start(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.start()
            except Exception as e:
                self.logger.error(f"Plugin {self.name} failed: {e}")
            if self.wait(self.interval):
                break
