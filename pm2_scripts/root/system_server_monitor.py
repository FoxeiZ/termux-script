from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, override

import psutil
from dotenv import find_dotenv, load_dotenv

if __name__ == "__main__":
    from __base__ import *

from lib.errors import PluginError
from lib.plugin import IntervalPlugin
from lib.plugin.interval import IntervalConfigLoader, IntervalConfigT
from lib.utils import log_function_call

if TYPE_CHECKING:
    import argparse


class SystemServerConfigT(IntervalConfigT):
    CPU_THRESHOLD: int
    THRESHOLD_COUNT_MAX: int


class SystemServerConfigLoader(IntervalConfigLoader[SystemServerConfigT]):
    def __init__(self) -> None:
        load_dotenv(find_dotenv(".env.system_server", usecwd=True))
        super().__init__()

    @override
    def get_defaults(self) -> SystemServerConfigT:
        defaults = super().get_defaults()
        defaults.update(
            {
                "NAME": "SystemServerMonitor",
                "CPU_THRESHOLD": 100,
                "THRESHOLD_COUNT_MAX": 3,
            }
        )
        return defaults

    def on_add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().on_add_arguments(parser)
        parser.add_argument(
            "--cpu-threshold",
            type=int,
            default=100,
            dest="CPU_THRESHOLD",
            help="CPU usage percentage threshold for system_server-CpuTracker to trigger action",
        )
        parser.add_argument(
            "--threshold-count-max",
            type=int,
            default=3,
            dest="THRESHOLD_COUNT_MAX",
            help="Number of consecutive threshold breaches before triggering action",
        )

    @override
    def on_init(self) -> None:
        super().on_init()
        if self.cpu_threshold <= 0:
            raise ValueError("CPU_THRESHOLD must be a positive integer")
        if self.threshold_count_max <= 0:
            raise ValueError("THRESHOLD_COUNT_MAX must be a positive integer")

    @property
    def cpu_threshold(self) -> int:
        return int(self._config["CPU_THRESHOLD"])

    @property
    def threshold_count_max(self) -> int:
        return int(self._config["THRESHOLD_COUNT_MAX"])


class SystemServerPlugin(IntervalPlugin):
    if TYPE_CHECKING:
        cpu_threshold: int
        threshold_count_max: int
        _threshold_count: int
        _cpu_tracker_proc: psutil.Process | None

    def __init__(self, config: SystemServerConfigLoader):
        try:
            os.lstat("/proc/stat")
        except PermissionError as e:
            raise PluginError("Permission denied to access /proc/stat. Please run as root.") from e

        super().__init__(config)
        self.cpu_threshold = config.cpu_threshold
        self.threshold_count_max = config.threshold_count_max

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

            reboot_script = Path(__file__).parent.parent / "reboot.sh"
            if not reboot_script.exists():
                self.logger.warning(f"Reboot script {reboot_script} not found. Attempting direct reboot command.")
                await asyncio.create_subprocess_exec(
                    "sudo",
                    "reboot",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
            else:
                self.logger.info(f"Executing reboot script: {reboot_script}")
                await asyncio.create_subprocess_exec(
                    "sudo",
                    str(reboot_script),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )


if __name__ == "__main__":
    from lib._runner import main

    config = SystemServerConfigLoader()
    plugin = SystemServerPlugin(config)
    asyncio.run(main(plugin))
