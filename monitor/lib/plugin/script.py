from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, override

from lib.plugin.base import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.manager import PluginManager
    from lib.plugin.metadata import PluginMetadata


class ScriptPlugin(Plugin, restart_on_failure=True):
    if TYPE_CHECKING:
        script_path: str
        args: list[str]
        use_screen: bool
        _process: subprocess.Popen[bytes] | None
        _stop_event: threading.Event

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ) -> None:
        super().__init__(manager, metadata, logger)

        script_path = metadata.kwargs.get("script_path")
        if not script_path:
            raise ValueError("script_path is required")

        path = Path(str(script_path))
        self.script_path = str(script_path)
        self.cwd = str(metadata.kwargs.get("cwd") or path.parent)
        self.args = list(metadata.kwargs.get("args") or [])
        self.use_screen = bool(metadata.kwargs.get("use_screen", False))
        self._process = None
        self._stop_event = threading.Event()

    def _get_command(self) -> list[str]:
        cmd = [self.script_path, *self.args]
        if self.use_screen:
            # -D -m: Start screen in detached mode, but don't fork.
            # -S name: Session name
            return ["screen", "-D", "-m", "-S", self.name, *cmd]
        return cmd

    def start(self) -> None:
        cmd = self._get_command()
        self.logger.info("starting script with command: %s", cmd)

        try:
            self._process = subprocess.Popen(cmd, cwd=self.cwd)
            while not self._stop_event.is_set():
                if self._process.poll() is not None:
                    break
                self._stop_event.wait(0.5)

            if self._stop_event.is_set():
                self.logger.info("stopping %s process", self.name)
                self._terminate_process()
            else:
                self.logger.warning("script %s self-exited with code %s", self.name, self._process.returncode)
                if self.restart_on_failure and self._process.returncode != 0:
                    raise RuntimeError(f"script {self.name} exited unexpectedly")
        finally:
            self._process = None

    def _terminate_process(self) -> None:
        if not self._process:
            return

        if self.use_screen:
            self.logger.info("terminating screen session %s", self.name)
            subprocess.run(["screen", "-S", self.name, "-X", "quit"], check=False)

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.logger.warning("process did not exit, terminating")
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.logger.warning("process did not terminate, killing")
                self._process.kill()

    @override
    def stop(self) -> None:
        self._stop_event.set()
