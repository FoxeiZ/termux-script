import os
import shutil
import sys
from pathlib import Path
from typing import Any

from lib.utils import get_logger

DIR = Path(__file__).resolve().parent
SVDIR = (
    Path(os.environ.get("SV_DIR", "/data/data/com.termux/files/usr/var/service"))
    if sys.platform == "linux"
    else DIR / "sv"
)

if not SVDIR.exists():
    raise RuntimeError(f"Service dir not exist, cannot continue. Make sure the path {SVDIR} exists.")


logger = get_logger("installer")


def init_supervisor_dir(service_path: str | Path) -> None:
    """Initialize the supervisor directory for the service"""
    svc = Path(service_path)
    supervisor_path = svc / "supervise"
    supervisor_path.mkdir(exist_ok=True)

    (supervisor_path / "pid").touch()
    (supervisor_path / "status").touch()
    (supervisor_path / "stat").touch()
    if sys.platform == "linux":
        os.mkfifo(str(supervisor_path / "control"), 0o600)
        os.mkfifo(str(supervisor_path / "ok"), 0o600)
    else:
        # simulate mkfifo for non-linux
        (supervisor_path / "control").touch()
        (supervisor_path / "ok").touch()


def init_log_service_dir(service_path: str | Path) -> None:
    """Initialize the log service directory"""
    log_path = Path(service_path) / "log"
    log_path.mkdir(parents=True, exist_ok=True)
    init_supervisor_dir(log_path)

    run_file = log_path / "run"
    with run_file.open("w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write('svlogger="/data/data/com.termux/files/usr/share/termux-services/svlogger"\n')
        f.write('exec "${svlogger}" "$@"\n')
    run_file.chmod(0o755)


def init_service_dir(
    service_name: str,
    service_runtime_path: str | None = None,
    runtime_script: str | None = None,
) -> None:
    """Initialize the service directory"""
    service_path = SVDIR / service_name
    service_path.mkdir(exist_ok=True)
    if not service_runtime_path:
        service_runtime_path = str(DIR / service_name)
    else:
        service_runtime_path = str(Path(service_runtime_path).resolve())

    if not runtime_script:
        runtime_script = f"exec python {service_runtime_path}"

    init_log_service_dir(service_path)
    init_supervisor_dir(service_path)
    run_file = service_path / "run"
    with run_file.open("w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write(runtime_script + "\n")
    run_file.chmod(0o755)


def install_service(
    service_name: str | None,
    *,
    service_runtime_path: str | None = None,
    runtime_script: str | None = None,
    force: bool = False,
) -> None:
    """Install the service by creating a symbolic link in the service directory."""
    if not isinstance(service_name, str):
        raise ValueError(f"Service name must be a string, got {type(service_name)}")

    service_path = SVDIR / service_name
    if service_path.exists():
        if force:
            shutil.rmtree(service_path)
            logger.warning(f"Removed existing service {service_name} due to force option")
        else:
            logger.warning(f"Service {service_name} already installed at {service_path}")
            return

    init_service_dir(
        service_name,
        service_runtime_path=service_runtime_path,
        runtime_script=runtime_script,
    )
    logger.info(f"Service {service_name} installed at {service_path}")


def parse_args(args: list[str]) -> dict[str, Any]:
    matched = None
    pargs: dict[str, Any] = {}
    for arg in args:
        if matched:
            pargs[matched] = arg
            matched = None
            continue

        match arg:
            case "--force":
                pargs["force"] = True
            case "--name":
                matched = "service_name"
            case "--runtime-path":
                matched = "service_runtime_path"
            case "--script":
                matched = "runtime_script"
            case _:
                if arg.startswith("--"):
                    logger.warning(f"Unknown argument: {arg}")
                    sys.exit(1)

    return pargs


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python installer.py <service_name> [<service_runtime_path>] [<runtime_script>] [--force]")
        sys.exit(1)

    install_service(**parse_args(sys.argv[1:]))
