from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from lib.plugin import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class NativeLongProcessPlugin(Plugin):
    async def start(self) -> None:
        while not self._stop_event.is_set():
            self.logger.info(
                "NativeLongProcessPlugin is running..., %s",
                self._stop_event.is_set(),
            )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)

    def force_stop(self) -> None:
        super().stop()

    def stop(self) -> None:
        self.logger.info("NativeLongProcessPlugin.stop called")
        super().stop()


class NativeLongProcessPluginRoot(Plugin, requires_root=True):
    async def start(self) -> None:
        while not self._stop_event.is_set():
            self.logger.info(
                "NativeLongProcessPluginRoot is running..., %s",
                self._stop_event.is_set(),
            )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)

    def force_stop(self) -> None:
        super().stop()

    def stop(self) -> None:
        self.logger.info("NativeLongProcessPluginRoot.stop called")
        super().stop()


class LongProcessPlugin(Plugin):
    if TYPE_CHECKING:
        _process: asyncio.subprocess.Process | None

    def __init__(self, manager: PluginManager, metadata: PluginMetadata, logger: Logger):
        super().__init__(manager, metadata, logger)
        self._process = None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            "sleep",
            "10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            process_task = asyncio.create_task(self._process.communicate())
            stop_task = asyncio.create_task(self._stop_event.wait())

            done, pending = await asyncio.wait([process_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()

            if stop_task in done:
                return

            if self.notifier is not None and not self._stop_event.is_set():
                await self.notifier.send_success()

        except asyncio.CancelledError:
            self.logger.info("LongProcessPlugin task cancelled")
            raise
        finally:
            if self._process and self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._process.terminate()

    def stop(self) -> None:
        super().stop()
        if not self._process or self._process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.terminate()

    def force_stop(self) -> None:
        super().stop()
        if not self._process or self._process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.kill()


class LongProcessPluginWithError(LongProcessPlugin):
    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                "sleet",
                "10",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _, stderr = await self._process.communicate()

            if self._process.returncode != 0 and self.notifier is not None:
                await self.notifier.send_error(stderr.decode(errors="ignore"))

        except asyncio.CancelledError:
            if self._process and self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._process.terminate()
            raise
        except Exception as e:
            if self.notifier is not None:
                await self.notifier.send_error(f"An error occurred: ```\n{e}\n```")


class LongProcessPluginWithLongOutput(LongProcessPlugin):
    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            "yes",
            "hello",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.logger.warning("process started")
        self.logger.warning(self._process)
        self.logger.warning(self._process.pid)

        try:
            process_task = asyncio.create_task(self._process.communicate())
            stop_task = asyncio.create_task(self._stop_event.wait())

            done, pending = await asyncio.wait([process_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()

            # "yes" runs infinitely, so if it finishes or the stop event is called,
            # we need to gracefully shut it down.
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except TimeoutError:
                    self._process.kill()

            if process_task in done:
                stdout, _ = process_task.result()
                if self.notifier is not None:
                    await self.notifier.send_success(stdout.decode(errors="ignore"))

        except asyncio.CancelledError:
            if self._process and self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._process.terminate()
            raise
