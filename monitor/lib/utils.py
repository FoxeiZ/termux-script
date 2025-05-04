from __future__ import annotations

import logging


def get_logger(
    name: str,
    level: int = logging.INFO,
    handler: type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(message)s",
) -> logging.Logger:
    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(init_handler)
    return logger
