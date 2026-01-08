from __future__ import annotations

import contextlib
import re
import signal
import subprocess
import time
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING

import requests
from lib.plugin import Plugin
from lib.utils import get_logger

if TYPE_CHECKING:
    from lib.manager import PluginManager


class Tailscaled(subprocess.Popen[bytes]):
    def __init__(self, home_dir: Path | str, auth_key: str = ""):
        self.logger = get_logger("TailscaledProcess")
        self.logger.debug("tailscaled process started")
        self.logger.debug("initializing Tailscaled with home_dir: %s", home_dir)

        self.home_dir = Path(home_dir).absolute()
        self.auth_key = auth_key
        self.stopped = False

        self.logging_file = self.home_dir / "tailscaled.log"
        self.tailscaled_bin = self.home_dir / "tailscaled"
        self.tailscale_bin = self.home_dir / "tailscale"

        if not self.tailscaled_bin.exists() or not self.tailscale_bin.exists():
            self.logger.debug("missing required files, attempting download")
            self._download_tailscale_binaries()

        # check again after download attempt
        missing_files: list[str] = []
        if not self.tailscaled_bin.exists():
            missing_files.append(str(self.tailscaled_bin))
        if not self.tailscale_bin.exists():
            missing_files.append(str(self.tailscale_bin))
        if missing_files:
            self.logger.error("missing required files: %s", ", ".join(missing_files))
            raise FileNotFoundError(f"missing required files: {', '.join(missing_files)}")

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        args = [
            "sudo",  # run with elevated privileges
            self.tailscaled_bin.as_posix(),
            "--statedir=" + str(self.home_dir / "state"),
            "--socket=" + str(self.home_dir / "tailscaled.sock"),
            "--tun=userspace-networking",
            "--socks5-server=localhost:1055",
            "--outbound-http-proxy-listen=localhost:1055",
        ]

        super().__init__(
            args,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            cwd=self.home_dir,
        )
        self.logger.debug("tailscaled initialized successfully")

    def _download_tailscale_binaries(self):
        """download and extract tailscale binaries"""
        try:
            self.logger.debug("fetching tailscale version from https://pkgs.tailscale.com/stable")

            response = requests.get("https://pkgs.tailscale.com/stable", timeout=30)
            response.raise_for_status()

            version_match = re.search(r"<option[^>]*>([\d.]+)", response.text)
            if not version_match:
                raise Exception("could not find version number in response")

            version = version_match.group(1)
            self.logger.debug("found tailscale version: %s", version)

            download_url = f"https://pkgs.tailscale.com/stable/tailscale_{version}_arm64.tgz"
            self.logger.debug("downloading from: %s", download_url)

            tar_file = self.home_dir / f"tailscale_{version}_arm64.tgz"

            self.home_dir.mkdir(parents=True, exist_ok=True)
            with requests.get(download_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with Path(tar_file).open("wb") as f:
                    f.writelines(r.iter_content(chunk_size=8192))

            self.logger.debug("download completed, extracting to: %s", self.home_dir)

            subprocess.run(
                [
                    "tar",
                    "xfz",
                    str(tar_file),
                    "-C",
                    str(self.home_dir),
                    "--strip-components=1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.logger.debug("extraction completed successfully")
            tar_file.unlink()
            self.logger.debug("cleaned up downloaded tar file")

        except requests.RequestException as e:
            self.logger.error("network error during download: %s", e)
            raise Exception(f"failed to download tailscale: {e}") from e

        except subprocess.CalledProcessError as e:
            self.logger.error("tar extraction failed: %s", e.stderr)
            raise Exception(f"failed to extract tailscale: {e}") from e

        except Exception as e:
            self.logger.error("unexpected error during download: %s", e)
            raise

    def stdout_reader(self):
        self.logger.debug("starting output reader thread")
        stdout = self.stdout  # cache reference
        if stdout is None:
            return

        while not self.stopped:
            line = stdout.readline(1)
            if not line:
                break

            self.logger.debug(
                line.decode("utf-8", errors="surrogateescape") if isinstance(line, bytes) else line,
            )

        self.logger.debug("output reader thread stopped")

    def bring_up_connection(self):
        self.logger.debug("bringing up connection")
        sp = subprocess.run(
            [
                "sudo",  # run with elevated privileges
                self.tailscale_bin,
                "up",
                "--authkey",
                self.auth_key,
                "--advertise-exit-node",
            ],
            check=True,
            cwd=self.home_dir,
        )
        if sp.returncode != 0:
            raise Exception("failed to bring up connection")

    def wait_for_connection(self, timeout: int = 60):
        self.logger.debug("waiting for connection with timeout: %d seconds", timeout)
        # pattern = re.compile(r"magicsock.*connected")
        start_time = time.time()

        stdout = self.stdout
        if stdout is None:
            raise Exception("stdout is None")

        while time.time() - start_time < timeout:
            try:
                line = stdout.readline()
                if not line.strip():
                    time.sleep(0.1)
                    continue

                if "bootstrap dial succeeded" in (
                    line.decode("utf-8", errors="surrogateescape") if isinstance(line, bytes) else line
                ):
                    self.logger.debug("connection established")
                    return True
                else:
                    time.sleep(0.1)
                    self.logger.debug(line.strip())
            except Empty:
                if self.poll() is not None:
                    raise Exception("tailscaled stopped unexpectedly") from None
                continue

        return False

    def _graceful_stop(self, timeout: int = 15):
        self.logger.debug("attempting graceful stop")
        self.send_signal(signal.SIGINT)
        try:
            self.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            self.logger.debug("graceful stop timed out")
            return False

    def __call_check(self, *args: str) -> bool:
        self.logger.debug("calling command: %s", " ".join(args))
        process = subprocess.run(args, check=False)
        return process.returncode == 0

    def _pkill_stop(self):
        self.logger.debug("attempting pkill stop")
        return self.__call_check(
            "sudo",
            "pkill",
            "-f",
            "tailscaled.*userspace-networking.*",
        )

    def _kill_stop(self):
        self.logger.debug("attempting calling kill")
        return self.__call_check("sudo", "kill", str(self.pid))

    def stop(self, timeout: int = 15):
        if self.stopped:
            return

        self.logger.debug("stopping Tailscaled process with timeout: %d seconds", timeout)

        for cb in [self._graceful_stop, self._pkill_stop, self._kill_stop]:
            try:
                if cb():
                    # run poll check first
                    # if still running, wait for timeout
                    # then check again
                    if self.poll() is None:
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            self.wait(timeout=timeout)

                    if self.poll() is not None:
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

        self.stopped = True
        self.logger.debug("tailscaled process stopped")


class Socatd(subprocess.Popen[bytes]):
    def __init__(self):
        self.logger = get_logger("SocatdProcess")

        self.logger.debug("initializing Socatd")
        self.started = False
        self.stopped = False
        self.logger.debug("socatd initialized successfully")

    def start(self):
        self.logger.debug("starting Socatd process")
        self.args = [
            "socat",
            "TCP-LISTEN:0,reuseaddr,fork",
            "SOCKS5:127.0.0.1:,socksport=8055",
        ]

        super().__init__(
            self.args,
            start_new_session=True,
        )
        self.started = True
        self.logger.debug("socatd process started")

    def stop(self, timeout: int = 15):
        if self.stopped or not self.started:
            return

        self.logger.debug("stopping Socatd process with timeout: %d seconds", timeout)
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed

        self.stopped = True
        self.logger.debug("socatd process stopped")


class TailscaledPlugin(Plugin):
    def __init__(
        self,
        manager: PluginManager,
        webhook_url: str = "",
        home_dir: Path | str = "./tailscale_data",
        auth_key: str = "",
    ):
        super().__init__(manager, webhook_url)

        self.logger.debug("initializing with home_dir: %s", home_dir)
        self.tailscaled = Tailscaled(home_dir, auth_key)
        self.socatd = Socatd()
        self.logger.debug("manager initialized successfully")

    def start(self):
        self.logger.debug("starting Manager")
        if not self.tailscaled.wait_for_connection():
            raise Exception("tailscaled failed to connect")

        self.tailscaled.bring_up_connection()
        self.socatd.start()
        self.logger.debug("manager started successfully")

        # main loop start here
        self.tailscaled.stdout_reader()

    def stop(self):
        self.logger.debug("stopping Manager")
        self.socatd.stop()
        self.tailscaled.stop()
        self.logger.debug("manager stopped successfully")
