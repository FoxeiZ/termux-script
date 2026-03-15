# ruff: noqa: S311

from __future__ import annotations

import io
import random
import threading
from abc import abstractmethod
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import requests

_RETRYABLE_HTTP_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_WEBHOOK_MAX_RETRIES: int = 3
_WEBHOOK_MAX_BACKOFF: float = 60.0
_EMBED_FIELD_MAX_LENGTH: int = 1024


def _parse_retry_after_seconds(retry_after: str | None) -> float | None:
    if not retry_after:
        return None

    try:
        return max(0.0, float(retry_after))
    except ValueError:
        pass

    try:
        retry_after_dt = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return None

    if retry_after_dt.tzinfo is None:
        retry_after_dt = retry_after_dt.replace(tzinfo=UTC)

    seconds = (retry_after_dt - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)


if TYPE_CHECKING:
    from logging import Logger
    from typing import Any

    from lib._types import WebhookPayload
    from lib.manager import PluginManager
    from lib.plugin.metadata import PluginMetadata


class Plugin:
    """Base class for plugin implementation."""

    if TYPE_CHECKING:
        metadata: PluginMetadata
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
        "_stop_event",
        "_thread",
        "logger",
        "manager",
        "metadata",
        "name",
        "webhook_url",
    )

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
        http_session: requests.Session | None = None,
    ) -> None:
        self.manager = manager
        self.metadata = metadata
        self.webhook_url = metadata.webhook_url
        self.name = metadata.name
        self.logger = logger

        self._requires_root = metadata.requires_root
        self._restart_on_failure = metadata.restart_on_failure
        self._message_id = None
        self._thread = None
        self._stop_event: threading.Event = threading.Event()

        self.__http_session = http_session or requests.Session() if self.webhook_url else None

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
    def thread(self) -> threading.Thread | None:
        """Return the thread if it exists, otherwise None."""
        return self._thread

    @thread.setter
    def thread(self, thread: threading.Thread | None) -> None:
        if self._thread and self._thread.is_alive():
            raise ValueError("Cannot set thread to a running plugin thread.")
        self._thread = thread
        self.logger.info("plugin %s thread set to %s", self.name, thread)

    @property
    def http_session(self) -> requests.Session:
        if not self.__http_session:
            self.__http_session = requests.Session()
        return self.__http_session

    def request(
        self,
        method: str,
        url: str,
        *args: Any,
        **kwargs: Any,
    ) -> requests.Response:
        """
        Args:
            method (str): HTTP method (e.g. "GET", "POST", "PATCH").
            url (str): The URL to send the request to.
            *args: Passed through to requests.Session.request().
            **kwargs: Passed through to requests.Session.request(). If
                'timeout' is not provided, defaults to
                self.manager.retry_delay.

        Returns:
            requests.Response on success.

        Raises:
            requests.exceptions.RequestException: After all retry attempts
                are exhausted or on a non-retryable error.
        """
        kwargs.setdefault("timeout", self.manager.retry_delay)
        base_delay = max(1.0, float(self.manager.retry_delay))

        for attempt in range(1, _WEBHOOK_MAX_RETRIES + 1):
            try:
                resp = self.http_session.request(method, url, *args, **kwargs)
                resp.raise_for_status()
                return resp

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in _RETRYABLE_HTTP_CODES or attempt >= _WEBHOOK_MAX_RETRIES:
                    self.logger.error(
                        "http %s %s failed for plugin %s (attempt %d/%d, status %s): %s",
                        method,
                        url,
                        self.name,
                        attempt,
                        _WEBHOOK_MAX_RETRIES,
                        status,
                        exc,
                    )
                    raise

                if status == 429 and exc.response is not None:
                    retry_after = exc.response.headers.get("Retry-After")
                    parsed_delay = _parse_retry_after_seconds(retry_after)
                    delay = parsed_delay if parsed_delay is not None else base_delay
                else:
                    backoff = min(base_delay * (2 ** (attempt - 1)), _WEBHOOK_MAX_BACKOFF)
                    jitter = backoff * 0.1
                    delay = max(0.1, backoff + random.uniform(-jitter, jitter))

                self.logger.warning(
                    "http %s %s failed for plugin %s (attempt %d/%d, status %s): %s; retrying in %.1fs",
                    method,
                    url,
                    self.name,
                    attempt,
                    _WEBHOOK_MAX_RETRIES,
                    status,
                    exc,
                    delay,
                )
                self._stop_event.wait(delay)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                if attempt >= _WEBHOOK_MAX_RETRIES:
                    self.logger.error(
                        "http %s %s failed for plugin %s after %d attempts: %s",
                        method,
                        url,
                        self.name,
                        _WEBHOOK_MAX_RETRIES,
                        exc,
                    )
                    raise

                backoff = min(base_delay * (2 ** (attempt - 1)), _WEBHOOK_MAX_BACKOFF)
                jitter = backoff * 0.1
                delay = max(0.1, backoff + random.uniform(-jitter, jitter))

                self.logger.warning(
                    "http %s %s failed for plugin %s (attempt %d/%d): %s; retrying in %.1fs",
                    method,
                    url,
                    self.name,
                    attempt,
                    _WEBHOOK_MAX_RETRIES,
                    exc,
                    delay,
                )
                self._stop_event.wait(delay)

        raise RuntimeError("request loop exited without returning or raising")

    def send_webhook(
        self,
        payload: WebhookPayload,
        wait: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Send a message to the webhook.

        Args:
            payload (WebhookPayload): The payload to send.
            wait (bool): Whether to wait for a response.
            *args: Refer to requests.Session.request() for more options.
            **kwargs: Refer to requests.Session.request() for more options.

        Returns:
            Response data if wait=True, otherwise None.
        """
        if not self.webhook_url:
            return None

        payload.setdefault("username", self.name)
        resp = self.request(
            "POST",
            self.webhook_url,
            *args,
            json=payload,
            params={"wait": wait} if wait else None,
            **kwargs,
        )
        if wait:
            data: dict[str, Any] = resp.json()
            self._message_id = data["id"]
            return data
        return None

    def edit_webhook(
        self,
        payload: WebhookPayload,
        msg_id: str | None = None,
    ) -> None:
        """Edit a previously sent webhook message."""
        if not msg_id:
            msg_id = self._message_id

        url = f"{self.webhook_url}/messages/{msg_id}"
        self.request("PATCH", url, json=payload)

    def send_message(self, title: str, description: str, color: int, content: str | None, wait: bool):
        files = None
        if content and len(content) > _EMBED_FIELD_MAX_LENGTH:
            files = {
                "filetag": (
                    "filename",
                    io.BytesIO(content.encode("utf-8")),
                    "text/plain",
                )
            }
            content = f"Output too large ({len(content)} chars), see attachment."

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

    def _start(self) -> None:
        """Start the plugin. This method get called by the manager, don't call it directly.

        If needed, override this method to fit your plugin's needs.
        """
        attempts = 0
        base_delay = max(1, self.manager.retry_delay)
        max_retries = self.manager.max_retries
        max_backoff = 300

        while not self._stop_event.is_set():
            try:
                self.start()
                attempts = 0
                return

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
                delay = backoff + random.uniform(-jitter, jitter)
                delay = max(0.1, delay)

                self.logger.info(
                    "restarting plugin %s in %.1fs (attempt %d/%s)",
                    self.name,
                    delay,
                    attempts,
                    str(max_retries) if max_retries > 0 else "∞",
                )

                self._stop_event.wait(delay)

    @abstractmethod
    def start(self) -> None:
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
        """Force stop the plugin. Default implementation logs a warning.
        Override this method to implement custom force stop behavior.
        """
        self.logger.warning("force_stop not implemented for plugin %s", self.name)
