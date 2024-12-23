from pathlib import Path
from queue import Queue
import os
import re
import sys
import signal
import subprocess
import threading
import time


class Tailscaled(subprocess.Popen):
    def __init__(self, home_dir: Path | str):
        self.home_dir = Path(home_dir).absolute()
        self.stopped = False

        # find binary  # TODO: maybe auto download?
        tailscaled_bin = self.home_dir / "tailscaled"
        if not tailscaled_bin.exists():
            raise FileNotFoundError(f"{tailscaled_bin} not found")

        state_dir = self.home_dir / "state"
        if not state_dir.exists():
            state_dir.mkdir()

        self.output_queue: Queue[str] = Queue()
        self.output_thread = threading.Thread(target=self._output_reader, daemon=True)

    def _output_reader(self):
        for line in self.stdout.readlines():  # type: ignore
            if not line or self.stopped:
                break

            if isinstance(line, bytes):
                line = line.decode("utf-8")
            self.output_queue.put(line)
            print(line, end="")

    def wait_for_connection(self, timeout: int = 60):
        pattern = re.compile(r"magicsock.*connected")
        start_time = time.time()

        while time.time() - start_time < timeout:
            line = self.output_queue.get()
            if pattern.search(line):
                return True

            if self.poll() is not None:
                raise Exception("Tailscaled stopped unexpectedly")

        return False

    def start(self):
        self.args = [
            str(self.home_dir / "tailscaled"),
            "--state",
            str(self.home_dir / "statedir"),
            "--socket",
            str(self.home_dir / "tailscaled.sock"),
        ]

        super().__init__(
            self.args,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        self.output_thread.start()

    def stop(self, timeout: int = 15):
        if self.stopped:
            return

        self.stopped = True
        if self.output_thread.is_alive():
            self.output_thread.join(1)

        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed


class Socatd(subprocess.Popen):
    def __init__(self):
        self.stopped = False

    def start(self):
        self.args = [
            "socat",
            "TCP-LISTEN:0,reuseaddr,fork",
            "SOCKS5:127.0.0.1:,socksport=8055",
        ]

        super().__init__(
            self.args,
            start_new_session=True,
        )

    def stop(self, timeout: int = 15):
        if self.stopped:
            return

        self.stopped = True
        if self.poll() is None:  # if running
            self.send_signal(signal.SIGINT)
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()  # force kill if sigint failed


class Manager:
    def __init__(self, home_dir: Path | str):
        self.home_dir = Path(home_dir)
        self.tailscaled = Tailscaled(self.home_dir)
        self.socatd = Socatd()

    def start(self):
        self.tailscaled.start()
        if not self.tailscaled.wait_for_connection():
            raise Exception("Tailscaled failed to connect")

        self.socatd.start()

    def stop(self):
        self.socatd.stop()
        self.tailscaled.stop()


if __name__ == "__main__":
    if os.getuid() != 0:  # type: ignore
        print("Please run as root")
        sys.exit(1)

    manager = Manager(sys.argv[1] if len(sys.argv) >= 2 else "~/.tailscale")
    manager.start()
    try:
        input("Press Enter to stop tailscale")
    except KeyboardInterrupt:
        manager.stop()
    finally:
        manager.stop()
