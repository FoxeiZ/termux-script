# ruff: noqa: S311

from __future__ import annotations

import asyncio
import io
import json
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from lib.types import WebhookPayload

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


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        plugin_name: str,
        retry_delay: float,
        logger: Any,
    ) -> None:
        self.webhook_url = webhook_url
        self.plugin_name = plugin_name
        self.retry_delay = max(1.0, retry_delay)
        self.logger = logger
        self._message_id: str | None = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: object | None = None,
        data_payload: dict[str, str] | None = None,
        files_payload: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        request_timeout = timeout if timeout is not None else float(self.retry_delay)

        for attempt in range(1, _WEBHOOK_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_payload,
                        data=data_payload,
                        files=files_payload,
                    )

                    if response.status_code < 400:
                        if "application/json" in response.headers.get("content-type", ""):
                            data = response.json()
                            if isinstance(data, dict):
                                return data
                            return None
                        return None

                    body = response.text
                    if response.status_code not in _RETRYABLE_HTTP_CODES or attempt >= _WEBHOOK_MAX_RETRIES:
                        self.logger.error(
                            "http %s %s failed for plugin %s (attempt %d/%d, status %s): %s",
                            method,
                            url,
                            self.plugin_name,
                            attempt,
                            _WEBHOOK_MAX_RETRIES,
                            response.status_code,
                            body,
                        )
                        response.raise_for_status()

                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        parsed_delay = _parse_retry_after_seconds(retry_after)
                        delay = parsed_delay if parsed_delay is not None else self.retry_delay
                    else:
                        backoff = min(self.retry_delay * (2 ** (attempt - 1)), _WEBHOOK_MAX_BACKOFF)
                        jitter = backoff * 0.1
                        delay = max(0.1, backoff + random.uniform(-jitter, jitter))

                    self.logger.warning(
                        "http %s %s failed for plugin %s (attempt %d/%d, status %s): %s; retrying in %.1fs",
                        method,
                        url,
                        self.plugin_name,
                        attempt,
                        _WEBHOOK_MAX_RETRIES,
                        response.status_code,
                        body,
                        delay,
                    )
                    await asyncio.sleep(delay)

            except (httpx.RequestError, TimeoutError) as exc:
                if attempt >= _WEBHOOK_MAX_RETRIES:
                    self.logger.error(
                        "http %s %s failed for plugin %s after %d attempts: %s",
                        method,
                        url,
                        self.plugin_name,
                        _WEBHOOK_MAX_RETRIES,
                        exc,
                    )
                    raise

                backoff = min(self.retry_delay * (2 ** (attempt - 1)), _WEBHOOK_MAX_BACKOFF)
                jitter = backoff * 0.1
                delay = max(0.1, backoff + random.uniform(-jitter, jitter))
                self.logger.warning(
                    "http %s %s failed for plugin %s (attempt %d/%d): %s; retrying in %.1fs",
                    method,
                    url,
                    self.plugin_name,
                    attempt,
                    _WEBHOOK_MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("request loop exited without returning or raising")

    async def send_webhook(
        self,
        payload: WebhookPayload,
        wait: bool = False,
        files: dict[str, tuple[str, io.BytesIO, str]] | None = None,
    ) -> dict[str, Any] | None:
        if not self.webhook_url:
            return None

        payload.setdefault("username", self.plugin_name)
        data_payload: dict[str, str] | None = None
        files_payload: dict[str, tuple[str, bytes, str]] | None = None
        json_payload: object | None = payload

        if files:
            files_payload = {}
            for field_name, (filename, buffer, content_type) in files.items():
                files_payload[field_name] = (filename, buffer.getvalue(), content_type)
            data_payload = {"payload_json": json.dumps(payload)}
            json_payload = None

        data = await self.request(
            "POST",
            self.webhook_url,
            params={"wait": True} if wait else None,
            json_payload=json_payload,
            data_payload=data_payload,
            files_payload=files_payload,
        )
        if wait and isinstance(data, dict):
            message_id = data.get("id")
            if isinstance(message_id, str):
                self._message_id = message_id
            return data
        return None

    async def edit_webhook(
        self,
        payload: WebhookPayload,
        msg_id: str | None = None,
    ) -> None:
        message_id = msg_id or self._message_id
        if not message_id or not self.webhook_url:
            return

        url = f"{self.webhook_url}/messages/{message_id}"
        await self.request("PATCH", url, json_payload=payload)

    async def send_message(
        self,
        title: str,
        description: str,
        color: int,
        content: str | None,
        wait: bool,
    ) -> None:
        files: dict[str, tuple[str, io.BytesIO, str]] | None = None
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

        await self.send_webhook(payload=payload, files=files, wait=wait)

    async def send_success(
        self,
        content: str | None = None,
        wait: bool = False,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
    ) -> None:
        await self.send_message(
            title=title or f"{self.plugin_name} finished successfully",
            description=description or f"Plugin {self.plugin_name} has finished successfully.",
            color=color or 2351395,
            content=content,
            wait=wait,
        )

    async def send_error(
        self,
        content: str | None = None,
        wait: bool = False,
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | None = None,
    ) -> None:
        await self.send_message(
            title=title or f"{self.plugin_name} failed",
            description=description or f"Plugin {self.plugin_name} has failed.",
            color=color or 14754595,
            content=content,
            wait=wait,
        )
