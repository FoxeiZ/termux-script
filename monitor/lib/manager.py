from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Literal

from .errors import DuplicatePluginError, PluginNotLoadedError
from .plugin import DaemonPlugin, IntervalPlugin, OneTimePlugin, Plugin

__all__ = ["PluginManager", "get_logger", "PluginTypeDict"]

PluginTypeDict = Literal["once", "daemon", "interval"]


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
        plugins: list[Plugin]
        max_retries: int
        retry_delay: int
        webhook_url: str | None
        _stopped: bool

    def __init__(
        self, max_retries: int = 3, retry_delay: int = 5, webhook_url: str | None = None
    ) -> None:
        self.plugins = []

        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.webhook_url = webhook_url

        self._stopped = False

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
            kwargs.setdefault("webhook_url", self.webhook_url)
            plugin_instance = plugin(manager=self, **kwargs)
        except PluginNotLoadedError as e:
            logger.error(
                f"Plugin {plugin.__name__} failed to load: {e.__class__.__name__}: {e}"
            )
            return
        except Exception as e:
            logger.error(
                f"There was an error when trying to load the plugin, {e.__class__.__name__}: {e}"
            )
            return

        if plugin_instance.name in [p.name for p in self.plugins]:
            raise DuplicatePluginError(
                f"Plugin {plugin_instance.name} already registered"
            )

        self.plugins.append(plugin_instance)

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
