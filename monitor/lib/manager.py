from __future__ import annotations

import contextlib
import importlib
import json
import os
import socket
import threading
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config
from .errors import DuplicatePluginError, PluginNotLoadedError
from .ipc_types import IPCCommand, IPCRequest, IPCResponse
from .plugin import Plugin
from .plugin.script import ScriptPlugin
from .utils import get_logger, log_function_call

__all__ = ["PluginManager"]


if TYPE_CHECKING:
    import logging
    from typing import Any

DIR = Path(__file__).resolve().parent


class PluginManager:
    if TYPE_CHECKING:
        plugins: list[Plugin]
        logger: logging.Logger
        max_retries: int
        retry_delay: int
        _webhook_url: str | None
        _stopped: bool

    __slots__ = (
        "_ipc_stop_event",
        "_stopped",
        "_webhook_url",
        "ipc_port",
        "ipc_thread",
        "logger",
        "max_retries",
        "plugins",
        "retry_delay",
    )

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: int = 5,
        webhook_url: str | None = None,
        ipc_port: int = 8765,
    ) -> None:
        self.plugins = []
        self.logger = get_logger(self.__class__.__name__)

        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self._webhook_url = webhook_url
        self._stopped = False

        self.ipc_port: int = ipc_port
        self.ipc_thread: threading.Thread | None = None
        self._ipc_stop_event: threading.Event | None = None

    @property
    def webhook_url(self) -> str | None:
        return self._webhook_url

    @log_function_call
    def register_plugin(
        self,
        plugin: type[Plugin],
        *args: Any,
        force: bool = False,
        **kwargs: Any,
    ) -> Plugin | None:
        """
        Add a plugin to the manager.

        Args:
            plugin: A class that inherits from BasePlugin.
            force: If True, bypass root privilege checks.
        """
        if not issubclass(plugin, Plugin):
            raise TypeError("Plugin must be a subclass of Plugin")

        if not force:
            if Config.run_script_only and plugin is not ScriptPlugin:
                self.logger.info(f"Skipping plugin {plugin.__name__} (not a script plugin in script-only env)")
                return
                # raise PluginNotLoadedError(
                #     f"Plugin {plugin.__name__} is not a script but environment is script-only. Use --force to override."  # noqa: E501
                # )
            if Config.run_root_only and not plugin._requires_root:
                self.logger.info(f"Skipping plugin {plugin.__name__} (non-root plugin in root-only env)")
                return
                # raise PluginNotLoadedError(
                #     f"Plugin {plugin.__name__} is non-root but environment is root-only. Use --force to override."
                # )

            if Config.run_non_root_only and plugin._requires_root:
                self.logger.info(f"Skipping plugin {plugin.__name__} (root plugin in non-root env)")
                return
                # raise PluginNotLoadedError(
                #     f"Plugin {plugin.__name__} requires root but environment is non-root. Use --force to override."
                # )

        try:
            kwargs.setdefault("webhook_url", self._webhook_url)
            plugin_instance = plugin(manager=self, *args, **kwargs)  # noqa: B026

        except PluginNotLoadedError as e:
            self.logger.error(f"Plugin {plugin.__name__} failed to load: {e.__class__.__name__}: {e}")
            return

        except Exception as e:
            self.logger.error(
                f"There was an error when trying to load the {plugin.__name__} plugin, {e.__class__.__name__}: {e}"
            )
            return

        if plugin_instance.name in [p.name for p in self.plugins]:
            raise DuplicatePluginError(f"Plugin {plugin_instance.name} already registered")

        self.plugins.append(plugin_instance)
        return plugin_instance

    def start_plugin(
        self,
        name: str | None = None,
        plugin: Plugin | None = None,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        """Start a plugin by name or instance. If not registered, try to import and register it.

        Args:
            name: Plugin name to start
            plugin: Plugin instance (if already have reference)
            args: Positional arguments to pass to plugin constructor if dynamically loading
            kwargs: Keyword arguments to pass to plugin constructor if dynamically loading
            force: If True, bypass root privilege checks when dynamically loading
        """
        if not name and not plugin:
            raise ValueError("Either name or plugin must be provided")

        if plugin is None:
            plugin = next((p for p in self.plugins if p.name == name), None)

        if plugin is None and name:
            plugin = self._import_and_register_plugin(name, args or [], kwargs or {}, force=force)

        if plugin is None:
            raise Exception(f"Plugin {name} not found and could not be imported")

        if plugin._thread and plugin._thread.is_alive():
            self.logger.info("Plugin %s already running", plugin.name)
            return

        thread = threading.Thread(
            target=plugin._start,
            daemon=False,
            name=f"Plugin-{plugin.name}",
        )
        thread.start()
        plugin._thread = thread
        self.logger.info("Started plugin %s", plugin.name)

    def _load_scripts(self) -> None:
        """Load scripts from monitor/scripts directory."""
        scripts_dir = DIR.parent / "scripts"
        if not scripts_dir.exists():
            return

        self.logger.info(f"Scanning for scripts in {scripts_dir}")
        for item in scripts_dir.iterdir():
            if not item.is_file():
                continue

            is_executable = item.suffix in (".py", ".sh") or os.access(item, os.X_OK)

            if is_executable:
                self.logger.info(f"Found script: {item.name}")
                try:
                    if item.suffix == ".py":
                        self.register_plugin(
                            ScriptPlugin,
                            script_path="python",
                            args=[str(item)],
                            cwd=str(item.parent),
                            use_screen=Config.scripts_use_screen,
                            force=True,
                            name=item.stem,
                        )
                    else:
                        self.register_plugin(
                            ScriptPlugin,
                            script_path=str(item),
                            use_screen=Config.scripts_use_screen,
                            force=True,
                        )
                except Exception as e:
                    self.logger.error(f"Failed to load script {item.name}: {e}")

    @log_function_call
    def start(self) -> None:
        """
        Start all registered plugins in separate threads.

        - Runs 'IntervalPlugin' methods in continuous loops at specified intervals
        - Runs 'OneTimePlugin' methods one time
        - Runs 'DaemonPlugin' methods as daemon threads
        """
        self.logger.info("Starting plugin manager...")

        success_count = 0
        try:
            self._load_scripts()
            self.start_ipc()
            for plugin in self.plugins:
                self.start_plugin(plugin=plugin)
                success_count += 1

            self.logger.info(f"Plugin manager started with {success_count} plugins")

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
                self.logger.warning(f"Plugin {plugin.name} didn't stop gracefully, trying force stop...")
                try:
                    plugin.force_stop()
                    plugin._thread.join(timeout=2.0)
                except Exception as e:
                    self.logger.error(f"Failed to force stop plugin {plugin.name}: {e}")

                if plugin._thread.is_alive():
                    self.logger.error(f"Plugin {plugin.name} failed to stop completely")

        self.stop_ipc()

        self._stopped = True
        self.logger.info("Plugin manager stopped")

    def stop_plugin(self, name: str, timeout: float = 5.0) -> None:
        plugin = next((p for p in self.plugins if p.name == name), None)
        if plugin is None:
            raise Exception(f"Plugin {name} not found")

        if not plugin._thread or not plugin._thread.is_alive():
            self.logger.info("Plugin %s not running", name)
            return

        try:
            plugin.stop()
        except Exception as e:
            self.logger.error("Failed to stop plugin %s: %s", name, e)

        plugin._thread.join(timeout=timeout)

        if plugin._thread.is_alive():
            self.logger.warning("Plugin %s didn't stop gracefully, trying force stop...", name)
            try:
                plugin.force_stop()
                plugin._thread.join(timeout=2.0)
            except Exception as e:
                self.logger.error("Failed to force stop plugin %s: %s", name, e)

        if plugin._thread.is_alive():
            self.logger.error("Plugin %s failed to stop completely", name)
        else:
            self.logger.info("Stopped plugin %s", name)

    def restart_plugin(
        self, name: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None, force: bool = False
    ) -> None:
        """Restart a plugin.

        Args:
            name: Plugin name
            args: Args to pass if re-registering
            kwargs: Kwargs to pass if re-registering
            force: If True, bypass root privilege checks
        """
        self.stop_plugin(name)
        time.sleep(0.1)
        self.start_plugin(name, args=args, kwargs=kwargs, force=force)

    def _import_and_register_plugin(
        self, plugin_name: str, args: list[Any], kwargs: dict[str, Any], force: bool = False
    ) -> Plugin | None:
        """Dynamically import and register a plugin from the plugins module.

        Args:
            plugin_name: Name of the plugin class to import
            args: Args to pass to plugin constructor
            kwargs: Kwargs to pass to plugin constructor
            force: If True, bypass root privilege checks

        Returns:
            The registered plugin instance or None if import/registration failed
        """
        try:
            plugins_module = importlib.import_module("plugins")

            # check if plugin class exists
            if not hasattr(plugins_module, plugin_name):
                self.logger.error("Plugin class %s not found in plugins module", plugin_name)
                return None

            plugin_class = getattr(plugins_module, plugin_name)

            # register with provided args, kwargs, and force flag
            return self.register_plugin(plugin_class, *args, force=force, **kwargs)

        except PluginNotLoadedError:
            # re-raise to preserve the detailed privilege error message
            raise

        except Exception as e:
            self.logger.error("Failed to import plugin %s: %s", plugin_name, e)
            return None

    def _handle_start_command(
        self, plugin_name: str, args: list[Any], kwargs: dict[str, Any], force: bool
    ) -> IPCResponse:
        if not plugin_name:
            return {
                "status": "failed",
                "message": "plugin_name is required for start command",
                "data": None,
            }

        if plugin_name.lower() == ScriptPlugin.__name__.lower():
            return {
                "status": "failed",
                "message": "Cannot start ScriptPlugin via IPC. Use specific script plugins instead.",
                "data": None,
            }

        self.start_plugin(plugin_name, args=args, kwargs=kwargs, force=force)
        return {
            "status": "ok",
            "message": f"Started plugin {plugin_name}",
            "data": None,
        }

    def _handle_stop_command(self, plugin_name: str) -> IPCResponse:
        if not plugin_name:
            return {
                "status": "failed",
                "message": "plugin_name is required for stop command",
                "data": None,
            }

        self.stop_plugin(plugin_name)
        return {
            "status": "ok",
            "message": f"Stopped plugin {plugin_name}",
            "data": None,
        }

    def _handle_restart_command(self, plugin_name: str, args: list[Any], kwargs: dict[str, Any]) -> IPCResponse:
        if not plugin_name:
            return {
                "status": "failed",
                "message": "plugin_name is required for restart command",
                "data": None,
            }

        self.restart_plugin(plugin_name, args=args, kwargs=kwargs)
        return {
            "status": "ok",
            "message": f"Restarted plugin {plugin_name}",
            "data": None,
        }

    def _handle_list_command(self) -> IPCResponse:
        plugin_list = ",".join(p.name for p in self.plugins)
        return {
            "status": "ok",
            "message": "Plugin list retrieved",
            "data": plugin_list,
        }

    def _handle_ipc_command(self, data: str) -> str:
        """Handle IPC command in JSON format.

        Args:
            data: JSON string with IPCRequest structure

        Returns:
            JSON string with IPCResponse structure
        """
        try:
            request: IPCRequest = json.loads(data)
            cmd = request.get("cmd", "").lower()
            plugin_name = request.get("plugin_name", "")
            args = request.get("args", [])
            kwargs = request.get("kwargs", {})
            force = request.get("force", False)

            try:
                ipc_cmd = IPCCommand(cmd)
            except ValueError:
                response: IPCResponse = {
                    "status": "failed",
                    "message": f"Unknown command: {cmd}",
                    "data": None,
                }
                return json.dumps(response)

            match ipc_cmd:
                case IPCCommand.START:
                    response = self._handle_start_command(plugin_name, args, kwargs, force)
                case IPCCommand.STOP:
                    response = self._handle_stop_command(plugin_name)
                case IPCCommand.RESTART:
                    response = self._handle_restart_command(plugin_name, args, kwargs, force)
                case IPCCommand.LIST:
                    response = self._handle_list_command()

            return json.dumps(response)

        except json.JSONDecodeError as e:
            self.logger.error("Failed to parse IPC request JSON: %s", e)
            response = {
                "status": "failed",
                "message": f"Invalid JSON: {e}",
                "data": traceback.format_exc(),
            }
            return json.dumps(response)

        except Exception as e:
            self.logger.error("IPC command failed: %s", e)
            response = {
                "status": "failed",
                "message": str(e),
                "data": traceback.format_exc(),
            }
            return json.dumps(response)

    def _ipc_worker(self, tcp_port: int = 8765) -> None:
        """TCP worker that listens for JSON commands on localhost.

        Args:
            tcp_port: TCP port to listen on (defaults to 8765)
        """
        stop_event = self._ipc_stop_event or threading.Event()

        port = tcp_port or (self.ipc_port or 8765)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        self.logger.info("IPC TCP server listening on 127.0.0.1:%d", port)
        srv.settimeout(1.0)
        try:
            while not stop_event.is_set():
                try:
                    conn, _ = srv.accept()
                except TimeoutError:
                    continue
                with conn:
                    data = conn.recv(4096).decode("utf-8", errors="ignore")
                    response = self._handle_ipc_command(data)
                    with contextlib.suppress(Exception):
                        conn.sendall((response + "\n").encode("utf-8"))
        finally:
            srv.close()

    def start_ipc(self, tcp_port: int = 8765) -> None:
        if self.ipc_thread and self.ipc_thread.is_alive():
            self.logger.info("IPC already running")
            return

        self.ipc_port = tcp_port
        self._ipc_stop_event = threading.Event()
        th = threading.Thread(
            target=self._ipc_worker,
            args=(self.ipc_port,),
            daemon=True,
            name="IPC-Listener",
        )
        th.start()
        self.ipc_thread = th
        self.logger.info("IPC listener started")

    def stop_ipc(self) -> None:
        if not self.ipc_thread:
            return
        if not self._ipc_stop_event:
            return
        self._ipc_stop_event.set()
        self.ipc_thread.join(timeout=2.0)
        self.ipc_thread = None
        self._ipc_stop_event = None
        self.logger.info("IPC listener stopped")

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
