from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import socket
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Config
from .errors import DuplicatePluginError
from .ipc import recv_str, send_json
from .ipc_types import IPCCommand, IPCRequest, IPCResponse, PipeCommand, PipeRequest, PipeResponse
from .plugin import Plugin
from .plugin.metadata import PluginMetadata
from .plugin.script import ScriptPlugin
from .utils import configure_queue_logging, get_logger, log_function_call, setup_logging_queue

__all__ = ["Manager", "PluginManager"]

DIR = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"


if TYPE_CHECKING:
    import logging
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
        self.pipe = pipe
        self.log_queue = log_queue
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.webhook_url = webhook_url or ""
        self.plugins: dict[str, Plugin] = {}
        self.metadata_by_name: dict[str, PluginMetadata] = {}
        self.logger: logging.Logger | None = None

    def run(self) -> None:
        self._drop_privileges_if_needed()
        configure_queue_logging(self.log_queue)
        self.logger = get_logger(f"plugin_manager.{self.role}", handler=None)
        self.logger.info("starting plugin manager process")
        request_id = ""
        while True:
            try:
                if not self.pipe.poll(0.5):
                    continue

                request = self.pipe.recv()
                request_id = request["id"]
                response = self._handle_request(request)
                self.pipe.send(response)

                if request["cmd"] == PipeCommand.SHUTDOWN:
                    self.logger.info("shutdown requested")
                    self._stop_all()
                    self.logger.info("exiting plugin manager process")
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

    def _drop_privileges_if_needed(self) -> None:
        if IS_WINDOWS:
            return
        if self.role != "non-root":
            return
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if not sudo_uid or not sudo_gid:
            return
        if not hasattr(os, "setgid") or not hasattr(os, "setuid"):
            return
        os.setgid(int(sudo_gid))
        os.setuid(int(sudo_uid))

    def _handle_request(self, request: PipeRequest) -> PipeResponse:
        cmd = request["cmd"]
        request_id = request["id"]

        if cmd == PipeCommand.LOAD:
            metadata = request.get("metadata")
            if not metadata:
                return self._response(request_id, "failed", "metadata is required", None)
            return self._load_plugin(metadata, request_id)

        if cmd == PipeCommand.START:
            return self._start_plugin(request)

        if cmd == PipeCommand.STOP:
            return self._stop_plugin(request)

        if cmd == PipeCommand.RESTART:
            stop_result = self._stop_plugin(request)
            if stop_result["status"] != "ok":
                return stop_result
            return self._start_plugin(request)

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

    def _load_plugin(self, metadata: PluginMetadata, request_id: str) -> PipeResponse:
        if metadata.name in self.plugins:
            return self._response(request_id, "ok", "already loaded", None)

        try:
            module = importlib.import_module(metadata.module_path)
        except Exception as exc:
            return self._response(request_id, "failed", f"import failed: {exc}", traceback.format_exc())

        plugin_cls = getattr(module, metadata.class_name, None)
        if not plugin_cls or not issubclass(plugin_cls, Plugin):
            return self._response(request_id, "failed", "invalid plugin class", None)

        metadata = PluginMetadata(
            name=metadata.name,
            module_path=metadata.module_path,
            class_name=metadata.class_name,
            requires_root=metadata.requires_root,
            restart_on_failure=metadata.restart_on_failure,
            args=list(metadata.args),
            kwargs=dict(metadata.kwargs),
            webhook_url=metadata.webhook_url,
        )

        logger = get_logger(f"plugin.{metadata.name}", handler=None)
        try:
            plugin = plugin_cls(manager=self, metadata=metadata, logger=logger)
        except Exception as exc:
            return self._response(request_id, "failed", f"init failed: {exc}", traceback.format_exc())

        self.plugins[metadata.name] = plugin
        self.metadata_by_name[metadata.name] = metadata
        return self._response(request_id, "ok", "loaded", None)

    def _start_plugin(self, request: PipeRequest) -> PipeResponse:
        request_id = request["id"]
        plugin_name = request.get("plugin_name")
        if not plugin_name:
            return self._response(request_id, "failed", "plugin_name is required", None)

        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            metadata = request.get("metadata")
            if metadata:
                load_result = self._load_plugin(metadata, request_id)
                if load_result["status"] != "ok":
                    return load_result
                plugin = self.plugins.get(plugin_name)

        if plugin is None:
            return self._response(request_id, "failed", "plugin not loaded", None)

        if plugin.thread and plugin.thread.is_alive():
            return self._response(request_id, "ok", "already running", None)

        thread = threading.Thread(target=plugin._start, daemon=False, name=f"Plugin-{plugin.name}")
        thread.start()
        plugin.thread = thread
        return self._response(request_id, "ok", "started", None)

    def _stop_plugin(self, request: PipeRequest) -> PipeResponse:
        request_id = request["id"]
        plugin_name = request.get("plugin_name")
        if not plugin_name:
            return self._response(request_id, "failed", "plugin_name is required", None)

        plugin = self.plugins.get(plugin_name)
        if not plugin:
            return self._response(request_id, "failed", "plugin not loaded", None)

        if not plugin.thread:
            return self._response(request_id, "ok", "not running", None)

        plugin.stop()
        plugin.thread.join(timeout=2.0)
        if plugin.thread.is_alive():
            plugin.force_stop()
            plugin.thread.join(timeout=2.0)

        if plugin.thread.is_alive():
            return self._response(request_id, "failed", "failed to stop", None)

        plugin.thread = None
        return self._response(request_id, "ok", "stopped", None)

    def _stop_all(self) -> None:
        for name in list(self.plugins.keys()):
            request: PipeRequest = {
                "id": uuid.uuid4().hex,
                "cmd": PipeCommand.STOP,
                "plugin_name": name,
                "metadata": None,
                "args": [],
                "kwargs": {},
                "force": False,
            }
            self._stop_plugin(request)


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
            if script_path:
                stem = Path(str(script_path)).stem
                unique = uuid.uuid4().hex[:6]
                name = f"{plugin.__name__}_{stem}_{unique}"

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
        self._start_workers()
        self._load_metadata_to_workers()
        self._load_scripts()
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
            # enqueue sentinel to unblock the listener thread
            self.log_listener.enqueue_sentinel()
            # wait for monitor thread to finish with timeout
            if hasattr(self.log_listener, "_thread") and self.log_listener._thread:
                self.log_listener._thread.join(timeout=0.2)
        except Exception as exc:
            self.logger.debug("log listener stop error (expected on abrupt shutdown): %s", exc)
        finally:
            try:
                self.log_queue.close()
                self.log_queue.join_thread()
            except Exception as exc:
                self.logger.debug("log queue close error: %s", exc)

    def _load_scripts(self) -> None:
        scripts_dir = DIR.parent / "scripts"
        if not scripts_dir.exists():
            return

        self.logger.info("scanning for scripts in %s", scripts_dir)
        for item in scripts_dir.iterdir():
            if not item.is_file():
                continue

            is_executable = item.suffix in (".py", ".sh") or os.access(item, os.X_OK)
            if not is_executable:
                continue

            self.logger.info("found script: %s", item.name)
            try:
                if item.suffix == ".py":
                    self.register_plugin(
                        ScriptPlugin,
                        script_path="python",
                        args=[str(item)],
                        cwd=str(item.parent),
                        use_screen=Config.scripts_use_screen,
                        force=True,
                    )
                else:
                    self.register_plugin(
                        ScriptPlugin,
                        script_path=str(item),
                        cwd=str(item.parent),
                        use_screen=Config.scripts_use_screen,
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

    def _handle_ipc_command(self, raw: str) -> str:
        try:
            request: IPCRequest = json.loads(raw)
            response = self._handle_ipc_request(request)
            return json.dumps(response)
        except json.JSONDecodeError as exc:
            response = {
                "status": "failed",
                "message": f"invalid JSON: {exc}",
                "data": traceback.format_exc() if Config.debug else None,
            }
            return json.dumps(response)
        except Exception as exc:
            response = {
                "status": "failed",
                "message": str(exc),
                "data": traceback.format_exc() if Config.debug else None,
            }
            return json.dumps(response)

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
                    conn, _ = server_socket.accept()
                except TimeoutError:
                    continue

                with conn:
                    data = recv_str(conn)
                    response = self._handle_ipc_command(data)
                    send_json(conn, response)
        finally:
            server_socket.close()

    def start_ipc(self, tcp_port: int = 8765) -> None:
        self.logger.info("starting ipc listener on port %s", tcp_port)
        if self.ipc_thread and self.ipc_thread.is_alive():
            self.logger.info("ipc already running")
            return

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
