from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, TypeVar

import psutil
from lib.errors import PluginError
from lib.plugin import IntervalPlugin
from lib.utils import log_function_call

if TYPE_CHECKING:
    from typing import TextIO, TypedDict

    from lib._types import WebhookPayload

    class _ProcessT(TypedDict):
        pid: int
        name: str
        cpu_percent: float


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


class SystemMonitorPlugin(IntervalPlugin):
    BATT_PATH = "/sys/class/power_supply/battery"
    if TYPE_CHECKING:
        _first_run: bool
        _file_cache: dict[str, TextIO]

        ReadT = TypeVar("ReadT", bound=str | int | float)

    def __init__(self, manager, interval=10, webhook_url="", **kwargs):
        try:
            os.lstat("/proc/stat")
        except PermissionError:
            raise PluginError(
                "Permission denied to access /proc/stat. Please run as root."
            )

        super().__init__(manager, interval, webhook_url)

        self._first_run = True
        self._file_cache = {}

    @log_function_call
    def get_uptime(self) -> str:
        return str(
            datetime.timedelta(
                seconds=(
                    datetime.datetime.now()
                    - datetime.datetime.fromtimestamp(psutil.boot_time())
                ).seconds
            )
        )

    @log_function_call
    def get_top_processes(self) -> list[_ProcessT]:
        processes = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                processes.append(p.info)
            except psutil.NoSuchProcess:
                pass
        processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
        return processes[:5]

    @log_function_call
    def __read_file(self, file_name: str, _type: type["ReadT"] = str) -> "ReadT | None":
        try:
            if file_name not in self._file_cache:
                self._file_cache[file_name] = open(
                    os.path.join(self.BATT_PATH, file_name), "r"
                )
            file = self._file_cache[file_name]
            file.seek(0)
            value = file.read().strip()
            if not value:
                return None
            return _type(value)

        except FileNotFoundError:
            pass

        except PermissionError:
            self.logger.warning(
                f"Permission denied to read {file_name} in {self.BATT_PATH}"
            )

        except Exception as e:
            self.logger.error(f"Error reading {file_name} in {self.BATT_PATH}: {e}")

        return None

    @log_function_call
    def __to_unit(
        self, c: int, file_name: str, _type: type["ReadT"] = str
    ) -> float | str:
        r = self.__read_file(file_name, _type)
        if not r or isinstance(r, str):
            return "Unknown"
        return r / c

    @log_function_call
    def get_battery_info(self) -> dict:
        if not os.path.exists(self.BATT_PATH):
            return {}

        return {
            "Capacity": self.__read_file("capacity", float),
            "Health": self.__read_file("health"),
            "Current": self.__to_unit(1000, "current_now", int),
            "Status": self.__read_file("status"),
            "Temperature": self.__to_unit(10, "temp", int),
        }

    @log_function_call
    def run(self):
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/mnt/installer/0/emulated")
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
                },
                {
                    "title": "Battery Info",
                    "fields": [
                        {
                            "name": k,
                            "value": str(v),
                            "inline": True,
                        }
                        for k, v in self.get_battery_info().items()
                    ],
                },
            ],
        }

        if self._first_run:
            self.send_webhook(payload=payload, wait=True)
            self._first_run = False
            return

        self.edit_webhook(payload=payload)

    @log_function_call
    def on_stop(self) -> None:
        for file in self._file_cache.values():
            try:
                file.close()
            except Exception as e:
                self.logger.error(f"Error closing file: {e}")
        self._file_cache.clear()
        return super().on_stop()
