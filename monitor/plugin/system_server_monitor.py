from __future__ import annotations

from typing import TYPE_CHECKING


import subprocess
import psutil

from ..lib import IntervalPlugin
from ..lib.manager import get_logger


logger = get_logger("SystemServerMonitor")


class SystemServerMonitor(IntervalPlugin):
    if TYPE_CHECKING:
        _cpu_tracker_proc: psutil.Process | None

    def __init__(self, manager, interval=10, webhook_url=""):
        super().__init__(manager, interval, webhook_url)

        self._cpu_tracker_proc = None

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

    def find_process(self, force=False):
        if (
            force
            or not self._cpu_tracker_proc
            or not self._cpu_tracker_proc.is_running()
        ):
            self._cpu_tracker_proc = self._find_process()

        return self._cpu_tracker_proc

    def run(self):
        process = self.find_process()
        if not process:
            logger.warning("system_server-CpuTracker not found")
            return

        cpu_percent = process.cpu_percent(interval=1)
        if cpu_percent > 90:
            logger.warning(
                f"system_server is abnormally using {cpu_percent}% CPU. Rebooting."
            )
            self.manager.send_webhook(
                self.webhook_url,
                {
                    "title": "Damn phone gone wild",
                    "description": f"CpuTracker is abnormally using {cpu_percent}% CPU. Rebooting.",
                },
            )
            subprocess.run(["reboot"])
