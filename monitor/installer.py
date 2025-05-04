import os
import shutil
import sys

from lib.utils import get_logger

DIR = os.path.dirname(os.path.abspath(__file__))
SVDIR = (
    os.environ.get("SV_DIR", "/data/data/com.termux/files/usr/var/service")
    if sys.platform == "linux"
    else os.path.join(DIR, "sv")
)

if not os.path.exists(SVDIR):
    # os.makedirs(SVDIR, exist_ok=True)
    raise RuntimeError(
        f"Service dir not exist, cannot continue. Make sure the path {SVDIR} exists."
    )


logger = get_logger("installer")


def init_supervisor_dir(service_path: str):
    """
    Initialize the supervisor directory for the service
    """
    supervisor_path = os.path.join(service_path, "supervise")
    os.mkdir(supervisor_path)
    open(os.path.join(supervisor_path, "pid"), "w").close()
    open(os.path.join(supervisor_path, "status"), "w").close()
    open(os.path.join(supervisor_path, "stat"), "w").close()
    if sys.platform == "linux":
        os.mkfifo(os.path.join(supervisor_path, "control"), 0o600)
        os.mkfifo(os.path.join(supervisor_path, "ok"), 0o600)
    else:
        # simulate mkfifo for non-linux
        open(os.path.join(supervisor_path, "control"), "w").close()
        open(os.path.join(supervisor_path, "ok"), "w").close()


def init_log_service_dir(service_path: str):
    """
    Initialize the log service directory
    """
    log_path = os.path.join(service_path, "log")
    os.makedirs(log_path, exist_ok=True)
    init_supervisor_dir(log_path)

    with open(os.path.join(log_path, "run"), "w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write(
            'svlogger="/data/data/com.termux/files/usr/share/termux-services/svlogger"\n'
        )
        f.write('exec "${svlogger}" "$@"\n')
    os.chmod(os.path.join(log_path, "run"), 0o755)


def init_service_dir(
    service_name: str,
    service_runtime_path: str | None = None,
    runtime_script: str | None = None,
):
    """
    Initialize the service directory
    """
    service_path = os.path.join(SVDIR, service_name)
    os.mkdir(service_path)
    if not service_runtime_path:
        service_runtime_path = os.path.join(DIR, service_name)
    else:
        service_runtime_path = os.path.abspath(service_runtime_path)

    if not runtime_script:
        runtime_script = f"exec python {service_runtime_path}"

    init_log_service_dir(service_path)
    init_supervisor_dir(service_path)
    with open(os.path.join(service_path, "run"), "w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write(runtime_script + "\n")
    os.chmod(os.path.join(service_path, "run"), 0o755)


def install_service(
    service_name: str | None,
    *,
    service_runtime_path: str | None = None,
    runtime_script: str | None = None,
    force: bool = False,
):
    """
    Install the service by creating a symbolic link in the service directory.
    """
    if not isinstance(service_name, str):
        raise ValueError(f"Service name must be a string, got {type(service_name)}")

    service_path = os.path.join(SVDIR, service_name)
    if os.path.exists(service_path):
        if force:
            shutil.rmtree(service_path)
            logger.warning(
                f"Removed existing service {service_name} due to force option"
            )
        else:
            logger.warning(
                f"Service {service_name} already installed at {service_path}"
            )
            return

    init_service_dir(
        service_name,
        service_runtime_path=service_runtime_path,
        runtime_script=runtime_script,
    )
    logger.info(f"Service {service_name} installed at {service_path}")


def parse_args(args):
    matched = None
    pargs = {}
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
        print(
            "Usage: python installer.py <service_name> [<service_runtime_path>] [<runtime_script>] [--force]"
        )
        sys.exit(1)

    install_service(**parse_args(sys.argv[1:]))
