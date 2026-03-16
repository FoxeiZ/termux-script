from __future__ import annotations

import asyncio
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

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class ScriptPlugin(Plugin, restart_on_failure=True):
    if TYPE_CHECKING:
        script_path: str
        args: list[str]
        use_screen: bool
        _process: asyncio.subprocess.Process | None

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
            return ["screen", "-D", "-m", "-S", self.name, *cmd]
        return cmd

    def _get_popen_kwargs(self) -> dict[str, Any]:
        if IS_WINDOWS:
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @property
    def _screen_base_name(self) -> str:
        return re.sub(r"_[0-9a-f]{6,}$", "", self.name)

    def _is_matching_screen_name(self, name: str) -> bool:
        prefix = self._screen_base_name
        return name in (self.name, prefix) or name.startswith(prefix + "_")

    def _get_screen_dir(self) -> Path | None:
        screendir = os.environ.get("SCREENDIR")
        if screendir:
            return Path(screendir)
        candidate = Path.home() / ".screen"
        return candidate if candidate.is_dir() else None

    async def _list_matching_screen_sessions(self) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "screen",
                "-ls",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            result_text = stdout.decode(errors="ignore")
        except Exception as exc:
            self.logger.debug("failed to list screen sessions: %s", exc)
            return []

        sessions: list[str] = []
        for line in result_text.splitlines():
            m = re.match(r"^\s+(\d+\.(\S+))", line)
            if m and self._is_matching_screen_name(m.group(2)):
                sessions.append(m.group(1))
        return sessions

    async def _cleanup_screen_state(self) -> None:
        if not self.use_screen or IS_WINDOWS:
            return

        for session_id in await self._list_matching_screen_sessions():
            self.logger.debug("quitting stale screen session %s", session_id)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "screen",
                    "-S",
                    session_id,
                    "-X",
                    "quit",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except Exception as exc:
                self.logger.debug("failed to quit screen session %s: %s", session_id, exc)

        try:
            proc = await asyncio.create_subprocess_exec(
                "screen",
                "-wipe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as exc:
            self.logger.debug("failed to wipe screen sockets: %s", exc)

        screen_dir = self._get_screen_dir()
        if not screen_dir:
            return

        for socket_file in screen_dir.iterdir():
            pid_name = socket_file.name.split(".", 1)
            if len(pid_name) == 2 and self._is_matching_screen_name(pid_name[1]):
                try:
                    socket_file.unlink()
                    self.logger.debug("removed stale screen socket: %s", socket_file.name)
                except OSError as exc:
                    self.logger.debug("failed to remove screen socket %s: %s", socket_file.name, exc)

    async def start(self) -> None:
        cmd = self._get_command()
        self.logger.info("starting script with command: %s", cmd)

        try:
            await self._cleanup_screen_state()
            popen_kwargs = self._get_popen_kwargs()

            self._process = await asyncio.create_subprocess_exec(cmd[0], *cmd[1:], cwd=self.cwd, **popen_kwargs)

            process_task = asyncio.create_task(self._process.wait())
            stop_task = asyncio.create_task(self._stop_event.wait())

            done, pending = await asyncio.wait([process_task, stop_task], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

            if stop_task in done:
                self.logger.info("stopping %s process", self.name)
                await self._terminate_process()
            else:
                returncode = self._process.returncode
                self.logger.warning("script %s self-exited with code %s", self.name, returncode)
                if self.restart_on_failure and returncode != 0:
                    raise RuntimeError(f"script {self.name} exited unexpectedly")

        except asyncio.CancelledError:
            self.logger.info("script plugin %s task was cancelled", self.name)
            await self._terminate_process()
            raise
        finally:
            await self._cleanup_screen_state()
            self._process = None

    async def _terminate_process(self) -> None:
        if not self._process:
            return

        if self.use_screen:
            self.logger.info("terminating screen session %s", self.name)
            await self._cleanup_screen_state()

        self._signal_process_group(signal.SIGTERM if not IS_WINDOWS else signal.CTRL_BREAK_EVENT)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except TimeoutError:
            self.logger.warning("process did not exit, terminating")
            self._signal_process_group(signal.SIGTERM if not IS_WINDOWS else signal.CTRL_BREAK_EVENT)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except TimeoutError:
                self.logger.warning("process did not terminate, killing")
                self._kill_process_group()

    def _signal_process_group(self, sig: int) -> None:
        if not self._process:
            return
        try:
            if IS_WINDOWS:
                self._process.send_signal(sig)
            else:
                os.killpg(self._process.pid, sig)
        except (ProcessLookupError, OSError) as exc:
            self.logger.debug("failed to send signal %s to process group: %s", sig, exc)

    def _kill_process_group(self) -> None:
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
        super().force_stop()
