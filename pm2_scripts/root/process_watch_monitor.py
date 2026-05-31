from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

if __name__ == "__main__":
    from __base__ import *

from lib.plugin import IntervalPlugin
from lib.plugin.interval import IntervalConfigLoader, IntervalConfigT
from lib.utils import log_function_call

if TYPE_CHECKING:
    import argparse
    from typing import TypedDict

    from lib.types import EmbedField, WebhookPayload

    class _ProcessSample(TypedDict):
        pid: int
        name: str
        cpu_percent: float
        ram_percent: float
        combined_percent: float


class ProcessWatchConfigT(IntervalConfigT):
    CPU_THRESHOLD: float
    RAM_THRESHOLD: float
    COMBINED_THRESHOLD: float
    WATCH_SECONDS: int
    TOP_N: int
    WHITELIST: list[str] | None
    BLACKLIST: list[str] | None
    WHITELIST_PIDS: list[int] | None
    BLACKLIST_PIDS: list[int] | None


class ProcessWatchConfig(IntervalConfigLoader[ProcessWatchConfigT]):
    def get_defaults(self) -> ProcessWatchConfigT:
        defaults = super().get_defaults()
        defaults.update(
            {
                "NAME": "ProcessWatchMonitor",
                "CPU_THRESHOLD": 80.0,
                "RAM_THRESHOLD": 80.0,
                "COMBINED_THRESHOLD": 150.0,
                "WATCH_SECONDS": 30,
                "TOP_N": 10,
                "WHITELIST": [],
                "BLACKLIST": [],
                "WHITELIST_PIDS": [],
                "BLACKLIST_PIDS": [],
            }
        )
        return defaults

    def on_add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().on_add_arguments(parser)
        parser.add_argument(
            "--cpu-threshold",
            type=float,
            default=80.0,
            help="CPU usage percentage threshold",
            dest="CPU_THRESHOLD",
        )
        parser.add_argument(
            "--ram-threshold",
            type=float,
            default=80.0,
            help="RAM usage percentage threshold",
            dest="RAM_THRESHOLD",
        )
        parser.add_argument(
            "--combined-threshold",
            type=float,
            default=150.0,
            dest="COMBINED_THRESHOLD",
            help="Combined CPU + RAM usage percentage threshold",
        )
        parser.add_argument(
            "--watch-seconds",
            type=int,
            default=30,
            dest="WATCH_SECONDS",
            help="Number of seconds a process must stay above the threshold before a notification is sent",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=10,
            dest="TOP_N",
            help="Number of top processes to include in the notification",
        )
        parser.add_argument(
            "--whitelist",
            nargs="*",
            default=[],
            dest="WHITELIST",
            help="List of process names to always monitor (overrides blacklist)",
        )
        parser.add_argument(
            "--blacklist",
            nargs="*",
            default=[],
            dest="BLACKLIST",
            help="List of process names to ignore",
        )
        parser.add_argument(
            "--whitelist-pids",
            nargs="*",
            type=int,
            default=[],
            dest="WHITELIST_PIDS",
            help="List of process IDs to always monitor (overrides blacklist)",
        )
        parser.add_argument(
            "--blacklist-pids",
            nargs="*",
            type=int,
            default=[],
            dest="BLACKLIST_PIDS",
            help="List of process IDs to ignore",
        )

    def on_init(self) -> None:
        super().on_init()
        if (
            self._config["CPU_THRESHOLD"] < 0
            or self._config["RAM_THRESHOLD"] < 0
            or self._config["COMBINED_THRESHOLD"] < 0
        ):
            raise ValueError("Threshold values must be non-negative")
        if self._config["WATCH_SECONDS"] <= 0:
            raise ValueError("WATCH_SECONDS must be a positive integer")
        if self._config["TOP_N"] <= 0:
            raise ValueError("TOP_N must be a positive integer")
        if set(self._config["WHITELIST"] or []) & set(self._config["BLACKLIST"] or []):
            raise ValueError("Process names cannot be in both whitelist and blacklist")
        if set(self._config["WHITELIST_PIDS"] or []) & set(self._config["BLACKLIST_PIDS"] or []):
            raise ValueError("Process IDs cannot be in both whitelist and blacklist")

    @property
    def cpu_threshold(self) -> float:
        return self._config["CPU_THRESHOLD"]

    @property
    def ram_threshold(self) -> float:
        return self._config["RAM_THRESHOLD"]

    @property
    def combined_threshold(self) -> float:
        return self._config["COMBINED_THRESHOLD"]

    @property
    def watch_seconds(self) -> int:
        return self._config["WATCH_SECONDS"]

    @property
    def top_n(self) -> int:
        return self._config["TOP_N"]

    @property
    def whitelist(self) -> set[str]:
        return {str(item) for item in self._config["WHITELIST"] or []}

    @property
    def blacklist(self) -> set[str]:
        return {str(item) for item in self._config["BLACKLIST"] or []}

    @property
    def whitelist_pids(self) -> set[int]:
        return {int(item) for item in self._config["WHITELIST_PIDS"] or []}

    @property
    def blacklist_pids(self) -> set[int]:
        return {int(item) for item in self._config["BLACKLIST_PIDS"] or []}


