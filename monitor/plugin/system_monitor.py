from __future__ import annotations

import datetime
import errno
import os
from typing import TYPE_CHECKING

import psutil
from lib.manager import get_logger
from lib.plugin import IntervalPlugin

if TYPE_CHECKING:
    from typing import TypedDict

    from lib._types import WebhookPayload

    class _ProcessT(TypedDict):
        pid: int
        name: str
        cpu_percent: float


logger = get_logger("SystemMonitor")


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


class SystemMonitorPlugin(IntervalPlugin):
    if TYPE_CHECKING:
        first_run: bool

    def __init__(self, manager, interval=10, webhook_url="", **kwargs):
        try:
            os.lstat("/proc/stat")
        except IOError as e:
            if e.errno == errno.EPERM:
                logger.error(
                    "Permission denied to access /proc/stat. Please run as root."
                )

        super().__init__(manager, interval, webhook_url)

        self.first_run = True

    def get_uptime(self) -> str:
        return str(
            datetime.timedelta(
                seconds=(
                    datetime.datetime.now()
                    - datetime.datetime.fromtimestamp(psutil.boot_time())
                ).seconds
            )
        )

    def get_top_processes(self) -> list[_ProcessT]:
        processes = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                processes.append(p.info)
            except psutil.NoSuchProcess:
                pass
        processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
        return processes[:5]

    def run(self):
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        top_processes = self.get_top_processes()

        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": f"Last update: <t:{int(datetime.datetime.now(datetime.UTC).timestamp())}:R>",
                    "fields": [
                        {
                            "name": "CPU Usage",
                            "value": f"{cpu_percent}%",
                            "inline": True,
                        },
                        {
                            "name": "Memory Usage",
                            "value": f"{memory.percent}%\n{sizeof_fmt(memory.used)} / {sizeof_fmt(memory.total)}",
                            "inline": True,
                        },
                        {
                            "name": "Swap Usage",
                            "value": f"{swap.percent}%\n{sizeof_fmt(swap.used)} / {sizeof_fmt(swap.total)}",
                            "inline": True,
                        },
                        {
                            "name": "Disk Usage",
                            "value": f"{disk.percent}%\n{sizeof_fmt(disk.used)} / {sizeof_fmt(disk.total)}",
                            "inline": False,
                        },
                        {
                            "name": "Top Processes",
                            "value": "\n".join(
                                f"{p['name']} ({p['pid']}): {p['cpu_percent']}%"
                                for p in top_processes
                            ),
                            "inline": False,
                        },
                    ],
                    "footer": {
                        "text": f"Uptime: {self.get_uptime()}",
                    },
                    # "timestamp": datetime.datetime.now(datetime.UTC).strftime(
                    #     r"%Y-%m-%dT%H:%M:%SZ"
                    # ),
                }
            ],
        }

        if self.first_run:
            self.send_webhook(payload=payload, wait=True)
            self.first_run = False
            return

        self.edit_webhook(payload=payload)

    def kill(self):
        pass
