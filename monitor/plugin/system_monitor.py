from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import psutil
from lib.manager import get_logger
from lib.plugin import IntervalPlugin

if TYPE_CHECKING:
    from lib._types import WebhookPayload


logger = get_logger("SystemMonitor")


class SystemMonitor(IntervalPlugin):
    if TYPE_CHECKING:
        first_run: bool

    def __init__(self, manager, interval=10, webhook_url="", **kwargs):
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

    def run(self):
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")

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
                            "value": f"{memory.percent}%",
                            "inline": True,
                        },
                        {
                            "name": "Swap Usage",
                            "value": f"{swap.percent}%",
                            "inline": True,
                        },
                        {
                            "name": "Disk Usage",
                            "value": f"{disk.percent}%",
                            "inline": True,
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