@dataclass(slots=True)
class _WatchEntry:
    pid: int
    name: str
    first_seen: float
    last_seen: float
    cpu_percent: float
    ram_percent: float
    combined_percent: float
    notified: bool = False


class ProcessWatchMonitorPlugin(IntervalPlugin):
    interval = 10

    if TYPE_CHECKING:
        cpu_threshold: float
        ram_threshold: float
        combined_threshold: float
        watch_seconds: int
        top_n: int
        whitelist: set[str]
        blacklist: set[str]
        whitelist_pids: set[int]
        blacklist_pids: set[int]
        _watch_list: dict[int, _WatchEntry]

    def __init__(self, config: ProcessWatchConfig) -> None:
        super().__init__(config)
        self.cpu_threshold = config.cpu_threshold
        self.ram_threshold = config.ram_threshold
        self.combined_threshold = config.combined_threshold
        self.watch_seconds = config.watch_seconds
        self.top_n = config.top_n

        self.whitelist = config.whitelist
        self.blacklist = config.blacklist
        self.whitelist_pids = config.whitelist_pids
        self.blacklist_pids = config.blacklist_pids
        self.blacklist_pids.add(os.getpid())

        self._watch_list = {}
        self._prime_cpu_percent()

    def _prime_cpu_percent(self) -> None:
        for process in psutil.process_iter(["pid"]):
            try:
                process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _is_blacklisted(self, pid: int, name: str) -> bool:
        return pid in self.blacklist_pids or name in self.blacklist

    def _is_whitelisted(self, pid: int, name: str) -> bool:
        if not self.whitelist and not self.whitelist_pids:
            return True
        return pid in self.whitelist_pids or name in self.whitelist

    def _is_above_threshold(self, sample: _ProcessSample) -> bool:
        return (
            sample["cpu_percent"] >= self.cpu_threshold
            or sample["ram_percent"] >= self.ram_threshold
            or sample["combined_percent"] >= self.combined_threshold
        )

    def _collect_samples(self) -> list[_ProcessSample]:
        samples: list[_ProcessSample] = []
        for process in psutil.process_iter(["pid", "name"]):
            try:
                pid = int(process.info.get("pid") or 0)
                if pid <= 0:
                    continue

                name = str(process.info.get("name") or f"pid-{pid}")
                if self._is_blacklisted(pid, name):
                    continue
                if not self._is_whitelisted(pid, name):
                    continue

                cpu_percent = float(process.cpu_percent(interval=None))
                ram_percent = float(process.memory_percent())
                combined_percent = cpu_percent + ram_percent

                samples.append(
                    {
                        "pid": pid,
                        "name": name,
                        "cpu_percent": cpu_percent,
                        "ram_percent": ram_percent,
                        "combined_percent": combined_percent,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return samples

    @staticmethod
    def _rank_samples(samples: list[_ProcessSample], key: str, top_n: int) -> list[_ProcessSample]:
        return sorted(samples, key=lambda sample: float(sample[key]), reverse=True)[:top_n]

    def _format_top_list(self, label: str, ranked: list[_ProcessSample], key: str) -> str:
        if not ranked:
            return f"{label}: none"
        rows = [f"{sample['name']} ({sample['pid']}): {sample[key]:.2f}%" for sample in ranked]
        return f"{label}:\n" + "\n".join(rows)

    async def _send_watch_notification(
        self,
        entry: _WatchEntry,
        top_cpu: list[_ProcessSample],
        top_ram: list[_ProcessSample],
        top_combined: list[_ProcessSample],
    ) -> None:
        duration = int(time.time() - entry.first_seen)
        description = (
            f"Process {entry.name} ({entry.pid}) stayed above threshold for about {duration}s.\n"
            f"CPU={entry.cpu_percent:.2f}% RAM={entry.ram_percent:.2f}% Combined={entry.combined_percent:.2f}%"
        )
        fields: list[EmbedField] = [
            {
                "name": "Watched Process",
                "value": f"{entry.name} ({entry.pid})",
                "inline": False,
            },
            {
                "name": "Usage",
                "value": (
                    f"CPU: {entry.cpu_percent:.2f}%\n"
                    f"RAM: {entry.ram_percent:.2f}%\n"
                    f"Combined: {entry.combined_percent:.2f}%"
                ),
                "inline": True,
            },
            {
                "name": "Thresholds",
                "value": (
                    f"CPU >= {self.cpu_threshold:.2f}%\n"
                    f"RAM >= {self.ram_threshold:.2f}%\n"
                    f"Combined >= {self.combined_threshold:.2f}%"
                ),
                "inline": True,
            },
            {
                "name": "Top CPU",
                "value": self._format_top_list("CPU", top_cpu, "cpu_percent")[:1024],
                "inline": False,
            },
            {
                "name": "Top RAM",
                "value": self._format_top_list("RAM", top_ram, "ram_percent")[:1024],
                "inline": False,
            },
            {
                "name": "Top Combined",
                "value": self._format_top_list("Combined", top_combined, "combined_percent")[:1024],
                "inline": False,
            },
        ]

        payload: WebhookPayload = {
            "embeds": [
                {
                    "title": "Process Watch Alert",
                    "description": description,
                    "fields": fields,
                    "color": 14754595,
                }
            ]
        }
        if self.notifier is not None:
            await self.notifier.send_webhook(payload=payload)

    @log_function_call
    async def start(self) -> None:
        now = time.time()
        samples = await asyncio.to_thread(self._collect_samples)

        top_cpu = self._rank_samples(samples, "cpu_percent", self.top_n)
        top_ram = self._rank_samples(samples, "ram_percent", self.top_n)
        top_combined = self._rank_samples(samples, "combined_percent", self.top_n)

        self.logger.debug("top cpu: %s", [f"{s['name']}:{s['cpu_percent']:.2f}" for s in top_cpu])
        self.logger.debug("top ram: %s", [f"{s['name']}:{s['ram_percent']:.2f}" for s in top_ram])
        self.logger.debug("top combined: %s", [f"{s['name']}:{s['combined_percent']:.2f}" for s in top_combined])

        hot_samples = [sample for sample in samples if self._is_above_threshold(sample)]
        hot_pids = {sample["pid"] for sample in hot_samples}

        for sample in hot_samples:
            pid = sample["pid"]
            entry = self._watch_list.get(pid)
            if entry is None:
                self._watch_list[pid] = _WatchEntry(
                    pid=pid,
                    name=sample["name"],
                    first_seen=now,
                    last_seen=now,
                    cpu_percent=sample["cpu_percent"],
                    ram_percent=sample["ram_percent"],
                    combined_percent=sample["combined_percent"],
                    notified=False,
                )
                self.logger.info(
                    "added process to watch list: %s (%s) cpu=%.2f ram=%.2f combined=%.2f",
                    sample["name"],
                    pid,
                    sample["cpu_percent"],
                    sample["ram_percent"],
                    sample["combined_percent"],
                )
                continue

            entry.last_seen = now
            entry.cpu_percent = sample["cpu_percent"]
            entry.ram_percent = sample["ram_percent"]
            entry.combined_percent = sample["combined_percent"]

            if not entry.notified and now - entry.first_seen >= self.watch_seconds:
                self.logger.warning(
                    "process %s (%s) stayed above threshold for %ss",
                    entry.name,
                    entry.pid,
                    self.watch_seconds,
                )
                await self._send_watch_notification(entry, top_cpu, top_ram, top_combined)
                entry.notified = True

        for watched_pid in list(self._watch_list.keys()):
            if watched_pid in hot_pids:
                continue

            removed = self._watch_list.pop(watched_pid)
            self.logger.info(
                "removed process from watch list: %s (%s) cpu=%.2f ram=%.2f combined=%.2f",
                removed.name,
                removed.pid,
                removed.cpu_percent,
                removed.ram_percent,
                removed.combined_percent,
            )


if __name__ == "__main__":
    from lib._runner import main

    config = ProcessWatchConfig()
    plugin = ProcessWatchMonitorPlugin(config)
    asyncio.run(main(plugin))
