from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import socket
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DIR, IS_TERMUX, IS_WINDOWS, Config
from .errors import DuplicatePluginError
from .ipc import recv_str, send_json
from .ipc_types import IPCCommand, IPCRequest, IPCResponse, PipeCommand, PipeRequest, PipeResponse
from .plugin import Plugin
from .plugin.metadata import PluginMetadata
from .plugin.script import ScriptPlugin
from .utils import configure_queue_logging, get_logger, log_function_call, setup_logging_queue

__all__ = ["Manager", "PluginManager"]


if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping
    from multiprocessing.connection import PipeConnection


class PluginManager(multiprocessing.Process):
    def __init__(
        self,
        role: str,
        pipe: PipeConnection,
        log_queue: multiprocessing.Queue[logging.LogRecord],
        max_retries: int,
        retry_delay: int,
        webhook_url: str | None,
    ) -> None:
        super().__init__(name=f"PluginManager-{role}")
        self.role = role
        if role not in ("root", "non-root"):
            raise ValueError("role must be 'root' or 'non-root'")
        self.pipe = pipe
        self.log_queue = log_queue
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.webhook_url = webhook_url or ""
        self.plugins: dict[str, Plugin] = {}
        self.metadata_by_name: dict[str, PluginMetadata] = {}
        self.logger: logging.Logger = get_logger(f"plugin_manager.{role}", handler=None)

    def run(self) -> None:
        configure_queue_logging(self.log_queue)
        self._drop_privileges_if_needed()
        self.logger.info("starting plugin manager process")
        request_id = ""
        try:
            while True:
                try:
                    if not self.pipe.poll(0.5):
                        continue

                    request = self.pipe.recv()
                    request_id = request["id"]
                    response = self._handle_request(request)
                    self.pipe.send(response)

                    if request["cmd"] == PipeCommand.SHUTDOWN:
                        self.logger.info("shutdown complete, exiting plugin manager process")
                        return

                except EOFError:
                    self.logger.info("pipe closed")
                    return

                except KeyboardInterrupt:
                    pass

                except Exception as exc:
                    error_response: PipeResponse = {
                        "id": request_id or uuid.uuid4().hex,
                        "status": "failed",
                        "message": str(exc),
                        "data": traceback.format_exc() if Config.debug else None,
                    }
                    try:
                        self.pipe.send(error_response)
                    except Exception:
                        self.logger.info("pipe send failed, exiting")
                        return
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        self.logger.debug("cleaning up %s plugin manager resources", self.role)
        try:
            self.pipe.close()
        except Exception as exc:
            self.logger.debug("failed to close pipe: %s", exc)

        try:
            self.log_queue.cancel_join_thread()
        except Exception as exc:
            self.logger.debug("failed to cancel log queue join thread: %s", exc)

    def _drop_privileges_if_needed(self) -> None:
        if IS_WINDOWS:
            return

        if os.getuid() != 0 or os.geteuid() != 0:
            self.logger.info("not running as root, no need to drop privileges")
            return

        if self.role != "non-root":
            return

        if not IS_TERMUX:
            self.logger.warning("non-root role requested but not running in Termux, cannot drop privileges")
            return

        self.logger.info("dropping root privileges for non-root role in Termux")
        try:
            p = subprocess.run(["dumpsys", "package", "com.termux"], check=True, capture_output=True)
            for line in p.stdout.decode().splitlines():
                s_line = line.strip()
                if s_line.startswith("userId="):
                    uid_str = s_line.split("=", 1)[1].strip()
                    uid = int(uid_str)
                    os.setgid(uid)
                    os.setuid(uid)
                    self.logger.info("dropped privileges to uid/gid %d", uid)
                    return
        except Exception as exc:
            self.logger.error("failed to drop privileges: %s", exc)

    def _handle_request(self, request: PipeRequest) -> PipeResponse:
        cmd = request["cmd"]
        request_id = request["id"]

        if cmd == PipeCommand.LOAD:
            metadata = request.get("metadata")
            if not metadata:
                return self._response(request_id, "failed", "metadata is required", None)
            return self._pipe_load_plugin(metadata, request_id)

        if cmd == PipeCommand.START:
            return self._pipe_start_plugin(request)

        if cmd == PipeCommand.STOP:
            return self._pipe_stop_plugin(request)

        if cmd == PipeCommand.RESTART:
            return self._pipe_restart_plugin(request)

        if cmd == PipeCommand.LIST:
            return self._response(request_id, "ok", "ok", list(self.metadata_by_name.keys()))

        if cmd == PipeCommand.SHUTDOWN:
            self._stop_all()
            return self._response(request_id, "ok", "ok", None)

        return self._response(request_id, "failed", "unsupported command", None)

    def _response(self, request_id: str, status: str, message: str, data: Any | None) -> PipeResponse:
        return {
            "id": request_id,
            "status": status,
            "message": message,
            "data": data,
        }

    def load_plugin(self, metadata: PluginMetadata) -> None:
        """load a plugin from metadata. raises exception on failure."""
        if metadata.name in self.plugins:
            self.logger.debug("plugin %s already loaded, skipping", metadata.name)
            return

        self.logger.info("loading plugin %s from %s", metadata.name, metadata.module_path)
        try:
            module = importlib.import_module(metadata.module_path)
        except Exception as exc:
            self.logger.error("failed to import module %s: %s", metadata.module_path, exc)
            raise ImportError(f"import failed: {exc}") from exc

        plugin_cls = getattr(module, metadata.class_name, None)
        if not plugin_cls or not issubclass(plugin_cls, Plugin):
            self.logger.error("invalid plugin class %s in module %s", metadata.class_name, metadata.module_path)
            raise TypeError("invalid plugin class")

        logger = get_logger(f"plugin.{metadata.name}", handler=None)
        try:
            plugin = plugin_cls(manager=self, metadata=metadata, logger=logger)
        except Exception as exc:
            self.logger.error("failed to initialize plugin %s: %s", metadata.name, exc)
            raise RuntimeError(f"init failed: {exc}") from exc

        self.plugins[metadata.name] = plugin
        self.metadata_by_name[metadata.name] = metadata
        self.logger.info("plugin %s loaded successfully", metadata.name)

    def start_plugin(self, plugin_name: str, metadata: PluginMetadata | None = None) -> None:
        """start a plugin by name. raises exception on failure."""
        self.logger.info("starting plugin %s", plugin_name)
        plugin = self.plugins.get(plugin_name)
        if plugin is None and metadata:
            self.load_plugin(metadata)
            plugin = self.plugins.get(plugin_name)

        if plugin is None:
            self.logger.error("plugin %s not loaded", plugin_name)
            raise ValueError("plugin not loaded")

        if plugin.thread and plugin.thread.is_alive():
            self.logger.debug("plugin %s already running, skipping", plugin_name)
            return

        thread = threading.Thread(target=plugin._start, daemon=False, name=f"Plugin-{plugin.name}")
        plugin.thread = thread
        thread.start()
        self.logger.info("plugin %s started", plugin_name)

    def stop_plugin(self, plugin_name: str) -> None:
        """stop a plugin by name. raises exception on failure."""
        self.logger.info("stopping plugin %s", plugin_name)
        plugin = self.plugins.get(plugin_name)
        if not plugin:
            self.logger.error("plugin %s not loaded", plugin_name)
            raise ValueError("plugin not loaded")

        if not plugin.thread:
            self.logger.debug("plugin %s has no thread, nothing to stop", plugin_name)
            return

        self.logger.debug("sending stop signal to plugin %s", plugin_name)
        plugin.stop()
        plugin.thread.join(timeout=2.0)
        if plugin.thread.is_alive():
            self.logger.warning("plugin %s did not stop gracefully, forcing stop", plugin_name)
            plugin.force_stop()
            plugin.thread.join(timeout=2.0)

        if plugin.thread.is_alive():
            self.logger.error("plugin %s failed to stop after force stop, marking as zombie", plugin_name)
            plugin._thread = None
            raise RuntimeError("failed to stop")

        plugin.thread = None
        self.logger.info("plugin %s stopped", plugin_name)

    def restart_plugin(self, plugin_name: str, metadata: PluginMetadata | None = None) -> None:
        """restart a plugin by name. raises exception on failure."""
        self.logger.info("restarting plugin %s", plugin_name)
        self.stop_plugin(plugin_name)
        self.start_plugin(plugin_name, metadata)
        self.logger.info("plugin %s restarted", plugin_name)

    def _pipe_load_plugin(self, metadata: PluginMetadata, request_id: str) -> PipeResponse:
        """pipe wrapper for load_plugin."""
        try:
            self.load_plugin(metadata)
            return self._response(request_id, "ok", "loaded", None)
        except Exception as exc:
            self.logger.error("pipe_load_plugin failed for %s: %s", metadata.name, exc)
            return self._response(request_id, "failed", str(exc), traceback.format_exc() if Config.debug else None)

    def _pipe_plugin_action(
        self,
        request: PipeRequest,
        action: str,
        handler: Any,
        success_message: str,
    ) -> PipeResponse:
        """generic pipe wrapper for plugin actions (start/stop/restart)."""
        request_id = request["id"]
        plugin_name = request.get("plugin_name")
        if not plugin_name:
            self.logger.error("pipe_%s_plugin called without plugin_name", action)
            return self._response(request_id, "failed", "plugin_name is required", None)

        try:
            # pass metadata only for actions that need it (start/restart)
            if action in ("start", "restart"):
                handler(plugin_name, request.get("metadata"))
            else:
                handler(plugin_name)
            return self._response(request_id, "ok", success_message, None)
        except Exception as exc:
            self.logger.error("pipe_%s_plugin failed for %s: %s", action, plugin_name, exc)
            return self._response(request_id, "failed", str(exc), traceback.format_exc() if Config.debug else None)

    def _pipe_start_plugin(self, request: PipeRequest) -> PipeResponse:
        """pipe wrapper for start_plugin."""
        return self._pipe_plugin_action(request, "start", self.start_plugin, "started")

    def _pipe_stop_plugin(self, request: PipeRequest) -> PipeResponse:
        """pipe wrapper for stop_plugin."""
        return self._pipe_plugin_action(request, "stop", self.stop_plugin, "stopped")

    def _pipe_restart_plugin(self, request: PipeRequest) -> PipeResponse:
        """pipe wrapper for restart_plugin."""
        return self._pipe_plugin_action(request, "restart", self.restart_plugin, "restarted")

    def _stop_all(self) -> None:
        """stop all plugins."""
        failed_plugins: list[str] = []
        for name in list(self.plugins.keys()):
            try:
                self.logger.info("stopping plugin %s", name)
                self.stop_plugin(name)
            except Exception as exc:
                self.logger.warning("failed to stop plugin %s: %s", name, exc)
                failed_plugins.append(name)

        for name in failed_plugins:
            plugin = self.plugins.get(name)
            if plugin and plugin.thread and plugin.thread.is_alive():
                self.logger.warning("force marking plugin %s thread as stopped", name)
                plugin.thread = None


