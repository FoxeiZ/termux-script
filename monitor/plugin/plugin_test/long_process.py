from __future__ import annotations

from subprocess import PIPE, Popen, TimeoutExpired
from typing import TYPE_CHECKING

from lib.plugins import Plugin


class LongProcessPlugin(Plugin):
    if TYPE_CHECKING:
        _process: Popen | None

    def __init__(self, manager, webhook_url=""):
        super().__init__(manager, webhook_url)

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

    def force_stop(self):
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
        self.logger.warning("Process started")
        self.logger.warning(self._process)
        self.logger.warning(self._process.pid)
        stdout, _ = self._process.communicate()
        try:
            self._process.wait(timeout=5)
        except TimeoutExpired:
            self._process.terminate()

        self.send_success(stdout.read().decode())
