import os
import subprocess
from typing import TYPE_CHECKING

import psutil
from lib.errors import PluginError
from lib.plugin import IntervalPlugin
from lib.utils import log_function_call


class SystemServerPlugin(IntervalPlugin):
    if TYPE_CHECKING:
        cpu_threshold: int
        threshold_count_max: int
        _threshold_count: int
        _cpu_tracker_proc: psutil.Process | None

    def __init__(
        self, manager, interval=10, webhook_url="", cpu_threshold: int = 100, **kwargs
    ):
        try:
            os.lstat("/proc/stat")
        except PermissionError:
            raise PluginError(
                "Permission denied to access /proc/stat. Please run as root."
            )

        super().__init__(manager, interval, webhook_url)
        self.cpu_threshold = cpu_threshold
        self.threshold_count_max = kwargs.get("threshold_count_max", 3)

        self._threshold_count = 0
        self._cpu_tracker_proc = None

    @log_function_call
    def _find_process(self, name="CpuTracker"):
        try:
            system_server_proc = next(
                p
                for p in psutil.process_iter(attrs=["name"])
                if p.name() == "system_server"
            )
            threads = system_server_proc.threads()
            for thread in threads:
                p = psutil.Process(thread.id)
                if p.name() == name:
                    return p

            return None

        except StopIteration:
            return None

    @log_function_call
    def find_process(self, force=False):
        if (
            force
            or not self._cpu_tracker_proc
            or not self._cpu_tracker_proc.is_running()
        ):
            self._cpu_tracker_proc = self._find_process()

        return self._cpu_tracker_proc

    @log_function_call
    def run(self):
        process = self.find_process()
        if not process:
            self.logger.warning("system_server-CpuTracker not found")
            return

        cpu_percent = process.cpu_percent(interval=1)
        if cpu_percent >= self.cpu_threshold:
            self._threshold_count += 1
        else:
            self._threshold_count = 0

        if self._threshold_count >= self.threshold_count_max:
            msg = f"system_server-CpuTracker is abnormally using {cpu_percent}% CPU. Rebooting."
            self.logger.warning(msg)
            self.send_webhook(
                {"embeds": [{"title": "System Server Monitor", "description": msg}]}
            )
            subprocess.run(["reboot"])
