from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import psutil
from lib.errors import PluginError
from lib.ipc import send_json
from lib.plugin import IntervalPlugin
from lib.types import IPCCommand, IPCCommandInternal, IPCRequestInternal
from lib.utils import log_function_call

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class SystemServerPlugin(IntervalPlugin, requires_root=True):
    interval = 10

    if TYPE_CHECKING:
        cpu_threshold: int
        threshold_count_max: int
        _threshold_count: int
        _cpu_tracker_proc: psutil.Process | None

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ):
        try:
            os.lstat("/proc/stat")
        except PermissionError as e:
            raise PluginError("Permission denied to access /proc/stat. Please run as root.") from e

        super().__init__(manager, metadata, logger)
        self.cpu_threshold = int(metadata.kwargs.get("cpu_threshold", 100))
        self.threshold_count_max = int(metadata.kwargs.get("threshold_count_max", 3))

        self._threshold_count = 0
        self._cpu_tracker_proc = None

    def _find_process(self) -> psutil.Process | None:
        try:
            system_server_proc = next(
                p
                for p in psutil.process_iter(["name"])
                if p.info.get("name") == "system_server" or p.name() == "system_server"
            )
            threads = system_server_proc.threads()
            for thread in threads:
                try:
                    p = psutil.Process(thread.id)
                    if p.name() == "CpuTracker":
                        return p
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        except (StopIteration, psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        return None

    def _refresh_cached_process(self) -> psutil.Process | None:
        proc = self._find_process()
        self._cpu_tracker_proc = proc
        return proc

    def _sample_cpu_percent(self, proc: psutil.Process) -> float | None:
        try:
            return proc.cpu_percent(interval=1.0)
        except psutil.AccessDenied:
            self._cpu_tracker_proc = None
            return None

    def _get_cpu_percent(self) -> float | None:
        proc = self._cpu_tracker_proc
        if not proc or not proc.is_running():
            proc = self._refresh_cached_process()

        if not proc:
            return None

        try:
            return self._sample_cpu_percent(proc)
        except psutil.NoSuchProcess:
            proc = self._refresh_cached_process()

            if not proc:
                return None

            try:
                return self._sample_cpu_percent(proc)
            except psutil.NoSuchProcess:
                self._cpu_tracker_proc = None
                return None

    @log_function_call
    async def start(self) -> None:
        cpu_percent = await asyncio.to_thread(self._get_cpu_percent)

        if cpu_percent is None:
            self.logger.warning("system_server-CpuTracker not found")
            return

        if cpu_percent >= self.cpu_threshold:
            self._threshold_count += 1
        else:
            self._threshold_count = 0

        if self._threshold_count >= self.threshold_count_max:
            msg = f"system_server-CpuTracker is abnormally using {cpu_percent}% CPU. Rebooting."
            self.logger.warning(msg)

            if self.notifier is not None:
                await self.notifier.send_webhook({"embeds": [{"title": "System Server Monitor", "description": msg}]})

            async with self.manager.internal_ipc() as (_, writer):
                request: IPCRequestInternal = {
                    "cmd": IPCCommand.INTERNAL,
                    "internal_cmd": IPCCommandInternal.REBOOT,
                    "kwargs": {},
                    "args": [],
                    "password": self.manager.ipc_password,
                }
                await send_json(writer, request)
