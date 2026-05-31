from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, override

from ..config import ConfigLoader, ConfigT
from .base import Plugin

if TYPE_CHECKING:
    import argparse


class IntervalConfigT(ConfigT):
    INTERVAL: int


class IntervalConfigLoader[T: IntervalConfigT](ConfigLoader[T]):
    def get_defaults(self) -> T:
        defaults = super().get_defaults()
        defaults.update(
            {
                "INTERVAL": 10,
            }
        )
        return defaults

    def on_add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Interval in seconds between plugin executions",
        )

    def on_init(self) -> None:
        if self._config["INTERVAL"] <= 0:
            raise ValueError("INTERVAL must be a positive integer")


class IntervalPlugin(Plugin):
    def __init__(self, config: ConfigLoader[ConfigT]) -> None:
        super().__init__(config)
        self.interval = config["INTERVAL"]

    async def wait(self, timeout: float | int) -> bool:
        """Wait for the specified timeout or until the plugin is stopped.
        Returns True if the plugin was stopped, False if the timeout was reached.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return True
        return False

    def on_stop(self) -> None:
        """Called when the plugin is stopped. Useful for cleanup."""

    @override
    def stop(self) -> None:
        """Stop the plugin."""
        super().stop()
        self.on_stop()

    @override
    def _log_start_failure(self, exception: Exception) -> None:
        self.logger.error("plugin %s failed: %s", self.name, exception, stack_info=True)

    @override
    def _on_restart_disabled(self) -> None:
        self.logger.info("plugin %s will not restart (restart_on_failure=False)", self.name)

    @override
    def _exit_after_successful_start(self) -> bool:
        return False

    @override
    async def _wait_before_next_cycle(self) -> bool:
        if self.interval <= 0:
            raise RuntimeError("interval must be a positive integer")
        return await self.wait(self.interval)
