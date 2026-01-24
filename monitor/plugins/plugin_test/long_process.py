from __future__ import annotations

import threading
from subprocess import PIPE, Popen, TimeoutExpired
from time import sleep
from typing import TYPE_CHECKING

from lib.plugin import Plugin

if TYPE_CHECKING:
    from logging import Logger
    from typing import Any

    from lib.manager import PluginManager
    from lib.plugin.metadata import PluginMetadata


class NativeLongProcessPlugin(Plugin):
    def __init__(self, manager: PluginManager, metadata: PluginMetadata, logger: Logger):
        super().__init__(manager, metadata, logger)
        self.logger.info("Initialized NativeLongProcessPlugin")

        self._stop_event = threading.Event()

    def start(self):
        while not self._stop_event.is_set():
            self.logger.info("NativeLongProcessPlugin is running...")
            sleep(1)

    def force_stop(self):
        self._stop_event.set()

    def stop(self) -> None:
        self._stop_event.set()


class NativeLongProcessPluginRoot(Plugin, requires_root=True):
    def __init__(self, manager: PluginManager, metadata: PluginMetadata, logger: Logger):
        super().__init__(manager, metadata, logger)
        self.logger.info("Initialized NativeLongProcessPluginRoot")

        self._stop_event = threading.Event()

    def start(self):
        while not self._stop_event.is_set():
            self.logger.info("NativeLongProcessPlugin is running...")
            sleep(1)

    def force_stop(self):
        self._stop_event.set()

    def stop(self) -> None:
        self._stop_event.set()


class LongProcessPlugin(Plugin):
    if TYPE_CHECKING:
        _process: Popen[Any] | None

    def __init__(self, manager: PluginManager, metadata: PluginMetadata, logger: Logger):
        super().__init__(manager, metadata, logger)
        self._process = None

    def start(self):
        self._process = Popen(
            ["sleep", "10"],
            stdout=PIPE,
            stderr=PIPE,
        )
        self._process.communicate()
        self._process.wait()
        self.send_success()

    def stop(self) -> None:
        if not self._process:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def force_stop(self) -> None:
        if not self._process:
            return
        self._process.kill()
        self._process.wait()


class LongProcessPluginWithError(LongProcessPlugin):
    def start(self):
        try:
            self._process = Popen(
                ["sleet", "10"],
                stdout=PIPE,
                stderr=PIPE,
            )
            self._process.communicate()
            self._process.wait()

            if self._process.returncode != 0:
                err = self._process.stderr.read()  # type: ignore
                self.send_error(err.decode())
        except Exception as e:
            self.send_error(f"An error occurred: ```\n{e}\n```")


class LongProcessPluginWithLongOutput(LongProcessPlugin):
    def start(self):
        self._process = Popen(
            ["yes", "hello"],
            stdout=PIPE,
            stderr=PIPE,
        )
        self.logger.warning("process started")
        self.logger.warning(self._process)
        self.logger.warning(self._process.pid)
        stdout, _ = self._process.communicate()
        try:
            self._process.wait(timeout=5)
        except TimeoutExpired:
            self._process.terminate()

        self.send_success(stdout.read().decode())
