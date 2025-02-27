from __future__ import annotations

from abc import abstractmethod
import threading
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    import threading
    from manager import PluginManager


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        name: str
        manager: PluginManager
        webhook_url: str
        _thread: threading.Thread | None

    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        """Initialize plugin."""
        self.name = self.__class__.__name__
        self.manager = manager
        self.webhook_url = webhook_url

        self._thread = None


class OneTimePlugin(Plugin):
    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        super().__init__(manager, webhook_url)

    @abstractmethod
    def kill(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError


class DaemonPlugin(Plugin):
    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        super().__init__(manager, webhook_url)

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError


class IntervalPlugin(Plugin):
    if TYPE_CHECKING:
        interval: int
        # _stop_requested: bool
        _stop_event: threading.Event

    def __init__(
        self, manager: PluginManager, interval: int, webhook_url: str = ""
    ) -> None:
        super().__init__(manager, webhook_url)
        self.interval = interval

        # self._stop_requested = False
        self._stop_event = threading.Event()

    def wait(self, timeout: int) -> bool:
        return self._stop_event.wait(timeout)

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        """Stop the plugin."""
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join()

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError

    def _interval_runner(self) -> None:
        while not self._stop_event.is_set():
            self.run()
            if self.wait(self.interval):
                break
