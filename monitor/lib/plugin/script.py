# ruff: noqa: S311

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from random import randint
from typing import TYPE_CHECKING, Any, override

from lib.plugin.base import Plugin

if TYPE_CHECKING:
    from lib.manager import PluginManager


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
        script_path: str,
        cwd: str | None = None,
        args: list[str] | None = None,
        use_screen: bool = False,
        name: str = "",
        **kwargs: Any,
    ) -> None:
        path = Path(script_path)
        if not name:
            name = path.stem
        name = f"ScriptPlugin_{name}_{randint(100, 999)}"

        super().__init__(manager, name=name, **kwargs)

        self.script_path = script_path
        self.cwd = cwd or str(path.parent)
        self.args = args or []
        self.use_screen = use_screen
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
        self.logger.info(f"Starting script with command: {cmd}")

        try:
            self._process = subprocess.Popen(cmd, cwd=self.cwd)
            while not self._stop_event.is_set():
                if self._process.poll() is not None:
                    break
                self._stop_event.wait(0.5)

            if self._stop_event.is_set():
                self.logger.info(f"Stopping {self.name} process...")
                self._terminate_process()
            else:
                self.logger.warning(f"Script {self.name} self-exited with code {self._process.returncode}")
                if self.restart_on_failure and self._process.returncode != 0:
                    raise RuntimeError(f"Script {self.name} exited unexpectedly")
        finally:
            self._process = None

    def _terminate_process(self) -> None:
        if not self._process:
            return

        if self.use_screen:
            self.logger.info(f"Terminating screen session {self.name}")
            subprocess.run(["screen", "-S", self.name, "-X", "quit"], check=False)

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.logger.warning("Process did not exit, terminating...")
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.logger.warning("Process did not terminate, killing...")
                self._process.kill()

    @override
    def stop(self) -> None:
        self._stop_event.set()
