from __future__ import annotations

import os
import re
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib.config import IS_WINDOWS
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
        _process: subprocess.Popen[str] | None

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
        self.args = list(metadata.kwargs.get("args") or []) + list(metadata.args or [])
        self.use_screen = bool(metadata.kwargs.get("use_screen", False))
        self._process = None

    def _get_command(self) -> list[str]:
        cmd = [self.script_path, *self.args]
        if self.use_screen:
            # -D -m: Start screen in detached mode, but don't fork.
            # -S name: Session name
            return ["screen", "-D", "-m", "-S", self.name, *cmd]
        return cmd

    def _get_popen_kwargs(self) -> dict[str, Any]:
        if IS_WINDOWS:
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @property
    def _screen_base_name(self) -> str:
        """return the stable base name by stripping any trailing _<hex> suffix."""
        return re.sub(r"_[0-9a-f]{6,}$", "", self.name)

    def _list_matching_screen_sessions(self) -> list[str]:
        """return screen session ids (pid.name) whose name starts with _screen_base_name."""
        try:
            result = subprocess.run(
                ["screen", "-ls"],
                capture_output=True,
                text=True,
                check=False,
            )
            # each session line looks like: "\t<pid>.<name>\t(<state>)"
            sessions: list[str] = []
            prefix = self._screen_base_name
            for line in result.stdout.splitlines():
                match = re.match(r"^\s+(\d+\.\S+)", line)
                if match:
                    session_id = match.group(1)
                    # match pid.name where name starts with our base prefix
                    session_name = session_id.split(".", 1)[-1]
                    if session_name == self.name or session_name.startswith(prefix + "_") or session_name == prefix:
                        sessions.append(session_id)
            return sessions
        except Exception as exc:
            self.logger.debug("failed to list screen sessions: %s", exc)
            return []

    def _cleanup_screen_state(self) -> None:
        if not self.use_screen or IS_WINDOWS:
            return

        for session_id in self._list_matching_screen_sessions():
            self.logger.debug("sending quit to stale screen session %s", session_id)
            try:
                subprocess.run(["screen", "-S", session_id, "-X", "quit"], check=False)
            except Exception as exc:
                self.logger.debug("failed to quit screen session %s: %s", session_id, exc)

        try:
            subprocess.run(["screen", "-wipe"], check=False)
        except Exception as exc:
            self.logger.debug("failed to wipe screen sockets: %s", exc)

    def start(self) -> None:
        cmd = self._get_command()
        self.logger.info("starting script with command: %s", cmd)

        try:
            self._cleanup_screen_state()
            popen_kwargs = self._get_popen_kwargs()
            self._process = subprocess.Popen(cmd, cwd=self.cwd, **popen_kwargs)
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
            self._cleanup_screen_state()
            self._process = None

    def _terminate_process(self) -> None:
        if not self._process:
            return

        if self.use_screen:
            self.logger.info("terminating screen session %s", self.name)
            self._cleanup_screen_state()

        self._signal_process_group(signal.SIGTERM if not IS_WINDOWS else signal.CTRL_BREAK_EVENT)
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.logger.warning("process did not exit, terminating")
            self._signal_process_group(signal.SIGTERM if not IS_WINDOWS else signal.CTRL_BREAK_EVENT)
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.logger.warning("process did not terminate, killing")
                self._kill_process_group()

    def _signal_process_group(self, sig: int) -> None:
        """send a signal to the process group."""
        if not self._process:
            return
        try:
            if IS_WINDOWS:
                self._process.send_signal(sig)
            else:
                # send signal to entire process group
                os.killpg(self._process.pid, sig)
        except (ProcessLookupError, OSError) as exc:
            self.logger.debug("failed to send signal %s to process group: %s", sig, exc)

    def _kill_process_group(self) -> None:
        """forcefully kill the process group."""
        if not self._process:
            return
        try:
            if IS_WINDOWS:
                self._process.kill()
            else:
                os.killpg(self._process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError) as exc:
            self.logger.debug("failed to kill process group: %s", exc)

    def force_stop(self) -> None:
        if self._process:
            self.logger.warning("force killing process %s and its children", self.name)
            self._kill_process_group()
