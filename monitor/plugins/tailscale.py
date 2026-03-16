from __future__ import annotations

import asyncio
import contextlib
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from lib.plugin import Plugin

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import PluginMetadata
    from lib.worker import PluginManager


class Tailscaled:
    def __init__(
        self,
        logger: Logger,
        home_dir: Path | str,
        auth_key: str = "",
        upgrade_check: bool = False,
    ):
        self.logger = logger
        self.home_dir = Path(home_dir).absolute()
        self.auth_key = auth_key
        self.upgrade_check = upgrade_check
        self.stopped = False
        self.process: asyncio.subprocess.Process | None = None
        self.connected_event = asyncio.Event()

        self.logging_file = self.home_dir / "tailscaled.log"
        self.tailscaled_bin = self.home_dir / "tailscaled"
        self.tailscale_bin = self.home_dir / "tailscale"

    def cleanup(self):
        if self.process:
            with contextlib.suppress(Exception):
                if self.process.stdin:
                    self.process.stdin.close()

    async def _get_latest_version(self) -> str:
        self.logger.debug("fetching tailscale version from https://pkgs.tailscale.com/stable")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get("https://pkgs.tailscale.com/stable")
            response.raise_for_status()

        version_match = re.search(r"<option[^>]*>([\d.]+)", response.text)
        if not version_match:
            raise RuntimeError("could not find version number in response")

        version = version_match.group(1)
        self.logger.debug("found tailscale version: %s", version)
        return version

    @staticmethod
    def _parse_version(version: str) -> tuple[int, ...]:
        parts = version.split(".")
        return tuple(int(part) for part in parts if part.isdigit())

    async def _get_installed_version(self) -> str | None:
        self.logger.debug("checking installed tailscale version")
        proc = await asyncio.create_subprocess_exec(
            self.tailscale_bin.as_posix(),
            "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.home_dir,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode != 0:
            self.logger.warning("failed to read installed tailscale version")
            return None

        match = re.search(r"([\d]+\.[\d]+\.[\d]+)", stdout.decode(errors="ignore"))
        if not match:
            self.logger.warning("could not parse installed tailscale version")
            return None

        version = match.group(1)
        self.logger.debug("installed tailscale version: %s", version)
        return version

    async def check_for_update(self) -> bool:
        try:
            latest_version = await self._get_latest_version()
            installed_version = await self._get_installed_version()

            if installed_version is None:
                self.logger.info("installed tailscale version is unknown, running upgrade")
                return True

            has_update = self._parse_version(latest_version) > self._parse_version(installed_version)
            if has_update:
                self.logger.info("tailscale update available: %s -> %s", installed_version, latest_version)
            else:
                self.logger.debug("tailscale is up to date: %s", installed_version)
            return has_update
        except Exception as e:
            self.logger.warning("tailscale update check failed: %s", e)
            return False

    async def upgrade(self) -> bool:
        if not await self.check_for_update():
            self.logger.debug("tailscale upgrade skipped")
            return False

        try:
            await self._download_tailscale_binaries()
            self.logger.info("tailscale binaries upgraded successfully")
            return True
        except Exception as e:
            self.logger.warning("tailscale upgrade failed: %s", e)
            return False

    async def _download_tailscale_binaries(self):
        """download and extract tailscale binaries"""
        try:
            version = await self._get_latest_version()

            download_url = f"https://pkgs.tailscale.com/stable/tailscale_{version}_arm64.tgz"
            self.logger.debug("downloading from: %s", download_url)

            tar_file = self.home_dir / f"tailscale_{version}_arm64.tgz"

            self.home_dir.mkdir(parents=True, exist_ok=True)

            async with httpx.AsyncClient(timeout=60.0) as client, client.stream("GET", download_url) as r:
                r.raise_for_status()
                with tar_file.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            self.logger.debug("download completed, extracting to: %s", self.home_dir)

            proc = await asyncio.create_subprocess_exec(
                "tar",
                "xfz",
                str(tar_file),
                "-C",
                str(self.home_dir),
                "--strip-components=1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.logger.error("tar extraction failed: %s", stderr.decode(errors="ignore"))
                raise Exception(f"failed to extract tailscale: {stderr.decode(errors='ignore')}")

            self.logger.debug("extraction completed successfully")
            tar_file.unlink()
            self.logger.debug("cleaned up downloaded tar file")

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            self.logger.error("network error during download: %s", e)
            raise Exception(f"failed to download tailscale: {e}") from e
        except Exception as e:
            self.logger.error("unexpected error during download: %s", e)
            raise

    async def start(self) -> None:
        self.logger.debug("tailscaled process started")
        self.logger.debug("initializing Tailscaled with home_dir: %s", self.home_dir)

        if not self.tailscaled_bin.exists() or not self.tailscale_bin.exists():
            self.logger.debug("missing required files, attempting download")
            await self._download_tailscale_binaries()

        missing_files: list[str] = []
        if not self.tailscaled_bin.exists():
            missing_files.append(str(self.tailscaled_bin))
        if not self.tailscale_bin.exists():
            missing_files.append(str(self.tailscale_bin))
        if missing_files:
            self.logger.error("missing required files: %s", ", ".join(missing_files))
            raise FileNotFoundError(f"missing required files: {', '.join(missing_files)}")

        if self.upgrade_check:
            self.logger.debug("tailscale upgrade check is enabled")
            with contextlib.suppress(Exception):
                await self.upgrade()

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        args = [
            self.tailscaled_bin.as_posix(),
            "--statedir=" + str(self.home_dir / "state"),
            "--socket=" + str(self.home_dir / "tailscaled.sock"),
            "--tun=userspace-networking",
            "--socks5-server=localhost:1055",
            "--outbound-http-proxy-listen=localhost:1055",
        ]

        self.process = await asyncio.create_subprocess_exec(
            *args,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.home_dir,
        )
        self.logger.debug("tailscaled initialized successfully")

    async def stdout_reader(self):
        self.logger.debug("starting output reader task")
        if not self.process or not self.process.stdout:
            return

        while not self.stopped and self.process.returncode is None:
            try:
                line_bytes = await self.process.stdout.readline()
                if not line_bytes:
                    break

                line_str = line_bytes.decode("utf-8", errors="surrogateescape").strip()
                if line_str:
                    self.logger.debug(line_str)

                    if "bootstrap dial succeeded" in line_str:
                        self.connected_event.set()
            except asyncio.CancelledError:
                self.logger.debug("output reader task cancelled")
                break
            except Exception:
                break

        self.logger.debug("output reader task stopped")

    async def bring_up_connection(self):
        self.logger.debug("bringing up connection")
        proc = await asyncio.create_subprocess_exec(
            self.tailscale_bin.as_posix(),
            "up",
            "--authkey",
            self.auth_key,
            "--advertise-exit-node",
            cwd=self.home_dir,
        )

        try:
            await asyncio.wait_for(proc.wait(), timeout=30.0)
        except TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            raise Exception(f"failed to bring up connection, code: {proc.returncode}")

    async def wait_for_connection(self, timeout: int = 60) -> bool:
        """Wait for the stdout_reader to find the success log."""
        self.logger.debug("waiting for connection with timeout: %d seconds", timeout)
        try:
            await asyncio.wait_for(self.connected_event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def __call_check(self, *args: str) -> bool:
        self.logger.debug("calling command: %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(*args)
        await proc.wait()
        return proc.returncode == 0

    async def _graceful_stop(self, timeout: int = 15) -> bool:
        if not self.process:
            return False

        self.logger.debug("attempting graceful stop")
        self.process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=timeout)
            return True
        except TimeoutError:
            self.logger.debug("graceful stop timed out")
            return False

    async def _pkill_stop(self) -> bool:
        self.logger.debug("attempting pkill stop")
        return await self.__call_check(
            "pkill",
            "-f",
            "tailscaled.*userspace-networking.*",
        )

    async def _kill_stop(self) -> bool:
        if not self.process:
            return False
        self.logger.debug("attempting calling kill")
        return await self.__call_check("kill", str(self.process.pid))

    async def stop(self, timeout: int = 15):
        if self.stopped or not self.process:
            return

        self.logger.debug("stopping Tailscaled process with timeout: %d seconds", timeout)

        for cb in [self._graceful_stop, self._pkill_stop, self._kill_stop]:
            try:
                if await cb():
                    if self.process.returncode is None:
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(self.process.wait(), timeout=timeout)

                    if self.process.returncode is not None:
                        self.logger.debug("stop method %s succeeded", cb.__name__)
                        break
                    else:
                        self.logger.debug("stop method %s did not stop the process", cb.__name__)

            except PermissionError:
                pass
            except Exception as e:
                self.logger.debug("stop method %s raised exception: %s", cb.__name__, e)
                continue

            self.logger.debug("stop method %s failed, trying next", cb.__name__)

        self.cleanup()
        self.stopped = True
        self.logger.debug("tailscaled process stopped")


class Socatd:
    def __init__(self, logger: Logger):
        self.logger = logger
        self.started = False
        self.stopped = False
        self.process: asyncio.subprocess.Process | None = None
        self.logger.debug("socatd initialized successfully")

    def cleanup(self):
        if self.process:
            with contextlib.suppress(Exception):
                if self.process.stdin:
                    self.process.stdin.close()

    async def start(self):
        self.logger.debug("starting Socatd process")
        args = [
            "socat",
            "TCP-LISTEN:0,reuseaddr,fork",
            "SOCKS5:127.0.0.1:,socksport=8055",
        ]

        self.process = await asyncio.create_subprocess_exec(
            *args,
            start_new_session=True,
        )
        self.started = True
        self.logger.debug("socatd process started")

    async def stop(self, timeout: int = 15):
        if self.stopped or not self.started or not self.process:
            return

        self.logger.debug("stopping Socatd process with timeout: %d seconds", timeout)

        if self.process.returncode is None:  # if running
            self.process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=timeout)
            except TimeoutError:
                self.process.kill()  # force kill if sigint failed
                await self.process.wait()

        self.cleanup()
        self.stopped = True
        self.logger.debug("socatd process stopped")


class TailscaledPlugin(Plugin, requires_root=True):
    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ):
        super().__init__(manager, metadata, logger)

        home_dir = metadata.kwargs.get("home_dir", "./tailscale_data")
        auth_key = metadata.kwargs.get("auth_key", "")
        upgrade_check = metadata.kwargs.get("upgrade_check", False)

        self.logger.debug("initializing with home_dir: %s", home_dir)
        self.tailscaled = Tailscaled(
            self.logger.getChild("tailscaled"),
            home_dir,
            auth_key,
            upgrade_check,
        )
        self.socatd = Socatd(self.logger.getChild("socatd"))
        self.logger.debug("manager initialized successfully")

    async def start(self):
        self.logger.debug("starting manager")
        reader_task = None
        try:
            await self.tailscaled.start()

            reader_task = asyncio.create_task(self.tailscaled.stdout_reader())
            if not await self.tailscaled.wait_for_connection():
                raise Exception("tailscaled failed to connect")

            await self.tailscaled.bring_up_connection()
            await self.socatd.start()
            self.logger.debug("manager started successfully")

            stop_task = asyncio.create_task(self._stop_event.wait())
            _, pending = await asyncio.wait([reader_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()

        except asyncio.CancelledError:
            self.logger.info("tailscaled plugin task cancelled")
            raise
        except Exception as e:
            self.logger.error("failed to start tailscaled plugin: %s", e)
            raise
        finally:
            self.logger.debug("executing teardown sequence")
            if reader_task and not reader_task.done():
                reader_task.cancel()
            await self.socatd.stop()
            await self.tailscaled.stop()
            self.logger.debug("manager stopped successfully")
