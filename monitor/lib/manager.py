from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import json
import http.client
import threading
import time
from urllib.parse import ParseResult, urlparse
import logging

from .errors import DuplicatePluginError
from .plugin import OneTimePlugin, DaemonPlugin, IntervalPlugin


__all__ = ["PluginManager", "get_logger", "PluginTypeDict"]

PluginTypeDict = Literal["once", "daemon", "interval"]
PluginType = OneTimePlugin | DaemonPlugin | IntervalPlugin


def get_logger(
    name: str,
    level: int = logging.INFO,
    handler: type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(message)s",
) -> logging.Logger:
    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(init_handler)
    return logger


logger = get_logger("PluginManager")


class PluginManager:
    if TYPE_CHECKING:
        plugins: list[PluginType]
        max_retries: int
        retry_delay: int
        _stopped: bool

    def __init__(self, max_retries: int = 3, retry_delay: int = 5) -> None:
        self.plugins = []

        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self._stopped = False

    def register_plugin(self, plugin: PluginType) -> None:
        """
        Add a plugin to the manager.

        Args:
            plugin: A class that inherits from BasePlugin.
        """
        if plugin.name in self.plugins:
            raise DuplicatePluginError(f"Plugin {plugin.name} already registered")

        self.plugins.append(plugin)

    def _create_connection(self, url: ParseResult) -> http.client.HTTPSConnection:
        """Create HTTPS connection for Discord webhook."""
        return http.client.HTTPSConnection(url.netloc)

    def _make_http_request(
        self,
        method: str,
        url: str,
        data: str | None = None,
        headers: dict | None = None,
    ) -> http.client.HTTPResponse:
        """Make HTTP request with specified method."""
        parsed_url = urlparse(url)
        conn = self._create_connection(parsed_url)

        try:
            default_headers = {"Content-Type": "application/json"}
            headers = headers or default_headers

            conn.request(
                method=method,
                url=parsed_url.path
                + ("?" + parsed_url.query if parsed_url.query else ""),
                body=data,
                headers=headers,
            )
            return conn.getresponse()
        finally:
            conn.close()

    def _check_response(self, response: http.client.HTTPResponse) -> bool:
        """Validate response from Discord webhook."""
        if response.status not in (200, 201, 204):
            self._handle_error(f"Discord API error. Status: {response.status}")
            return False
        return True

    def _handle_error(self, message: str, exception: Exception | None = None) -> None:
        """Log errors with consistent format."""
        if exception:
            logger.error(f"{message}: {str(exception)}")
        else:
            logger.error(message)

    def get(self, url: str, headers: dict | None = None) -> dict:
        """Send GET request."""
        try:
            response = self._make_http_request("GET", url, headers=headers)
            if self._check_response(response):
                return json.loads(response.read())
            return {}
        except Exception as e:
            self._handle_error("GET request failed", e)
            return {}

    def post(self, url: str, data: str | dict, headers: dict | None = None) -> bool:
        """Send POST request."""
        try:
            if isinstance(data, dict):
                data = json.dumps(data)

            response = self._make_http_request("POST", url, data, headers)
            return self._check_response(response)
        except Exception as e:
            self._handle_error("POST request failed", e)
            return False

    def send_webhook(self, webhook_url: str, embed: str | dict) -> None:
        """Send message to Discord webhook."""
        self.post(webhook_url, embed)

    def start(self) -> None:
        """
        Start all registered plugins in separate threads.

        - Runs 'every_' methods in continuous loops
        - Runs 'once_' methods one time
        - Runs 'daemon_' methods as daemon threads
        """
        logger.info("Starting plugin manager...")

        try:
            for plugin in self.plugins:
                if isinstance(plugin, OneTimePlugin):
                    thread = threading.Thread(
                        target=plugin.run,
                        daemon=False,
                    )
                    thread.start()
                    plugin._thread = thread

                elif isinstance(plugin, DaemonPlugin):
                    thread = threading.Thread(
                        target=plugin.start,
                        daemon=True,
                    )
                    thread.start()
                    plugin._thread = thread

                elif isinstance(plugin, IntervalPlugin):
                    thread = threading.Thread(
                        target=plugin._interval_runner,
                        args=(plugin,),
                        daemon=True,
                    )
                    thread.start()
                    plugin._thread = thread

            logger.info(f"Plugin manager started with {len(self.plugins)} plugins")

        except Exception as e:
            logger.error(f"Failed to start plugin manager: {e}")
            self.stop()
            raise

    def stop(self) -> None:
        """Stop all plugin threads gracefully."""
        if self._stopped:
            return

        logger.info("Stopping plugin manager...")

        for plugin in self.plugins:
            if not plugin._thread or not plugin._thread.is_alive():
                continue

            if isinstance(plugin, (DaemonPlugin, IntervalPlugin)):
                if hasattr(plugin, "stop"):
                    try:
                        plugin.stop()
                    except Exception as e:
                        logger.error(f"Failed to stop plugin {plugin.name}: {e}")
                    plugin._thread.join(timeout=5.0)

            elif isinstance(plugin, OneTimePlugin):
                plugin._thread.join(timeout=5.0)

                if plugin._thread.is_alive():
                    logger.error(f"Plugin {plugin.name} failed to stop")
                    try:
                        plugin.kill()
                    except Exception as e:
                        logger.error(f"Failed to kill plugin {plugin.name}: {e}")

        self._stopped = True
        logger.info("Plugin manager stopped")

    def run(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Ctrl+C detected. Exiting...")
        except Exception as e:
            logger.error(f"Plugin manager error: {e}")
        finally:
            self.stop()
