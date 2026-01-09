# ruff: noqa: S311

from __future__ import annotations

import io
import random
import time
from abc import abstractmethod
from typing import TYPE_CHECKING

import requests

from lib.utils import get_logger

if TYPE_CHECKING:
    import threading
    from logging import Logger
    from typing import Any

    from lib._types import WebhookPayload
    from lib.manager import PluginManager


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        name: str
        manager: PluginManager
        logger: Logger
        webhook_url: str
        _requires_root: bool
        _message_id: str | None
        _thread: threading.Thread | None
        __http_session: requests.Session | None

    __slots__ = (
        "__http_session",
        "_message_id",
        "_requires_root",
        "_restart_on_failure",
        "_thread",
        "logger",
        "manager",
        "name",
        "webhook_url",
    )

    def __init__(
        self,
        manager: PluginManager,
        webhook_url: str = "",
        *,
        name: str | None = None,
        requires_root: bool | None = None,
        restart_on_failure: bool | None = None,
        http_session: requests.Session | None = None,
    ) -> None:
        """Initialize plugin."""
        self.manager = manager
        self.webhook_url = webhook_url
        self.name = (
            name
            if name is not None
            else self.__class__.name or getattr(self, "name", self.__class__.__name__) or self.__class__.__name__
        )
        self.logger = get_logger(name=self.name)

        self._requires_root = (
            requires_root if requires_root is not None else getattr(self.__class__, "_requires_root", False)
        )
        self._restart_on_failure = (
            restart_on_failure
            if restart_on_failure is not None
            else getattr(self.__class__, "_restart_on_failure", False)
        )
        self._message_id = None
        self._thread = None

        self.__http_session = http_session or requests.Session() if self.webhook_url else None

    def __init_subclass__(
        cls,
        name: str = "",
        requires_root: bool = False,
        restart_on_failure: bool = False,
    ) -> None:
        """Support class-level parameters like ``class CustomPlugin(Plugin, name="...")``"""
        super().__init_subclass__()
        if name:
            cls.name = name
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
    def thread(self) -> threading.Thread | None:
        """Return the thread if it exists, otherwise None."""
        return self._thread

    @thread.setter
    def thread(self, thread: threading.Thread | None) -> None:
        """Set the thread for the plugin."""
        self._thread = thread

    @property
    def http_session(self) -> requests.Session:
        if not self.__http_session:
            self.__http_session = requests.Session()
        return self.__http_session

    def send_webhook(
        self,
        payload: WebhookPayload,
        wait: bool = False,
        *args: Any,
        **kwargs: Any,
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

        resp = self.http_session.post(
            self.webhook_url,
            json=payload,
            params={"wait": wait} if wait else None,
            timeout=self.manager.retry_delay,
            *args,  # noqa: B026
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
        self.http_session.patch(url, json=payload, timeout=self.manager.retry_delay)

    def send_message(self, title: str, description: str, color: int, content: str | None, wait: bool):
        files = None
        if content and len(content) > 2000:
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

        If needed, override this method to fit your plugin's needs.
        """
        attempts = 0
        base_delay = max(1, self.manager.retry_delay)
        max_retries = self.manager.max_retries
        max_backoff = 300

        while True:
            try:
                self.start()
                attempts = 0
                return

            except Exception as e:
                attempts += 1
                self.logger.error(f"Plugin {self.name} failed: {e}")
                self.logger.exception(e)

                if not self.restart_on_failure:
                    break

                if max_retries > 0 and attempts >= max_retries:
                    self.logger.error("Max retries reached for plugin %s; giving up", self.name)
                    break

                backoff = min(base_delay * (2 ** (attempts - 1)), max_backoff)
                jitter = backoff * 0.1
                delay = backoff + random.uniform(-jitter, jitter)
                delay = max(0.1, delay)

                self.logger.info(
                    "Restarting plugin %s in %.1fs (attempt %d/%s)",
                    self.name,
                    delay,
                    attempts,
                    str(max_retries) if max_retries > 0 else "∞",
                )

                time.sleep(delay)

    @abstractmethod
    def start(self) -> None:
        """The main entry point for the plugin.
        This method should be overridden by the plugin implementation.

        If the plugin runs indefinitely, this method should block until the plugin is stopped.

        If the plugin want to retry on failure, it should raise an exception on failure. Don't catch exceptions here.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the plugin. Default implementation does nothing."""

    def force_stop(self) -> None:
        """Force stop the plugin. Default implementation does nothing."""
