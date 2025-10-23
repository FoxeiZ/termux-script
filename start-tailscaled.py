import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from queue import Empty

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Tailscaled(subprocess.Popen):
    def __init__(self, home_dir: Path | str):
        logging.debug("Tailscaled process started")
        logging.debug("Initializing Tailscaled with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir).absolute()
        self.stopped = False

        # find binary  # TODO: maybe auto download?
        self.logging_file = self.home_dir / "tailscaled.log"
        self.tailscaled_bin = self.home_dir / "tailscaled"
        self.tailscale_bin = self.home_dir / "tailscale"
        missing_files = []
        if not self.tailscaled_bin.exists():
            missing_files.append(str(self.tailscaled_bin))
        if not self.tailscale_bin.exists():
            missing_files.append(str(self.tailscale_bin))
        if missing_files:
            logging.error("Missing required files: %s", ", ".join(missing_files))
            raise FileNotFoundError(
                f"Missing required files: {', '.join(missing_files)}"
            )

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        args = [
            "sudo",  # run with elevated privileges
            self.tailscaled_bin.as_posix(),
            "--statedir=" + str(self.home_dir / "state"),
            # "--socket=" + str(self.home_dir / "tailscaled.sock"),
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
        logging.debug("Tailscaled initialized successfully")

    def stdout_reader(self):
        logging.debug("Starting output reader thread")
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

        logging.debug("Output reader thread stopped")

    def bring_up_connection(self):
        logging.debug("Bringing up connection")
        sp = subprocess.run(
            [
                "sudo",  # run with elevated privileges
                str(self.home_dir / "tailscale"),
                "up",
                "--authkey",
                os.getenv("TAILSCALE_AUTHKEY") or "",
            ],
            check=True,
        )
        if sp.returncode != 0:
            raise Exception("Failed to bring up connection")

    def wait_for_connection(self, timeout: int = 60):
        logging.debug("Waiting for connection with timeout: %d seconds", timeout)
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
                    logging.debug("Connection established")
                    return True
                else:
                    time.sleep(0.1)
                    logging.debug(line.strip())
            except Empty:
                if self.poll() is not None:
                    raise Exception("Tailscaled stopped unexpectedly")
                continue

        return False

    def _graceful_stop(self, timeout: int = 15):
        logging.debug("Attempting graceful stop")
        self.send_signal(signal.SIGINT)
        try:
            self.wait(timeout=timeout)
            logging.debug("Graceful stop successful")
            return True
        except subprocess.TimeoutExpired:
            logging.debug("Graceful stop timed out")
            return False

    def __call_check(self, *args):
        process = subprocess.run(args)
        if process.returncode == 0:
            return True
        else:
            return False

    def _pkill_stop(self):
        logging.debug("Attempting pkill stop")
        return self.__call_check("sudo", "pkill", "tailscaled")

    def _kill_stop(self):
        logging.debug("Attempting calling kill")
        return self.__call_check("sudo", "kill", str(self.pid))

    def stop(self, timeout: int = 15):
        if self.stopped:
            return

        logging.debug("Stopping Tailscaled process with timeout: %d seconds", timeout)

        for cb in [self._graceful_stop, self._pkill_stop, self._kill_stop]:
            if cb():
                break
            logging.debug("Stop method %s failed, trying next", cb.__name__)

        self.stopped = True
        logging.debug("Tailscaled process stopped")


class Socatd(subprocess.Popen):
    def __init__(self):
        logging.debug("Initializing Socatd")
        self.started = False
        self.stopped = False
        logging.debug("Socatd initialized successfully")

    def start(self):
        logging.debug("Starting Socatd process")
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
        logging.debug("Socatd process started")

    def stop(self, timeout: int = 15):
        if self.stopped or not self.started:
            return

        logging.debug("Stopping Socatd process with timeout: %d seconds", timeout)
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed

        self.stopped = True
        logging.debug("Socatd process stopped")


class Manager:
    def __init__(self, home_dir: Path | str):
        logging.debug("Initializing Manager with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir)
        self.tailscaled = Tailscaled(self.home_dir)
        self.socatd = Socatd()
        logging.debug("Manager initialized successfully")

    def start(self):
        logging.debug("Starting Manager")
        if not self.tailscaled.wait_for_connection():
            raise Exception("Tailscaled failed to connect")

        self.tailscaled.bring_up_connection()
        self.socatd.start()
        logging.debug("Manager started successfully")

        # main loop start here
        self.tailscaled.stdout_reader()

    def stop(self):
        logging.debug("Stopping Manager")
        self.socatd.stop()
        self.tailscaled.stop()
        logging.debug("Manager stopped successfully")


if __name__ == "__main__":
    logging.debug("Script started")
    manager = Manager(sys.argv[1] if len(sys.argv) > 1 else "~/.tailscale")
    try:
        manager.start()
        logging.debug("Press Ctrl+C to stop")
    except KeyboardInterrupt:
        logging.debug("KeyboardInterrupt received")
    finally:
        manager.stop()
