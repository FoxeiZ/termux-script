from __future__ import annotations

import logging
from typing import Callable


def get_logger(
    name: str,
    level: int = logging.INFO,
    handler: type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> logging.Logger:
    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(init_handler)
    return logger


def log_function_call(func: Callable) -> Callable:
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
