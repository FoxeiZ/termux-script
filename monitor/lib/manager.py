from __future__ import annotations

import asyncio
import contextlib
import json
import multiprocessing
import os
import secrets
import signal
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DIR, IS_TERMUX, Config
from .errors import DuplicatePluginError
from .ipc import IPCServer
from .plugin import Plugin
from .plugin.script import ScriptPlugin
from .types import IPCCommand, IPCCommandInternal, PipeCommand, PluginMetadata
from .utils import get_logger, log_function_call, setup_logging_queue
from .worker import PluginManager

__all__ = ["IS_TERMUX", "Manager"]

if TYPE_CHECKING:
    from collections.abc import Mapping
    from multiprocessing.connection import PipeConnection

    from .types import IPCRequest, IPCRequestInternal, IPCRequestManager, IPCResponse, PipeRequest, PipeResponse


class ManagerStateContext:
    class State:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            self.plugins_loaded = False
            self.workers_started = False
            self.ipc_started = False
            self.has_internet_access = None
            self.last_error = None

    def __init__(self) -> None:
        self.__state = self.State()
        self.lock = asyncio.Lock()

    async def __aenter__(self) -> State:
        await self.lock.acquire()
        return self.__state

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self.lock.locked():
            self.lock.release()


class Manager:
    __password__: str = secrets.token_hex(16)

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: int = 5,
        webhook_url: str | None = None,
        ipc_port: int = 8765,
    ) -> None:
        self.log_queue, self.log_listener = setup_logging_queue()
        self.logger = get_logger(self.__class__.__name__, handler=None)
        self.ctx_state = ManagerStateContext()

        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._webhook_url = webhook_url

        self.ipc_port = ipc_port
        self.ipc_server: IPCServer | None = None

        self.metadata_by_name: dict[str, PluginMetadata] = {}
        self.role_by_name: dict[str, str] = {}
        self.workers: dict[str, PluginManager] = {}
        self.pipes: dict[str, PipeConnection] = {}
        self._pipe_locks: dict[str, asyncio.Lock] = {}
        self._pending_pipe_responses: dict[str, dict[str, PipeResponse]] = {}
        self._shutdown_event = asyncio.Event()

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

    async def _start_workers(self) -> None:
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
            ipc_password=self.__password__,
            ipc_port=self.ipc_port,
        )
        non_root_worker = PluginManager(
            role="non-root",
            pipe=non_root_child,
            log_queue=self.log_queue,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            webhook_url=self._webhook_url,
            ipc_password=self.__password__,
            ipc_port=self.ipc_port,
        )

        root_worker.start()
        non_root_worker.start()

        self.workers = {"root": root_worker, "non-root": non_root_worker}
        self.pipes = {"root": root_parent, "non-root": non_root_parent}
        self._pending_pipe_responses = {"root": {}, "non-root": {}}
        async with self.ctx_state as state:
            state.workers_started = True

    def _pipe_lock(self, role: str) -> asyncio.Lock:
        lock = self._pipe_locks.get(role)
        if lock is None:
            lock = asyncio.Lock()
            self._pipe_locks[role] = lock
        return lock

    async def _load_metadata_to_workers(self) -> None:
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
            response = await self._send_pipe_request(role, request)
            if response["status"] != "ok":
                self.logger.warning(
                    "failed to load plugin %s in %s worker: %s",
                    metadata.name,
                    role,
                    response.get("message", "unknown error"),
                )

    async def _send_pipe_request(
        self,
        role: str,
        request: PipeRequest,
        timeout: float = 10.0,
        check_worker_alive: bool = False,
    ) -> PipeResponse:
        pipe = self.pipes[role]
        lock = self._pipe_lock(role)
        pending_responses = self._pending_pipe_responses.setdefault(role, {})
        worker = self.workers.get(role) if check_worker_alive else None

        async with lock:
            try:
                cached_response = pending_responses.pop(request["id"], None)
                if cached_response is not None:
                    return cached_response

                await asyncio.to_thread(pipe.send, request)
                start_time = asyncio.get_running_loop().time()
                while asyncio.get_running_loop().time() - start_time < timeout:
                    if worker and not worker.is_alive():
                        return {
                            "id": request["id"],
                            "status": "failed",
                            "message": "worker died",
                            "data": None,
                        }

                    if await asyncio.to_thread(pipe.poll, 0.05):
                        response = await asyncio.to_thread(pipe.recv)
                        response_id = response.get("id")
                        if response_id == request["id"]:
                            return response
                        if isinstance(response_id, str):
                            pending_responses[response_id] = response
                        else:
                            self.logger.debug("dropping malformed pipe response without id: %s", response)

                    await asyncio.sleep(0)
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

    async def start_plugin(self, plugin_name: str) -> PipeResponse:
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
        return await self._send_pipe_request(role, request)

    async def stop_plugin(self, plugin_name: str) -> PipeResponse:
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
        return await self._send_pipe_request(role, request)

    async def restart_plugin(self, plugin_name: str) -> PipeResponse:
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
        return await self._send_pipe_request(role, request)

    async def list_plugins(self) -> list[str]:
        self.logger.info("listing plugins")

        async def fetch_for_role(role: str) -> list[str]:
            if role not in self.pipes:
                return []
            request: PipeRequest = {
                "id": uuid.uuid4().hex,
                "cmd": PipeCommand.LIST,
                "plugin_name": None,
                "metadata": None,
                "args": [],
                "kwargs": {},
                "force": False,
            }
            response = await self._send_pipe_request(role, request)
            if response["status"] == "ok" and isinstance(response["data"], list):
                return response["data"]
            return []

        results = await asyncio.gather(fetch_for_role("root"), fetch_for_role("non-root"))
        return [item for sublist in results for item in sublist]

    async def start(self) -> None:
        self.logger.info("starting manager")
        self._load_scripts()
        await self._start_workers()
        await self._load_metadata_to_workers()

        start_tasks = [self.start_plugin(name) for name in self.metadata_by_name]
        responses = await asyncio.gather(*start_tasks)

        for name, response in zip(self.metadata_by_name.keys(), responses, strict=True):
            if response["status"] != "ok":
                self.logger.warning(
                    "failed to start plugin %s: %s",
                    name,
                    response.get("message", "unknown error"),
                )
        async with self.ctx_state as state:
            state.plugins_loaded = True

        await self.start_ipc(self.ipc_port)

    async def stop(self) -> None:
        async with self.ctx_state as state:
            if state.stopped:
                self.logger.info("manager already stopped")
                return
            state.stopped = True

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
                await self._send_pipe_request(role, request, timeout=0.3, check_worker_alive=True)
            except Exception as exc:
                self.logger.warning("failed to send shutdown to %s: %s", role, exc)

        alive_workers = {name: worker for name, worker in self.workers.items() if worker.is_alive()}
        if alive_workers:
            self.logger.debug("waiting for workers to exit: %s", list(alive_workers.keys()))
            deadline = asyncio.get_running_loop().time() + 5.0
            while alive_workers and asyncio.get_running_loop().time() < deadline:
                for name, worker in list(alive_workers.items()):
                    await asyncio.to_thread(worker.join, 0.1)
                    if not worker.is_alive():
                        self.logger.debug("worker %s exited", name)
                        del alive_workers[name]

            for name, worker in alive_workers.items():
                self.logger.debug("worker %s did not exit in time, terminating", name)
                worker.terminate()
                await asyncio.to_thread(worker.join, 0.5)

        await self.stop_ipc()

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

    async def _handle_ipc_request(self, request: IPCRequestManager) -> IPCResponse:
        cmd_value = request.get("cmd", "")
        plugin_name = request.get("plugin_name", "")
        try:
            cmd = IPCCommand(cmd_value)
        except ValueError:
            return {"status": "failed", "message": "unsupported command", "data": None}

        if cmd == IPCCommand.LIST:
            return {"status": "ok", "message": "ok", "data": await self.list_plugins()}

        if not plugin_name:
            return {"status": "failed", "message": "plugin_name is required", "data": None}

        metadata = self.metadata_by_name.get(plugin_name)
        if not metadata:
            return {"status": "failed", "message": "plugin not found", "data": None}

        if cmd == IPCCommand.START:
            response = await self.start_plugin(plugin_name)
        elif cmd == IPCCommand.STOP:
            response = await self.stop_plugin(plugin_name)
        elif cmd == IPCCommand.RESTART:
            response = await self.restart_plugin(plugin_name)
        else:
            response = {"status": "failed", "message": "unsupported command", "data": None, "id": uuid.uuid4().hex}

        status = str(response.get("status") or "failed")
        message = str(response.get("message") or "")
        data = response.get("data")
        return {
            "status": status,
            "message": message,
            "data": data,
        }

    async def _handle_internal_ipc_command(self, request: IPCRequestInternal) -> IPCResponse:
        internal_cmd = request.get("internal_cmd", "")
        try:
            cmd = IPCCommandInternal(internal_cmd)
        except ValueError:
            return {"status": "failed", "message": "unsupported command", "data": None}

        match cmd:
            case IPCCommandInternal.REBOOT:
                is_test = "test" in request.get("args", [])

                async def _stop():
                    # call `stop` manager before triggering shutdown event to ensure logs are processed before reboot
                    await self.stop()
                    self._shutdown_event.set()

                def shutdown(*args: Any) -> None:
                    import subprocess  # noqa: PLC0415

                    if IS_TERMUX and not is_test:
                        subprocess.run(["sudo", "reboot"], check=False)
                    else:
                        print("reboot command received")

                asyncio.create_task(_stop()).add_done_callback(shutdown)
                return {"status": "ok", "message": "shutting down", "data": None}

            case IPCCommandInternal.UPDATE_STATE:
                state_datas: list[dict[str, Any]] = request.get("args", [{}])
                for state_data in state_datas:
                    for key, value in state_data.items():
                        async with self.ctx_state as state:
                            if hasattr(state, key):
                                setattr(state, key, value)
                return {"status": "ok", "message": "state updated", "data": None}

            case _:
                return {"status": "failed", "message": "unsupported internal command", "data": None}

    async def _handle_ipc_command(self, raw: str) -> Mapping[str, Any]:
        try:
            request: IPCRequest = json.loads(raw)
            if request.get("cmd") == IPCCommand.INTERNAL.value:
                return await self._handle_internal_ipc_command(request)  # type: ignore
            elif request.get("plugin_name"):
                return await self._handle_ipc_request(request)  # type: ignore
            else:
                return {"status": "failed", "message": "invalid request format", "data": None}

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

    async def start_ipc(self, tcp_port: int = 8765) -> None:
        self.logger.info("starting ipc listener on port %s", tcp_port)
        if self.ipc_server is not None:
            self.logger.info("ipc already running")
            return

        self.ipc_port = tcp_port
        self.ipc_server = IPCServer(
            host="127.0.0.1",
            port=self.ipc_port,
            on_message_received=self._handle_ipc_command,
            logger=self.logger,
        )
        await self.ipc_server.start()
        async with self.ctx_state as state:
            state.ipc_started = True

    async def stop_ipc(self) -> None:
        self.logger.info("stopping ipc listener")
        if self.ipc_server is None:
            return
        await self.ipc_server.stop()
        self.ipc_server = None

    @log_function_call
    async def run(self) -> None:
        self.logger.info("starting run loop")
        await self.start()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            self.logger.info("shutdown signal received")
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _signal_handler)

        try:
            # if IS_WINDOWS:
            #     while not shutdown_event.is_set():
            #         with contextlib.suppress(TimeoutError):
            #             await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
            # else:
            async with self.ctx_state as state:
                state.started = True
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            self.logger.info("manager run task cancelled")
        except Exception as exc:
            self.logger.error("manager error: %s", exc)
        finally:
            self.logger.info("stopping run loop")
            await self.stop()
            self._stop_log_listener()
