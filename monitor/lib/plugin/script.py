from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from lib.plugin.base import Plugin

if TYPE_CHECKING:
    from lib.manager import PluginManager


class ScriptPlugin(Plugin):
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
        **kwargs: Any,
    ) -> None:
        path = Path(script_path)
        name = path.stem

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
            screen_name = f"monitor_script_{self.name}"
            return ["screen", "-D", "-m", "-S", screen_name, *cmd]
        return cmd

    def _start(self) -> None:
        cmd = self._get_command()
        self.logger.info(f"Starting script with command: {cmd}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
            )

            while not self._stop_event.is_set():
                if self._process.poll() is not None:
                    break
                self._stop_event.wait(0.5)

            if self._stop_event.is_set():
                self.logger.info("Stopping script process...")
                self._terminate_process()

            stdout, stderr = self._process.communicate()
            if stdout:
                self.logger.info(f"Output: {stdout.decode('utf-8', errors='replace')}")
            if stderr:
                self.logger.error(f"Error: {stderr.decode('utf-8', errors='replace')}")

        except Exception as e:
            self.logger.error(f"Failed to run script: {e}")
        finally:
            self._process = None

    def _terminate_process(self) -> None:
        if not self._process:
            return

        if self.use_screen:
            screen_name = f"monitor_script_{self.name}"
            self.logger.info(f"Terminating screen session {screen_name}")
            subprocess.run(["screen", "-S", screen_name, "-X", "quit"], check=False)

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
