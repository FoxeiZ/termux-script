from __future__ import annotations

import asyncio
import contextlib
import importlib
import multiprocessing
import os
import subprocess
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import IS_TERMUX, IS_WINDOWS, Config
from .plugin import Plugin
from .types import PipeCommand, PipeRequest, PipeResponse
from .utils import configure_queue_logging, get_logger

if TYPE_CHECKING:
    import logging
    from logging import LogRecord
    from multiprocessing.connection import PipeConnection
    from multiprocessing.queues import Queue

    from .types import PluginMetadata


class PluginManager(multiprocessing.Process):
    def __init__(
        self,
        role: str,
        pipe: PipeConnection,
        log_queue: Queue[LogRecord],
        max_retries: int,
        retry_delay: int,
        webhook_url: str | None,
    ) -> None:
        super().__init__(name=f"PluginManager-{role}")
        self.role: str = role
        if role not in ("root", "non-root"):
            raise ValueError("role must be 'root' or 'non-root'")
        self.pipe: PipeConnection[Any, Any] = pipe
        self.log_queue: Queue[LogRecord] = log_queue
        self.max_retries: int = max_retries
        self.retry_delay: int = retry_delay
        self.webhook_url: str = webhook_url or ""
        self.plugins: dict[str, Plugin] = {}
        self.metadata_by_name: dict[str, PluginMetadata] = {}
        self.plugin_tasks: dict[str, asyncio.Task[None]] = {}
        self.logger: logging.Logger = get_logger(f"plugin_manager.{role}", handler=None)

    def run(self) -> None:
        configure_queue_logging(self.log_queue)
        self._drop_privileges_if_needed()
        self.logger.info("starting plugin manager process")
        try:
            asyncio.run(self._run_loop())
        except KeyboardInterrupt:
            self.logger.info("plugin manager process interrupted")
        finally:
            self._cleanup()

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()

        if not IS_WINDOWS:
            # epoll/kqueue via fd is more efficient than polling in a thread
            # windows does not have this capability (eww)
            pipe_event = asyncio.Event()

            def _on_pipe_readable() -> None:
                pipe_event.set()

            loop.add_reader(self.pipe.fileno(), _on_pipe_readable)

            try:
                while True:
                    await pipe_event.wait()
                    pipe_event.clear()

                    while self.pipe.poll():
                        request = self.pipe.recv()
                        should_exit = await self._process_request(request)
                        if should_exit:
                            return
            finally:
                loop.remove_reader(self.pipe.fileno())
        else:
            while True:
                if not await asyncio.to_thread(self.pipe.poll, 0.1):
                    await asyncio.sleep(0)
                    continue
                request = await asyncio.to_thread(self.pipe.recv)
                should_exit = await self._process_request(request)
                if should_exit:
                    return

    async def _process_request(self, request: PipeRequest) -> bool:
        request_id = request.get("id", uuid.uuid4().hex)
        try:
            response = await self._handle_request(request)

            if IS_WINDOWS:
                await asyncio.to_thread(self.pipe.send, response)
            else:
                self.pipe.send(response)

            if request.get("cmd") == PipeCommand.SHUTDOWN:
                self.logger.info("shutdown complete, exiting plugin manager process")
                return True

        except EOFError:
            self.logger.info("pipe closed, exiting")
            return True
        except Exception as exc:
            error_response: PipeResponse = {
                "id": request_id,
                "status": "failed",
                "message": str(exc),
                "data": traceback.format_exc() if Config.debug else None,
            }
            try:
                if IS_WINDOWS:
                    await asyncio.to_thread(self.pipe.send, error_response)
                else:
                    self.pipe.send(error_response)
            except Exception:
                self.logger.warning("pipe send failed, exiting")
                return True
        return False

    def _cleanup(self) -> None:
        self.logger.debug("cleaning up %s plugin manager resources", self.role)
        try:
            self.pipe.close()
        except Exception as exc:
            self.logger.warning("failed to close pipe: %s", exc)

        try:
            self.log_queue.cancel_join_thread()
        except Exception as exc:
            self.logger.warning("failed to cancel log queue join thread: %s", exc)

    def _drop_privileges_if_needed(self) -> None:
        if IS_WINDOWS:
            return

        if os.getuid() != 0 or os.geteuid() != 0:
            self.logger.info("not running as root, no need to drop privileges")
            return

        if self.role != "non-root":
            return

        if not IS_TERMUX:
            return

        self.logger.info("dropping root privileges for non-root")
        try:
            process = subprocess.run(["dumpsys", "package", "com.termux"], check=True, capture_output=True)
            for line in process.stdout.decode().splitlines():
                s_line = line.strip()
                if s_line.startswith("userId="):
                    uid_str = s_line.split("=", 1)[1].strip()
                    uid = int(uid_str)
                    os.setgroups([9997, 3003, uid])
                    os.setgid(uid)
                    os.setuid(uid)
                    self._fix_suroot_env_vars()
                    self.logger.info("dropped privileges to uid/gid %d", uid)
                    return
        except Exception as exc:
            self.logger.error("failed to drop privileges: %s", exc)

    def _fix_suroot_env_vars(self) -> None:
        env = os.environ.copy()
        for key, value in list(env.items()):
            if ".suroot" not in value:
                continue

            home_path = Path(value)
            parts = home_path.parts
            try:
                suroot_index = parts.index(".suroot")
            except ValueError:
                continue
            new_home = Path(*parts[:suroot_index], *parts[suroot_index + 1 :])
            new_value = str(new_home)
            os.environ[key] = new_value
            self.logger.debug("fixed env var %s: %s -> %s", key, value, new_value)

    async def _handle_request(self, request: PipeRequest) -> PipeResponse:
        cmd = request["cmd"]
        request_id = request["id"]

        if cmd == PipeCommand.LOAD:
            metadata = request.get("metadata")
            if not metadata:
                return self._response(request_id, "failed", "metadata is required", None)
            return self._pipe_load_plugin(metadata, request_id)

        if cmd == PipeCommand.START:
            return await self._pipe_start_plugin(request)

        if cmd == PipeCommand.STOP:
            return await self._pipe_stop_plugin(request)

        if cmd == PipeCommand.RESTART:
            return await self._pipe_restart_plugin(request)

        if cmd == PipeCommand.LIST:
            return self._response(request_id, "ok", "ok", list(self.metadata_by_name.keys()))

        if cmd == PipeCommand.SHUTDOWN:
            await self._stop_all()
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

    async def _run_plugin(self, plugin_name: str) -> None:
        plugin = self.plugins[plugin_name]
        try:
            await plugin._start()
        except asyncio.CancelledError:
            self.logger.info("plugin %s task was cancelled", plugin_name)
        except Exception as exc:
            self.logger.error("plugin %s runtime error: %s", plugin_name, exc)
            self.logger.exception(exc)

    async def start_plugin(self, plugin_name: str, metadata: PluginMetadata | None = None) -> None:
        self.logger.info("starting plugin %s", plugin_name)
        plugin = self.plugins.get(plugin_name)
        if plugin is None and metadata:
            self.load_plugin(metadata)
            plugin = self.plugins.get(plugin_name)

        if plugin is None:
            self.logger.error("plugin %s not loaded", plugin_name)
            raise ValueError("plugin not loaded")

        existing_task = self.plugin_tasks.get(plugin_name)
        if existing_task and not existing_task.done():
            self.logger.debug("plugin %s already running, skipping", plugin_name)
            return

        task = asyncio.create_task(self._run_plugin(plugin_name), name=f"Plugin-{plugin.name}")
        self.plugin_tasks[plugin_name] = task
        plugin.task = task
        self.logger.info("plugin %s started", plugin_name)

    async def stop_plugin(self, plugin_name: str) -> None:
        self.logger.info("stopping plugin %s", plugin_name)
        plugin = self.plugins.get(plugin_name)
        if not plugin:
            self.logger.error("plugin %s not loaded", plugin_name)
            raise ValueError("plugin not loaded")

        task = self.plugin_tasks.get(plugin_name)
        if task is None or task.done():
            self.logger.debug("plugin %s has no running task, nothing to stop", plugin_name)
            plugin.task = None
            return

        plugin.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            self.logger.warning("plugin %s did not stop gracefully, forcing stop", plugin_name)
            plugin.force_stop()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1.0)
            except TimeoutError as exc:
                self.logger.error("plugin %s failed to stop after force stop", plugin_name)
                raise RuntimeError("failed to stop") from exc

        self.plugin_tasks.pop(plugin_name, None)
        plugin.task = None
        self.logger.info("plugin %s stopped", plugin_name)

    async def restart_plugin(self, plugin_name: str, metadata: PluginMetadata | None = None) -> None:
        self.logger.info("restarting plugin %s", plugin_name)
        await self.stop_plugin(plugin_name)
        await self.start_plugin(plugin_name, metadata)
        self.logger.info("plugin %s restarted", plugin_name)

    def _pipe_load_plugin(self, metadata: PluginMetadata, request_id: str) -> PipeResponse:
        try:
            self.load_plugin(metadata)
            return self._response(request_id, "ok", "loaded", None)
        except Exception as exc:
            self.logger.error("pipe_load_plugin failed for %s: %s", metadata.name, exc)
            return self._response(request_id, "failed", str(exc), traceback.format_exc() if Config.debug else None)

    async def _pipe_plugin_action(
        self,
        request: PipeRequest,
        action: str,
        handler: Any,
        success_message: str,
    ) -> PipeResponse:
        request_id = request["id"]
        plugin_name = request.get("plugin_name")
        if not plugin_name:
            self.logger.error("pipe_%s_plugin called without plugin_name", action)
            return self._response(request_id, "failed", "plugin_name is required", None)

        try:
            if action in ("start", "restart"):
                await handler(plugin_name, request.get("metadata"))
            else:
                await handler(plugin_name)
            return self._response(request_id, "ok", success_message, None)
        except Exception as exc:
            self.logger.error("pipe_%s_plugin failed for %s: %s", action, plugin_name, exc)
            return self._response(request_id, "failed", str(exc), traceback.format_exc() if Config.debug else None)

    async def _pipe_start_plugin(self, request: PipeRequest) -> PipeResponse:
        return await self._pipe_plugin_action(request, "start", self.start_plugin, "started")

    async def _pipe_stop_plugin(self, request: PipeRequest) -> PipeResponse:
        return await self._pipe_plugin_action(request, "stop", self.stop_plugin, "stopped")

    async def _pipe_restart_plugin(self, request: PipeRequest) -> PipeResponse:
        return await self._pipe_plugin_action(request, "restart", self.restart_plugin, "restarted")

    async def _stop_all(self) -> None:
        if not self.plugins:
            return

        self.logger.info("broadcasting shutdown to all plugins")

        stop_tasks = [self.stop_plugin(name) for name in self.plugins]
        results = await asyncio.gather(*stop_tasks, return_exceptions=True)

        failed_plugins: list[str] = []
        for name, result in zip(self.plugins.keys(), results, strict=True):
            if isinstance(result, Exception):
                self.logger.warning("failed to stop plugin %s: %s", name, result)
                failed_plugins.append(name)

        for name in failed_plugins:
            plugin = self.plugins.get(name)
            task = self.plugin_tasks.get(name)
            if plugin and task and not task.done():
                self.logger.warning("force cancelling rogue plugin %s task", name)
                task.cancel()
                plugin.task = None