class Manager:
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: int = 5,
        webhook_url: str | None = None,
        ipc_port: int = 8765,
    ) -> None:
        self.log_queue, self.log_listener = setup_logging_queue()
        self.logger = get_logger(self.__class__.__name__, handler=None)

        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._webhook_url = webhook_url

        self.ipc_port = ipc_port
        self.ipc_thread: threading.Thread | None = None
        self._ipc_stop_event: threading.Event | None = threading.Event()

        self.metadata_by_name: dict[str, PluginMetadata] = {}
        self.role_by_name: dict[str, str] = {}
        self.workers: dict[str, PluginManager] = {}
        self.pipes: dict[str, PipeConnection] = {}
        self.mp_context = multiprocessing.get_context("spawn")

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
    ) -> PluginMetadata | None:
        self.logger.info("registering plugin %s", plugin.__name__)
        if not issubclass(plugin, Plugin):
            raise TypeError("Plugin must be a subclass of Plugin")

        requires_root = bool(getattr(plugin, "_requires_root", False))
        restart_on_failure = bool(getattr(plugin, "_restart_on_failure", False))

        if not force:
            if Config.run_script_only and plugin is not ScriptPlugin:
                self.logger.info("skipping plugin %s (not a script plugin in script-only env)", plugin.__name__)
                return None
            if Config.run_root_only and not requires_root:
                self.logger.info("skipping plugin %s (non-root plugin in root-only env)", plugin.__name__)
                return None
            if Config.run_non_root_only and requires_root:
                self.logger.info("skipping plugin %s (root plugin in non-root env)", plugin.__name__)
                return None

        metadata = self._build_metadata(plugin, args, kwargs, restart_on_failure)
        if metadata.name in self.metadata_by_name:
            raise DuplicatePluginError(f"Plugin {metadata.name} already registered")

        self.metadata_by_name[metadata.name] = metadata
        self.role_by_name[metadata.name] = "root" if metadata.requires_root else "non-root"
        self.logger.info("registered plugin %s", metadata.name)
        return metadata

    def _build_metadata(
        self,
        plugin: type[Plugin],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        restart_on_failure: bool,
    ) -> PluginMetadata:
        name = getattr(plugin, "name", plugin.__name__)
        module_path = plugin.__module__
        class_name = plugin.__name__
        requires_root = bool(getattr(plugin, "_requires_root", False))
        webhook_url = kwargs.get("webhook_url") or self._webhook_url or ""

        if plugin is ScriptPlugin:
            script_path = kwargs.get("script_path")
            requires_root = bool(kwargs.pop("requires_root", requires_root))
            if script_path and name == plugin.__name__:
                base_name = kwargs.get("name") or Path(str(script_path)).stem
                unique = uuid.uuid4().hex[:6]
                name = f"{plugin.__name__}_{base_name}_{unique}"

        return PluginMetadata(
            name=name,
            module_path=module_path,
            class_name=class_name,
            requires_root=requires_root,
            restart_on_failure=restart_on_failure,
            args=list(args),
            kwargs=dict(kwargs),
            webhook_url=webhook_url,
        )

    def _start_workers(self) -> None:
        if self.workers:
            return

        root_parent, root_child = self.mp_context.Pipe(duplex=True)
        non_root_parent, non_root_child = self.mp_context.Pipe(duplex=True)

        root_worker = PluginManager(
            role="root",
            pipe=root_child,
            log_queue=self.log_queue,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            webhook_url=self._webhook_url,
        )
        non_root_worker = PluginManager(
            role="non-root",
            pipe=non_root_child,
            log_queue=self.log_queue,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            webhook_url=self._webhook_url,
        )

        root_worker.start()
        non_root_worker.start()

        self.workers = {"root": root_worker, "non-root": non_root_worker}
        self.pipes = {"root": root_parent, "non-root": non_root_parent}

    def _load_metadata_to_workers(self) -> None:
        for metadata in self.metadata_by_name.values():
            role = "root" if metadata.requires_root else "non-root"
            request: PipeRequest = {
                "id": uuid.uuid4().hex,
                "cmd": PipeCommand.LOAD,
                "plugin_name": metadata.name,
                "metadata": metadata,
                "args": [],
                "kwargs": {},
                "force": False,
            }
            self._send_pipe_request(role, request)

    def _send_pipe_request(
        self,
        role: str,
        request: PipeRequest,
        timeout: float = 10.0,
        check_worker_alive: bool = False,
    ) -> PipeResponse:
        pipe = self.pipes[role]
        worker = self.workers.get(role) if check_worker_alive else None
        try:
            pipe.send(request)
            start_time = time.time()
            while time.time() - start_time < timeout:
                # early exit if worker died
                if worker and not worker.is_alive():
                    return {
                        "id": request["id"],
                        "status": "failed",
                        "message": "worker died",
                        "data": None,
                    }
                if pipe.poll(0.05):
                    response = pipe.recv()
                    if response["id"] == request["id"]:
                        return response
        except (ValueError, OSError, EOFError, BrokenPipeError) as exc:
            return {
                "id": request["id"],
                "status": "failed",
                "message": f"pipe communication failed: {exc}",
                "data": None,
            }
        return {
            "id": request["id"],
            "status": "failed",
            "message": "timeout",
            "data": None,
        }

    def start_plugin(self, plugin_name: str) -> PipeResponse:
        self.logger.info("starting plugin %s", plugin_name)
        metadata = self.metadata_by_name.get(plugin_name)
        if not metadata:
            return {"status": "failed", "message": "plugin not found", "data": None, "id": uuid.uuid4().hex}

        role = "root" if metadata.requires_root else "non-root"
        request: PipeRequest = {
            "id": uuid.uuid4().hex,
            "cmd": PipeCommand.START,
            "plugin_name": plugin_name,
            "metadata": metadata,
            "args": [],
            "kwargs": {},
            "force": False,
        }
        return self._send_pipe_request(role, request)

    def stop_plugin(self, plugin_name: str) -> PipeResponse:
        self.logger.info("stopping plugin %s", plugin_name)
        metadata = self.metadata_by_name.get(plugin_name)
        if not metadata:
            return {"status": "failed", "message": "plugin not found", "data": None, "id": uuid.uuid4().hex}
        role = "root" if metadata.requires_root else "non-root"
        request: PipeRequest = {
            "id": uuid.uuid4().hex,
            "cmd": PipeCommand.STOP,
            "plugin_name": plugin_name,
            "metadata": None,
            "args": [],
            "kwargs": {},
            "force": False,
        }
        return self._send_pipe_request(role, request)

    def restart_plugin(self, plugin_name: str) -> PipeResponse:
        self.logger.info("restarting plugin %s", plugin_name)
        metadata = self.metadata_by_name.get(plugin_name)
        if not metadata:
            return {"status": "failed", "message": "plugin not found", "data": None, "id": uuid.uuid4().hex}
        role = "root" if metadata.requires_root else "non-root"
        request: PipeRequest = {
            "id": uuid.uuid4().hex,
            "cmd": PipeCommand.RESTART,
            "plugin_name": plugin_name,
            "metadata": metadata,
            "args": [],
            "kwargs": {},
            "force": False,
        }
        return self._send_pipe_request(role, request)

    def list_plugins(self) -> list[str]:
        self.logger.info("listing plugins")
        results: list[str] = []
        for role in ["root", "non-root"]:
            if role not in self.pipes:
                continue
            request: PipeRequest = {
                "id": uuid.uuid4().hex,
                "cmd": PipeCommand.LIST,
                "plugin_name": None,
                "metadata": None,
                "args": [],
                "kwargs": {},
                "force": False,
            }
            response = self._send_pipe_request(role, request)
            if response["status"] == "ok" and isinstance(response["data"], list):
                results.extend(response["data"])
        return results

    def start(self) -> None:
        self.logger.info("starting manager")
        self._load_scripts()
        self._start_workers()
        self._load_metadata_to_workers()
        for plugin_name in self.metadata_by_name:
            self.start_plugin(plugin_name)

        if not Config.disable_ipc:
            self.start_ipc(self.ipc_port)

    def stop(self) -> None:
        self.logger.info("stopping manager")
        for role in list(self.pipes.keys()):
            worker = self.workers.get(role)
            if worker and not worker.is_alive():
                self.logger.debug("worker %s already dead, skipping shutdown request", role)
                continue

            request: PipeRequest = {
                "id": uuid.uuid4().hex,
                "cmd": PipeCommand.SHUTDOWN,
                "plugin_name": None,
                "metadata": None,
                "args": [],
                "kwargs": {},
                "force": False,
            }
            try:
                self._send_pipe_request(role, request, timeout=0.3, check_worker_alive=True)
            except Exception as exc:
                self.logger.warning("failed to send shutdown to %s: %s", role, exc)

        for name, worker in self.workers.items():
            if worker.is_alive():
                self.logger.debug("waiting for worker %s to exit", name)
                worker.join(timeout=5.0)
                if worker.is_alive():
                    self.logger.debug("worker %s did not exit in time, terminating", name)
                    worker.terminate()
                    worker.join(timeout=0.1)

        self.stop_ipc()
        self._stop_log_listener()

    def _stop_log_listener(self) -> None:
        self.logger.info("stopping log listener")
        try:
            self.log_listener.enqueue_sentinel()
            if self.log_listener._thread and self.log_listener._thread.is_alive():
                self.log_listener._thread.join(timeout=2.0)
                if self.log_listener._thread.is_alive():
                    self.logger.debug("log listener thread did not stop in time")
        except Exception as exc:
            self.logger.debug("log listener stop error: %s", exc)
        finally:
            try:
                self.log_queue.cancel_join_thread()
                self.log_queue.close()
            except Exception as exc:
                self.logger.debug("log queue close error: %s", exc)

    def _load_scripts(self) -> None:
        scripts_dir = DIR.parent / "scripts"
        if not scripts_dir.exists():
            return

        self.logger.info("scanning for scripts in %s", scripts_dir)
        for item in scripts_dir.iterdir():
            if not item.is_file() or item.name.startswith("."):
                continue

            is_executable = item.suffix in (".py", ".sh") or os.access(item, os.X_OK)
            if not is_executable:
                continue

            self.logger.info("found script: %s", item.name)
            requires_root = item.stem.startswith("root")
            try:
                if item.suffix == ".py":
                    self.register_plugin(
                        ScriptPlugin,
                        script_path="python",
                        args=[str(item.resolve())],
                        name=item.stem,
                        cwd=str(item.parent),
                        use_screen=Config.scripts_use_screen,
                        requires_root=requires_root,
                        force=True,
                    )
                else:
                    self.register_plugin(
                        ScriptPlugin,
                        script_path=str(item),
                        cwd=str(item.parent),
                        use_screen=Config.scripts_use_screen,
                        requires_root=requires_root,
                        force=True,
                    )
            except Exception as exc:
                self.logger.error("failed to register script %s: %s", item.name, exc)

    def _handle_ipc_request(self, request: IPCRequest) -> IPCResponse:
        cmd_value = request.get("cmd", "")
        plugin_name = request.get("plugin_name", "")
        try:
            cmd = IPCCommand(cmd_value)
        except ValueError:
            return {"status": "failed", "message": "unsupported command", "data": None}

        if cmd == IPCCommand.LIST:
            data = json.dumps(self.list_plugins())
            return {"status": "ok", "message": "ok", "data": data}

        if not plugin_name:
            return {"status": "failed", "message": "plugin_name is required", "data": None}

        metadata = self.metadata_by_name.get(plugin_name)
        if not metadata:
            return {"status": "failed", "message": "plugin not found", "data": None}

        if cmd == IPCCommand.START:
            response = self.start_plugin(plugin_name)
        elif cmd == IPCCommand.STOP:
            response = self.stop_plugin(plugin_name)
        elif cmd == IPCCommand.RESTART:
            response = self.restart_plugin(plugin_name)
        else:
            response = {"status": "failed", "message": "unsupported command", "data": None, "id": uuid.uuid4().hex}

        status = str(response.get("status") or "failed")
        message = str(response.get("message") or "")
        data_value = response.get("data")
        data = None if data_value is None else json.dumps(data_value)
        return {
            "status": status,
            "message": message,
            "data": data,
        }

    def _handle_ipc_command(self, raw: str) -> Mapping[str, Any]:
        try:
            request: IPCRequest = json.loads(raw)
            return self._handle_ipc_request(request)
        except json.JSONDecodeError as exc:
            return {
                "status": "failed",
                "message": f"invalid JSON: {exc}",
                "data": traceback.format_exc() if Config.debug else None,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "message": str(exc),
                "data": traceback.format_exc() if Config.debug else None,
            }

    def _ipc_worker(self, tcp_port: int = 8765) -> None:
        assert self._ipc_stop_event is not None

        port = tcp_port or (self.ipc_port or 8765)
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", port))
        server_socket.listen(1)
        server_socket.settimeout(1.0)
        self.logger.info("ipc tcp server listening on 127.0.0.1:%d", port)
        try:
            while not self._ipc_stop_event.is_set():
                try:
                    conn, addr = server_socket.accept()
                except TimeoutError:
                    continue

                with conn:
                    try:
                        data = recv_str(conn)
                        response = self._handle_ipc_command(data)
                        send_json(conn, response)
                    except TimeoutError:
                        self.logger.warning("ipc client %s timed out, closing connection", addr)
                    except ConnectionError as exc:
                        self.logger.debug("ipc client %s disconnected: %s", addr, exc)
        finally:
            server_socket.close()

    def start_ipc(self, tcp_port: int = 8765) -> None:
        self.logger.info("starting ipc listener on port %s", tcp_port)
        if self.ipc_thread and self.ipc_thread.is_alive():
            self.logger.info("ipc already running")
            return

        if self._ipc_stop_event is None:
            self._ipc_stop_event = threading.Event()

        self.ipc_port = tcp_port
        thread = threading.Thread(
            target=self._ipc_worker,
            args=(self.ipc_port,),
            daemon=True,
            name="IPC-Listener",
        )
        thread.start()
        self.ipc_thread = thread
        self.logger.info("ipc listener started")

    def stop_ipc(self) -> None:
        self.logger.info("stopping ipc listener")
        if not self.ipc_thread:
            return
        if not self._ipc_stop_event:
            return
        self._ipc_stop_event.set()
        self.ipc_thread.join(timeout=2.0)
        self.ipc_thread = None
        self._ipc_stop_event = None
        self.logger.info("ipc listener stopped")

    @log_function_call
    def run(self) -> None:
        self.logger.info("starting run loop")
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("ctrl+c detected, exiting")
        except Exception as exc:
            self.logger.error("manager error: %s", exc)
        finally:
            self.logger.info("stopping run loop")
            self.stop()
