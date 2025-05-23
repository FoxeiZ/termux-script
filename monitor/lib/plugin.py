from __future__ import annotations

import io
import threading
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

import requests

from .utils import get_logger

if TYPE_CHECKING:
    import threading
    from logging import Logger

    from ._types import WebhookPayload
    from .manager import PluginManager


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        name: str
        manager: PluginManager
        logger: Logger
        webhook_url: str
        _message_id: str | None
        _thread: threading.Thread | None
        _http_session: requests.Session

    def __init__(
        self,
        manager: PluginManager,
        webhook_url: str = "",
        *,
        name: str = "",
    ) -> None:
        """Initialize plugin."""
        self.name = name or self.__class__.__name__
        self.manager = manager
        self.logger = get_logger(name=self.name)
        self.webhook_url = webhook_url

        self._thread = None
        if self.webhook_url:
            self._http_session = requests.Session()

    @property
    def http_session(self) -> requests.Session:
        if not self._http_session:
            self._http_session = requests.Session()
        return self._http_session

    def send_webhook(
        self, payload: WebhookPayload, wait: bool = False, *args, **kwargs
    ) -> None:
        """Send a message to the webhook.

        Args:
            payload (WebhookPayload): The payload to send.
            wait (bool): Whether to wait for a response.
            *args: Refer to requests.post() for more options.
            **kwargs: Refer to requests.post() for more options.
        """
        if not self.webhook_url:
            return

        payload.setdefault("username", self.name)
        # remove None from payload
        # payload = {k: v for k, v in payload.items() if v is not None}

        resp = self._http_session.post(
            self.webhook_url,
            json=payload,
            params={"wait": wait} if wait else None,
            timeout=self.manager.retry_delay,
            *args,
            **kwargs,
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
        self._http_session.patch(url, json=payload, timeout=self.manager.retry_delay)

    def send_message(
        self, title: str, description: str, color: int, content: str | None, wait: bool
    ):
        files = None
        if content:
            if len(content) > 2000:
                files = {
                    "filetag": (
                        "filename",
                        io.BytesIO(content.encode("utf-8")),
                        "text/plain",
                    )
                }
                content = "Content too large, see attachment."

        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "fields": [
                        {
                            "name": "Output",
                            "value": content if content else "No output",
                            "inline": False,
                        }
                    ],
                    "color": color,
                }
            ]
        }

        self.send_webhook(payload=payload, files=files, wait=wait)

    def send_success(
        self,
        content: str | None = None,
        wait: bool = False,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
    ) -> None:
        """Send a success message to the webhook."""
        self.send_message(
            title=title or f"{self.name} finished successfully",
            description=description or f"Plugin {self.name} has finished successfully.",
            color=color or 2351395,
            content=content,
            wait=wait,
        )

    def send_error(
        self,
        content: str | None = None,
        wait: bool = False,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
    ) -> None:
        """Send a error message to the webhook."""

        self.send_message(
            title=title or f"{self.name} failed",
            description=description or f"Plugin {self.name} has failed.",
            color=color or 14754595,
            content=content,
            wait=wait,
        )

    @abstractmethod
    def _start(self) -> None:
        """Start the plugin. This method get called by the manager, don't call it directly.

        This method should be overridden by the plugin implementation.
        """
        raise NotImplementedError


class OneTimePlugin(Plugin):
    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        super().__init__(manager, webhook_url)

    @abstractmethod
    def kill(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError

    def _start(self) -> None:
        """
        Run the plugin once.
        This method get called by the manager, don't call it directly.
        """
        try:
            self.run()
            # self.send_success()
        except Exception as e:
            self.logger.error(f"Plugin {self.name} failed: {e}")


class DaemonPlugin(Plugin):
    def __init__(self, manager: PluginManager, webhook_url: str = "") -> None:
        super().__init__(manager, webhook_url)

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def _start(self) -> None:
        """
        Start the plugin. This method get called by the manager, don't call it directly.
        """
        try:
            self.start()
        except Exception as e:
            self.logger.error(f"Plugin {self.name} failed: {e}")


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

    def on_stop(self) -> None:
        """Called when the plugin is stopped. Useful for cleanup."""
        pass

    def stop(self) -> None:
        """Stop the plugin."""
        self.on_stop()
        self._stop_event.set()

    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError

    def _start(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run()
            except Exception as e:
                self.logger.error(f"Plugin {self.name} failed: {e}")
            if self.wait(self.interval):
                break
