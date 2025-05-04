from __future__ import annotations

import logging
from typing import Callable

from lib.config import Config


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


def log_function_call(func: Callable) -> Callable:
    if not Config.debug and not Config.log_function_call:
        return func

    def wrapper(*args, **kwargs):
        try:
            logger = args[0].logger
        except AttributeError:
            logger = get_logger(func.__name__)

        logger.info(f"Calling {func.__name__} with args: {args} and kwargs: {kwargs}")
        result = func(*args, **kwargs)
        logger.info(f"{func.__name__} returned: {result}")
        return result

    return wrapper
