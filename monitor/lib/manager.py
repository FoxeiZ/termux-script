from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Literal

from .errors import DuplicatePluginError, PluginNotLoadedError
from .plugin import DaemonPlugin, IntervalPlugin, OneTimePlugin, Plugin
from .utils import get_logger, log_function_call

__all__ = ["PluginManager", "PluginTypeDict"]

PluginTypeDict = Literal["once", "daemon", "interval"]


logger = get_logger("PluginManager")


class PluginManager:
    if TYPE_CHECKING:
        plugins: list[Plugin]
        max_retries: int
        retry_delay: int
        _webhook_url: str | None
        _stopped: bool

    def __init__(
        self, max_retries: int = 3, retry_delay: int = 5, webhook_url: str | None = None
    ) -> None:
        self.plugins = []

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
            logger.error(
                f"Plugin {plugin.__name__} failed to load: {e.__class__.__name__}: {e}"
            )
            return

        except Exception as e:
            logger.error(
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
        logger.info("Starting plugin manager...")

        try:
            for plugin in self.plugins:
                thread = threading.Thread(
                    target=plugin._start,
                    daemon=False,
                )
                thread.start()
                plugin._thread = thread

            logger.info(f"Plugin manager started with {len(self.plugins)} plugins")

        except Exception as e:
            logger.error(f"Failed to start plugin manager: {e}")
            self.stop()
            raise

    @log_function_call
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

    @log_function_call
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
