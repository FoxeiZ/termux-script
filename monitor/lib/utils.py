from __future__ import annotations

import logging
import subprocess
from functools import wraps
from typing import TYPE_CHECKING, Protocol, cast

from lib.config import Config

if TYPE_CHECKING:
    from typing import Callable, ParamSpec, TypeVar

    P = ParamSpec("P")
    R = TypeVar("R")
    T = TypeVar("T")


class HasLogger(Protocol):
    logger: logging.Logger


def get_logger(
    name: str,
    level: str | int = Config.log_level,
    handler: type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> logging.Logger:
    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger = logging.getLogger(name)
    logger.setLevel("DEBUG" if Config.debug else level or "INFO")
    logger.addHandler(init_handler)
    return logger


def log_function_call(func: Callable[P, R]) -> Callable[P, R]:
    if not Config.debug or not Config.log_function_call:
        return func

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            logger = cast(HasLogger, args[0]).logger
        except AttributeError:
            logger = get_logger(func.__name__)

        logger.info(f"Calling {func.__name__} with args: {args} and kwargs: {kwargs}")
        result = func(*args, **kwargs)
        logger.info(f"{func.__name__} returned: {result}")
        return result

    return wrapper
