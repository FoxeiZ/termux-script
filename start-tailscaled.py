import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from queue import Empty

import requests

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Tailscaled(subprocess.Popen):
    def __init__(self, home_dir: Path | str):
        logging.debug("tailscaled process started")
        logging.debug("initializing Tailscaled with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir).absolute()
        self.stopped = False

        self.logging_file = self.home_dir / "tailscaled.log"
        self.tailscaled_bin = self.home_dir / "tailscaled"
        self.tailscale_bin = self.home_dir / "tailscale"
        self.tailscale_socket = self.home_dir / "tailscaled.sock"

        if not self.tailscaled_bin.exists() or not self.tailscale_bin.exists():
            logging.debug("missing required files, attempting download")
            self._download_tailscale_binaries()

        # check again after download attempt
        missing_files = []
        if not self.tailscaled_bin.exists():
            missing_files.append(str(self.tailscaled_bin))
        if not self.tailscale_bin.exists():
            missing_files.append(str(self.tailscale_bin))
        if missing_files:
            logging.error("missing required files: %s", ", ".join(missing_files))
            raise FileNotFoundError(
                f"missing required files: {', '.join(missing_files)}"
            )

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        args = [
            "sudo",  # run with elevated privileges
            self.tailscaled_bin.as_posix(),
            "--statedir=" + str(self.home_dir / "state"),
            "--socket=" + str(self.tailscale_socket),
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
        )
        logging.debug("tailscaled initialized successfully")

    def _download_tailscale_binaries(self):
        """download and extract tailscale binaries"""
        try:
            logging.debug(
                "fetching tailscale version from https://pkgs.tailscale.com/stable"
            )

            response = requests.get("https://pkgs.tailscale.com/stable", timeout=30)
            response.raise_for_status()

            version_match = re.search(r"<option[^>]*>([\d.]+)", response.text)
            if not version_match:
                raise Exception("could not find version number in response")

            version = version_match.group(1)
            logging.debug("found tailscale version: %s", version)

            download_url = (
                f"https://pkgs.tailscale.com/stable/tailscale_{version}_arm64.tgz"
            )
            logging.debug("downloading from: %s", download_url)

            tar_file = self.home_dir / f"tailscale_{version}_arm64.tgz"

            self.home_dir.mkdir(parents=True, exist_ok=True)
            with requests.get(download_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tar_file, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            logging.debug("download completed, extracting to: %s", self.home_dir)

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
            logging.debug("extraction completed successfully")
            tar_file.unlink()
            logging.debug("cleaned up downloaded tar file")

        except requests.RequestException as e:
            logging.error("network error during download: %s", e)
            raise Exception(f"failed to download tailscale: {e}")

        except subprocess.CalledProcessError as e:
            logging.error("tar extraction failed: %s", e.stderr)
            raise Exception(f"failed to extract tailscale: {e}")

        except Exception as e:
            logging.error("unexpected error during download: %s", e)
            raise

    def stdout_reader(self):
        logging.debug("starting output reader thread")
        stdout = self.stdout  # cache reference
        if stdout is None:
            return

        while not self.stopped:
            line = stdout.readline(1)
            if not line:
                break

            print(
                line.decode("utf-8", errors="surrogateescape")
                if isinstance(line, bytes)
                else line,
                end="",
            )

        logging.debug("output reader thread stopped")

    def bring_up_connection(self):
        logging.debug("bringing up connection")
        try:
            subprocess.run(
                [
                    "sudo",  # run with elevated privileges
                    self.tailscale_bin,
                    "--socket",
                    str(self.tailscale_socket),
                    "up",
                    "--authkey",
                    os.getenv("TAILSCALE_AUTHKEY") or "",
                    "--advertise-exit-node",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logging.error("failed to bring up connection: %s", e)
            logging.error("tailscale up stderr: %s", e.stderr)
            logging.error("tailscale up stdout: %s", e.stdout)
            raise RuntimeError("failed to bring up connection")

    def wait_for_connection(self, timeout: int = 60):
        logging.debug("waiting for connection with timeout: %d seconds", timeout)
        pattern = re.compile(r"magicsock.*connected")
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

                if pattern.search(
                    line.decode("utf-8", errors="surrogateescape")
                    if isinstance(line, bytes)
                    else line
                ):
                    logging.debug("connection established")
                    return True
                else:
                    time.sleep(0.1)
                    logging.debug(line.strip())
            except Empty:
                if self.poll() is not None:
                    raise Exception("tailscaled stopped unexpectedly")
                continue

        return False

    def _graceful_stop(self, timeout: int = 15):
        logging.debug("attempting graceful stop")
        self.send_signal(signal.SIGINT)
        try:
            self.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            logging.debug("graceful stop timed out")
            return False

    def __call_check(self, *args):
        logging.debug("calling command: %s", " ".join(args))
        process = subprocess.run(args)
        if process.returncode == 0:
            return True
        else:
            return False

    def _pkill_stop(self):
        logging.debug("attempting pkill stop")
        return self.__call_check(
            "sudo",
            "pkill",
            "-f",
            "tailscaled.*userspace-networking.*",
        )

    def _kill_stop(self):
        logging.debug("attempting calling kill")
        return self.__call_check("sudo", "kill", str(self.pid))

    def stop(self, timeout: int = 15):
        if self.stopped:
            return

        logging.debug("stopping Tailscaled process with timeout: %d seconds", timeout)

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
                        logging.debug("stop method %s succeeded", cb.__name__)
                        break
                    else:
                        logging.debug(
                            "stop method %s did not stop the process", cb.__name__
                        )

            except PermissionError:
                pass

            except Exception as e:
                logging.debug("stop method %s raised exception: %s", cb.__name__, e)
                continue

            logging.debug("stop method %s failed, trying next", cb.__name__)

        self.stopped = True
        logging.debug("tailscaled process stopped")


class Socatd(subprocess.Popen):
    def __init__(self):
        logging.debug("initializing Socatd")
        self.started = False
        self.stopped = False
        logging.debug("socatd initialized successfully")

    def start(self):
        logging.debug("starting Socatd process")
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
        logging.debug("socatd process started")

    def stop(self, timeout: int = 15):
        if self.stopped or not self.started:
            return

        logging.debug("stopping Socatd process with timeout: %d seconds", timeout)
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed

        self.stopped = True
        logging.debug("socatd process stopped")


class Manager:
    def __init__(self, home_dir: Path | str):
        logging.debug("initializing Manager with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir)
        self.tailscaled = Tailscaled(self.home_dir)
        self.socatd = Socatd()
        logging.debug("manager initialized successfully")

    def start(self):
        logging.debug("starting Manager")
        if not self.tailscaled.wait_for_connection():
            raise Exception("tailscaled failed to connect")

        self.tailscaled.bring_up_connection()
        self.socatd.start()
        logging.debug("manager started successfully")

        # main loop start here
        self.tailscaled.stdout_reader()

    def stop(self):
        logging.debug("stopping Manager")
        self.socatd.stop()
        self.tailscaled.stop()
        logging.debug("manager stopped successfully")


if __name__ == "__main__":
    logging.debug("script started")
    manager = Manager(sys.argv[1] if len(sys.argv) > 1 else "~/.tailscale")
    try:
        manager.start()
        logging.debug("press Ctrl+C to stop")
    except KeyboardInterrupt:
        logging.debug("keyboardInterrupt received")
    finally:
        manager.stop()
