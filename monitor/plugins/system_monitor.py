from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import psutil
from lib.errors import PluginError
from lib.plugin import IntervalPlugin
from lib.utils import log_function_call

if TYPE_CHECKING:
    from logging import Logger
    from typing import TextIO, TypedDict

    from lib.types import PluginMetadata, WebhookPayload
    from lib.worker import PluginManager

    _ReadT = TypeVar("_ReadT", bound=str | int | float)

    class _ProcessT(TypedDict):
        pid: int
        name: str
        cpu_percent: float | None


def sizeof_fmt(num: float, suffix: str = "B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


class SystemMonitorPlugin(IntervalPlugin, requires_root=True):
    BATT_PATH = "/sys/class/power_supply/battery"
    interval = 10

    if TYPE_CHECKING:
        _first_run: bool
        _file_cache: dict[str, TextIO]

    def __init__(self, manager: PluginManager, metadata: PluginMetadata, logger: Logger):
        try:
            os.lstat("/proc/stat")
        except PermissionError:
            raise PluginError("Permission denied to access /proc/stat. Please run as root.") from None

        super().__init__(manager, metadata, logger)

        self._first_run = True
        self._file_cache = {}

    def get_uptime(self) -> str:
        return str(
            datetime.timedelta(
                seconds=(datetime.datetime.now() - datetime.datetime.fromtimestamp(psutil.boot_time())).seconds
            )
        )

    def get_top_processes(self) -> list[_ProcessT]:
        processes: list[_ProcessT] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                processes.append(p.info)  # type: ignore
        processes.sort(key=lambda x: x.get("cpu_percent") or 0.0, reverse=True)
        return processes[:5]

    def __read_file(self, file_name: str, _type: type[_ReadT] = str) -> _ReadT | None:
        try:
            if file_name not in self._file_cache:
                self._file_cache[file_name] = (Path(self.BATT_PATH) / file_name).open()
            file = self._file_cache[file_name]
            file.seek(0)
            value = file.read().strip()
            if not value:
                return None
            return _type(value)

        except FileNotFoundError:
            pass
        except PermissionError:
            self.logger.warning(f"Permission denied to read {file_name} in {self.BATT_PATH}")
        except Exception as e:
            self.logger.error(f"Error reading {file_name} in {self.BATT_PATH}: {e}")

        return None

    def __to_unit(
        self,
        c: int,
        file_name: str,
        _type: type[_ReadT] = str,
        *,
        decimal: int = 1,
    ) -> str:
        r = self.__read_file(file_name, _type)
        if r is None:
            return "Unknown"
        return f"{float(r) / c:.{decimal}f}"

    def get_battery_info(self) -> dict[str, Any]:
        if not Path(self.BATT_PATH).exists():
            return {}

        return {
            "Capacity": self.__read_file("capacity", float),
            "Health": self.__read_file("health"),
            "Current": self.__to_unit(1000, "current_now", int),
            "Status": self.__read_file("status"),
            "Temperature": self.__to_unit(10, "temp", int),
            "Voltage": self.__to_unit(1000000, "voltage_now", int, decimal=2),
        }

    def _collect_stats(self) -> dict[str, Any]:
        """Synchronous wrapper to collect all blocking psutil and file I/O stats."""
        return {
            "cpu_percent": psutil.cpu_percent(),
            "memory": psutil.virtual_memory(),
            "swap": psutil.swap_memory(),
            "disk": psutil.disk_usage("/mnt/installer/0/emulated"),
            "top_processes": self.get_top_processes(),
            "battery_info": self.get_battery_info(),
            "uptime": self.get_uptime(),
        }

    @log_function_call
    async def start(self) -> None:
        # Execute all blocking OS reads in the executor
        stats = await asyncio.to_thread(self._collect_stats)

        memory = stats["memory"]
        swap = stats["swap"]
        disk = stats["disk"]

        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": f"Last update: <t:{int(datetime.datetime.now(datetime.UTC).timestamp())}:R>",
                    "fields": [
                        {
                            "name": "CPU Usage",
                            "value": f"{stats['cpu_percent']}%",
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
                                f"{p['name']} ({p['pid']}): {p['cpu_percent'] or 0.0}%" for p in stats["top_processes"]
                            ),
                            "inline": False,
                        },
                    ],
                    "footer": {
                        "text": f"Uptime: {stats['uptime']}",
                    },
                },
                {
                    "title": "Battery Info",
                    "fields": [
                        {
                            "name": k,
                            "value": str(v),
                            "inline": True,
                        }
                        for k, v in stats["battery_info"].items()
                    ],
                },
            ],
        }

        if self.notifier is None:
            return

        if self._first_run:
            await self.notifier.send_webhook(payload=payload, wait=True)
            self._first_run = False
            return

        await self.notifier.edit_webhook(payload=payload)

    @log_function_call
    def on_stop(self) -> None:
        for file in self._file_cache.values():
            try:
                file.close()
            except Exception as e:
                self.logger.error("error closing file: %s", e)
        self._file_cache.clear()
        return super().on_stop()
