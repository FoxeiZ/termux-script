from pathlib import Path
from queue import Empty
import os
import re
import signal
import subprocess
import time


from ..lib.plugin import DaemonPlugin
from ..lib.manager import PluginManager, get_logger


logger = get_logger("TailscalePlugin")


class Tailscaled(subprocess.Popen):
    def __init__(self, home_dir: Path | str):
        logger.debug("Initializing Tailscaled with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir).absolute()
        self.started = False
        self.stopped = False
        self.is_up = False

        # find binary  # TODO: maybe auto download?
        tailscaled_bin = self.home_dir / "tailscaled"
        if not tailscaled_bin.exists():
            logger.error("%s not found", tailscaled_bin)
            raise FileNotFoundError(f"{tailscaled_bin} not found")

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        self.logging_file = self.home_dir / "tailscaled.log"
        logger.debug("Tailscaled initialized successfully")

    def stdout_reader(self):
        logger.debug("Starting output reader thread")
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

        logger.debug("Output reader thread stopped")

    def bring_up_connection(self):
        logger.debug("Bringing up connection")
        sp = subprocess.run(
            [
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
        logger.debug("Waiting for connection with timeout: %d seconds", timeout)
        pattern = re.compile(r"magicsock.*connected")
        start_time = time.time()

        stdout = self.stdout
        if stdout is None:
            raise Exception("stdout is None")

        while time.time() - start_time < timeout:
            try:
                line = stdout.readline()
                if pattern.search(
                    line.decode("utf-8", errors="surrogateescape")
                    if isinstance(line, bytes)
                    else line
                ):
                    logger.debug("Connection established")
                    return True
            except Empty:
                if self.poll() is not None:
                    raise Exception("Tailscaled stopped unexpectedly")
                continue

        return False

    def start(self):
        logger.debug("Starting Tailscaled process")
        self.args = [
            str(self.home_dir / "tailscaled"),
            "--statedir=" + str(self.home_dir / "state"),
            # "--socket=" + str(self.home_dir / "tailscaled.sock"),
            "--tun=userspace-networking",
            "--socks5-server=localhost:1055",
            "--outbound-http-proxy-listen=localhost:1055",
        ]

        super().__init__(
            self.args,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        self.started = True
        logger.debug("Tailscaled process started")

    def stop(self, timeout: int = 15):
        if self.stopped or not self.started:
            return

        logger.debug("Stopping Tailscaled process with timeout: %d seconds", timeout)
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed

        self.stopped = True
        logger.debug("Tailscaled process stopped")


class Socatd(subprocess.Popen):
    def __init__(self):
        logger.debug("Initializing Socatd")
        self.started = False
        self.stopped = False
        logger.debug("Socatd initialized successfully")

    def start(self):
        logger.debug("Starting Socatd process")
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
        logger.debug("Socatd process started")

    def stop(self, timeout: int = 15):
        if self.stopped or not self.started:
            return

        logger.debug("Stopping Socatd process with timeout: %d seconds", timeout)
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed

        self.stopped = True
        logger.debug("Socatd process stopped")


class TailscalePlugin(DaemonPlugin):
    def __init__(
        self,
        manager: PluginManager,
        webhook_url: str = "",
        home_dir: Path | str = "~/.tailscale",
    ):
        super().__init__(manager, webhook_url=webhook_url)

        logger.debug("Initializing Manager with home_dir: %s", home_dir)
        self.home_dir = Path(home_dir)
        self.tailscaled = Tailscaled(self.home_dir)
        self.socatd = Socatd()
        logger.debug("Manager initialized successfully")

    def start(self):
        logger.debug("Starting Manager")
        self.tailscaled.start()
        if not self.tailscaled.wait_for_connection():
            raise Exception("Tailscaled failed to connect")

        self.tailscaled.bring_up_connection()
        self.socatd.start()
        logger.debug("Manager started successfully")

        # main loop start here
        self.tailscaled.stdout_reader()

    def stop(self):
        logger.debug("Stopping Manager")
        self.socatd.stop()
        self.tailscaled.stop()
        logger.debug("Manager stopped successfully")


# if __name__ == "__main__":
#     logger.debug("Script started")
#     if not os.getuid() == 0:  # type: ignore
#         logger.error("Please run as root")
#         sys.exit(1)

#     manager = TailscalePlugin(sys.argv[1] if len(sys.argv) > 1 else "~/.tailscale")
#     try:
#         manager.start()
#         logger.debug("Press Ctrl+C to stop")
#     except KeyboardInterrupt:
#         logger.debug("KeyboardInterrupt received")
#     finally:
#         manager.stop()
