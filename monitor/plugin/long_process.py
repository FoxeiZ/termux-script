from __future__ import annotations

from typing import TYPE_CHECKING

from subprocess import Popen, PIPE
from lib.plugin import OneTimePlugin


class LongProcess(OneTimePlugin):
    if TYPE_CHECKING:
        _process: Popen | None

    def __init__(self, manager, webhook_url=""):
        super().__init__(manager, webhook_url)

        self._process = None

    def run(self):
        self._process = Popen(
            ["sleep", "10"],
            stdout=PIPE,
            stderr=PIPE,
        )
        self._process.communicate()
        self._process.wait()

    def kill(self):
        if not self._process:
            return

        self._process.kill()
        self._process.wait()
