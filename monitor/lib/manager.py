from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .errors import DuplicatePluginError, PluginNotLoadedError
from .plugins import Plugin
from .utils import get_logger, log_function_call

__all__ = ["PluginManager"]

if TYPE_CHECKING:
    import logging


class PluginManager:
    if TYPE_CHECKING:
        plugins: list[Plugin]
        logger: logging.Logger
        max_retries: int
        retry_delay: int
        _webhook_url: str | None
        _stopped: bool

    def __init__(
        self, max_retries: int = 3, retry_delay: int = 5, webhook_url: str | None = None
    ) -> None:
        self.plugins = []
        self.logger = get_logger("PluginManager")

        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self._webhook_url = webhook_url
        self._stopped = False

    @property
    def webhook_url(self) -> str | None:
        return self._webhook_url

    @log_function_call
    def register_plugin(
        self,
        plugin: type[Plugin],
        **kwargs,
    ) -> None:
        """
        Add a plugin to the manager.

        Args:
            plugin: A class that inherits from BasePlugin.
        """
        if not issubclass(plugin, Plugin):
            raise TypeError("Plugin must be a subclass of Plugin")

        try:
            kwargs.setdefault("webhook_url", self._webhook_url)
            plugin_instance = plugin(manager=self, **kwargs)

        except PluginNotLoadedError as e:
            self.logger.error(
                f"Plugin {plugin.__name__} failed to load: {e.__class__.__name__}: {e}"
            )
            return

        except Exception as e:
            self.logger.error(
                f"There was an error when trying to load the {plugin.__name__} plugin, {e.__class__.__name__}: {e}"
            )
            return

        if plugin_instance.name in [p.name for p in self.plugins]:
            raise DuplicatePluginError(
                f"Plugin {plugin_instance.name} already registered"
            )

        self.plugins.append(plugin_instance)

    @log_function_call
    def start(self) -> None:
        """
        Start all registered plugins in separate threads.

        - Runs 'IntervalPlugin' methods in continuous loops at specified intervals
        - Runs 'OneTimePlugin' methods one time
        - Runs 'DaemonPlugin' methods as daemon threads
        """
        self.logger.info("Starting plugin manager...")

        try:
            for plugin in self.plugins:
                thread = threading.Thread(
                    target=plugin._start,
                    daemon=False,
                    name=f"Plugin-{plugin.name}",
                )
                thread.start()
                plugin._thread = thread

            self.logger.info(f"Plugin manager started with {len(self.plugins)} plugins")

        except Exception as e:
            self.logger.error(f"Failed to start plugin manager: {e}")
            self.stop()
            raise

    @log_function_call
    def stop(self) -> None:
        """Stop all plugin threads gracefully."""
        if self._stopped:
            return

        self.logger.info("Stopping plugin manager...")

        for plugin in self.plugins:
            if not plugin._thread or not plugin._thread.is_alive():
                continue

            # Try graceful stop first
            try:
                plugin.stop()
            except Exception as e:
                self.logger.error(f"Failed to stop plugin {plugin.name}: {e}")

            # Wait for thread to finish
            plugin._thread.join(timeout=5.0)

            # If thread is still alive, try force stop
            if plugin._thread.is_alive():
                self.logger.warning(
                    f"Plugin {plugin.name} didn't stop gracefully, trying force stop..."
                )
                try:
                    plugin.force_stop()
                    plugin._thread.join(timeout=2.0)
                except Exception as e:
                    self.logger.error(f"Failed to force stop plugin {plugin.name}: {e}")

                if plugin._thread.is_alive():
                    self.logger.error(f"Plugin {plugin.name} failed to stop completely")

        self._stopped = True
        self.logger.info("Plugin manager stopped")

    @log_function_call
    def run(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Ctrl+C detected. Exiting...")
        except Exception as e:
            self.logger.error(f"Plugin manager error: {e}")
        finally:
            self.stop()
