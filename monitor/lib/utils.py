from __future__ import annotations

import logging
import multiprocessing
import shlex
import subprocess
from functools import wraps
from logging.handlers import QueueHandler, QueueListener
from typing import TYPE_CHECKING, cast

from .config import Config

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Protocol

    class HasLogger(Protocol):
        logger: logging.Logger


def get_logger(
    name: str,
    level: str | int = Config.log_level,
    handler: type[logging.Handler] | None = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel("DEBUG" if Config.debug else level or "INFO")

    root_logger = logging.getLogger()
    if handler is not None and not logger.handlers and not root_logger.handlers:
        init_handler = handler()
        init_handler.setFormatter(logging.Formatter(formatter))
        logger.addHandler(init_handler)

    logger.propagate = True
    return logger


def setup_logging_queue(
    level: str | int = Config.log_level,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> tuple[multiprocessing.Queue[logging.LogRecord], QueueListener]:
    log_queue: multiprocessing.Queue[logging.LogRecord] = multiprocessing.Queue()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(formatter))
    handler.setLevel("DEBUG" if Config.debug else level or "INFO")
    listener = QueueListener(log_queue, handler, respect_handler_level=True)
    listener.start()
    configure_queue_logging(log_queue, level)
    return log_queue, listener


def configure_queue_logging(
    log_queue: multiprocessing.Queue[logging.LogRecord],
    level: str | int = Config.log_level,
) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel("DEBUG" if Config.debug else level or "INFO")
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.addHandler(QueueHandler(log_queue))


def log_function_call[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    if not Config.debug or not Config.log_function_call:
        return func

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            logger = cast("HasLogger", args[0]).logger
        except AttributeError:
            logger = get_logger(func.__name__)

        logger.info(f"Calling {func.__name__} with args: {args} and kwargs: {kwargs}")
        result = func(*args, **kwargs)
        logger.info(f"{func.__name__} returned: {result}")
        return result

    return wrapper


def run_as_root(command: str | list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a command as root using sudo."""
    if isinstance(command, str):
        try:
            command_list = shlex.split(command)
        except ValueError as exc:
            raise RuntimeError("invalid command string") from exc
    else:
        if not all(isinstance(part, str) for part in command):
            raise TypeError("command list must contain only strings")
        command_list = list(command)

    if not command_list:
        raise ValueError("empty command provided")

    full_cmd = ["sudo", *command_list]
    try:
        return subprocess.run(
            full_cmd,
            check=True,
            text=True,
            capture_output=True,
            **kwargs,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command '{' '.join(command)}' failed with error: {e.stderr.strip()}") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"Command '{' '.join(command)}' not found. Please install sudo.") from e
