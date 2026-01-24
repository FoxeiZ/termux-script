from __future__ import annotations

from abc import abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from .base import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.manager import PluginManager
    from lib.plugin.metadata import PluginMetadata


class CronParser:
    """Simple crontab parser that can calculate the next execution time."""

    def __init__(self, cron_expression: str):
        self.expression = cron_expression.strip()
        self.minute, self.hour, self.day, self.month, self.weekday = self._parse_expression()

    def _parse_expression(self) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
        """Parse the cron expression into sets of valid values."""
        fields = self.expression.split()
        if len(fields) != 5:
            raise ValueError("Cron expression must have exactly 5 fields (minute hour day month weekday)")

        return (
            self._parse_field(fields[0], 0, 59),  # minute
            self._parse_field(fields[1], 0, 23),  # hour
            self._parse_field(fields[2], 1, 31),  # day
            self._parse_field(fields[3], 1, 12),  # month
            self._parse_field(fields[4], 0, 6),  # weekday (0=Sunday, 6=Saturday)
        )

    def _parse_field(self, field: str, min_val: int, max_val: int) -> set[int]:
        """Parse a single cron field into a set of valid values."""
        if field == "*":
            return set(range(min_val, max_val + 1))

        values: set[int] = set()

        # Handle comma-separated values
        for part in field.split(","):
            if "/" in part:
                # Handle step values like */2 or 1-10/2
                range_part, step = part.split("/")
                step = int(step)

                if range_part == "*":
                    start, end = min_val, max_val
                elif "-" in range_part:
                    start, end = map(int, range_part.split("-"))
                else:
                    start = end = int(range_part)

                values.update(range(start, end + 1, step))

            elif "-" in part:
                # Handle ranges like 1-5
                start, end = map(int, part.split("-"))
                values.update(range(start, end + 1))

            else:
                # Handle single numbers
                values.add(int(part))

        # Filter values to be within valid range
        return {v for v in values if min_val <= v <= max_val}

    def next(self, from_time: datetime | None = None) -> datetime:
        """
        Calculate the next execution time based on the cron expression.

        Args:
            from_time: Calculate next time from this datetime. If None, uses current time.

        Returns:
            datetime: Next execution time
        """
        if from_time is None:
            from_time = datetime.now()

        # Start from the next minute (cron jobs run at the beginning of the minute)
        next_time = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Safety counter to prevent infinite loops
        max_iterations = 527040  # One year worth of minutes (366 * 24 * 60)
        iterations = 0

        while iterations < max_iterations:
            if self._matches_time(next_time):
                return next_time

            next_time += timedelta(minutes=1)
            iterations += 1

        raise RuntimeError("Could not find next execution time within reasonable timeframe")

    def _matches_time(self, dt: datetime) -> bool:
        """Check if a datetime matches the cron expression."""
        # Convert Python weekday (0=Monday) to cron weekday (0=Sunday)
        weekday = (dt.weekday() + 1) % 7

        return (
            dt.minute in self.minute
            and dt.hour in self.hour
            and dt.day in self.day
            and dt.month in self.month
            and weekday in self.weekday
        )

    def __str__(self) -> str:
        return f"CronParser('{self.expression}')"

    def __repr__(self) -> str:
        return self.__str__()

    def __iter__(self):
        """Make the parser iterable to get the next execution time."""
        return self.next()


class CronPlugin(Plugin):
    if TYPE_CHECKING:
        cron_expression: str
        run_on_startup: bool
        _cron_parser: CronParser
        _last_run: datetime | None

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ) -> None:
        super().__init__(manager, metadata, logger)

        cron_expression = metadata.kwargs.get("cron_expression")
        run_on_startup = metadata.kwargs.get("run_on_startup")

        self.cron_expression = cron_expression or getattr(self.__class__, "cron_expression", "")
        self.run_on_startup = bool(run_on_startup) or getattr(self.__class__, "run_on_startup", False)

        if not self.cron_expression:
            raise ValueError("cron_expression must be provided either in __init__ or as class attribute")

        self._cron_parser = CronParser(self.cron_expression)
        self._last_run = None

    def __init_subclass__(cls, cron_expression: str = "", run_on_startup: bool = False, **kwargs: Any) -> None:
        """Support class-level parameters like ``class CustomCronPlugin(CronPlugin, cron_expression="...")``"""
        super().__init_subclass__(**kwargs)
        if cron_expression:
            cls.cron_expression = cron_expression
        if run_on_startup:
            cls.run_on_startup = run_on_startup

    def is_stopped(self) -> bool:
        """Check if the plugin is stopped."""
        return self._stop_event.is_set()

    def wait_until_next_run(self) -> bool:
        """Wait until the next scheduled time. Returns True if stopped, False if time to run."""
        next_run = self._cron_parser.next()
        now = datetime.now()
        wait_seconds = (next_run - now).total_seconds()

        if wait_seconds > 0:
            self.logger.debug("next run scheduled for %s (in %.1f seconds)", next_run, wait_seconds)
            return self._stop_event.wait(wait_seconds)

        return False

    def should_run_now(self) -> bool:
        """Check if the cron job should run at the current time."""
        now = datetime.now().replace(second=0, microsecond=0)

        # Prevent running multiple times in the same minute
        if self._last_run and self._last_run == now:
            return False

        return self._cron_parser._matches_time(now)

    @abstractmethod
    def start(self) -> Any:
        """The actual job to run. Must be implemented by subclasses."""
        raise NotImplementedError

    @override
    def _start(self) -> None:
        self.logger.info("starting cron plugin with expression: %s", self.cron_expression)

        if self.run_on_startup:
            self.logger.info("running on startup as requested")
            try:
                self.start()
                self._last_run = datetime.now().replace(second=0, microsecond=0)
            except Exception as e:
                self.logger.error("plugin %s failed on startup: %s", self.name, e)

        while not self._stop_event.is_set():
            try:
                if self.wait_until_next_run():
                    break

                if self.should_run_now():
                    self.logger.debug("running scheduled job at %s", datetime.now())
                    self.start()
                    self._last_run = datetime.now().replace(second=0, microsecond=0)
                elif self._stop_event.wait(1):
                    break

            except Exception as e:
                self.logger.error("plugin %s failed: %s", self.name, e)
                if self._stop_event.wait(60):
                    break
