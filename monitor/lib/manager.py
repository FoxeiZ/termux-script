from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Literal

from .errors import DuplicatePluginError
from .plugin import DaemonPlugin, IntervalPlugin, OneTimePlugin

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
