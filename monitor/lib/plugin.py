from __future__ import annotations

import threading
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    import threading

    from ._types import WebhookPayload
    from .manager import PluginManager


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        name: str
        manager: PluginManager
        webhook_url: str
        _message_id: str | None
        _thread: threading.Thread | None
        _http_session: requests.Session

    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        """Initialize plugin."""
        self.name = self.__class__.__name__
        self.manager = manager
        self.webhook_url = webhook_url

        self._thread = None
        if self.webhook_url:
            self._http_session = requests.Session()

    @property
    def http_session(self) -> requests.Session:
        if not self._http_session:
            self._http_session = requests.Session()
        return self._http_session

    def send_webhook(self, payload: WebhookPayload, wait: bool = False) -> None:
        """Send a message to the webhook."""
        if not self.webhook_url:
            return

        payload.setdefault("username", self.name)
        # remove None from payload
        # payload = {k: v for k, v in payload.items() if v is not None}

        resp = self._http_session.post(
            self.webhook_url, json=payload, params={"wait": wait}
        )
        resp.raise_for_status()
        if wait:
            data = resp.json()
            self._message_id = data["id"]
            return data

    def edit_webhook(
        self,
        payload: WebhookPayload,
        msg_id: str | None = None,
    ) -> None:
        """Edit the webhook URL."""
        if not msg_id:
            msg_id = self._message_id

        url = f"{self.webhook_url}/messages/{msg_id}"
        self._http_session.patch(url, json=payload)


class OneTimePlugin(Plugin):
    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        super().__init__(manager, webhook_url)

    @abstractmethod
    def kill(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError

    def send_success(self, content: str | None = None) -> None:
        """Send a success message to the webhook."""
        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": f"{self.name} finished successfully",
                    "description": f"Plugin {self.name} has finished successfully.",
                    "fields": [
                        {
                            "name": "Output",
                            "value": content if content else "No output",
                            "inline": False,
                        }
                    ],
                    "color": 2351395,
                }
            ]
        }
        self.send_webhook(payload=payload)

    def send_error(self, content: str | None = None) -> None:
        """Send a error message to the webhook."""
        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": f"{self.name} failed",
                    "description": f"Plugin {self.name} has failed.",
                    "fields": [
                        {
                            "name": "Output",
                            "value": content if content else "No output",
                            "inline": False,
                        }
                    ],
                    "color": 14754595,
                }
            ]
        }
        self.send_webhook(payload=payload)


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
        self._stop_event.set()

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError

    def _interval_runner(self) -> None:
        while not self._stop_event.is_set():
            self.run()
            if self.wait(self.interval):
                break
