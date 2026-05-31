from __future__ import annotations

import argparse
import os
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from dotenv import find_dotenv, load_dotenv

if TYPE_CHECKING:
    from typing import Any


class ConfigT(TypedDict):
    NAME: str
    LOG_FUNCTION_CALL: bool
    LOG_LEVEL: str
    RESTART_ON_FAILURE: bool
    BASE_DELAY: int
    MAX_BACKOFF: int
    MAX_RETRIES: int
    WEBHOOK_URL: str | None


DIR = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
IS_TERMUX = (
    "com.termux" in os.environ.get("SHELL", "") or os.environ.get("PREFIX", "") == "/data/data/com.termux/files/usr"
)


class ConfigLoader[T: ConfigT]:
    if TYPE_CHECKING:
        _config: T

    def __init__(self):
        load_dotenv()
        load_dotenv(find_dotenv(usecwd=True))
        load_dotenv(find_dotenv(filename=".env.local"))
        load_dotenv(find_dotenv(usecwd=True, filename=".env.local"))

        self._config = self.get_defaults()
        self._parse_args()
        self._load_from_env()
        self.on_init()

    @abstractmethod
    def on_add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Called during initialization to add custom command line arguments."""

    @abstractmethod
    def on_init(self) -> None:
        """Called after configuration is loaded. Useful for validation or post-processing."""

    def get_defaults(self) -> T:
        """Get the default configuration values."""
        return {  # type: ignore
            "NAME": "pm2-scripts",
            "LOG_FUNCTION_CALL": False,
            "LOG_LEVEL": "INFO",
            "RESTART_ON_FAILURE": False,
            "BASE_DELAY": 1,
            "MAX_BACKOFF": 300,
            "MAX_RETRIES": 0,
            "WEBHOOK_URL": None,
        }

    def _parse_args(self):
        """Parse command line arguments."""
        parser = argparse.ArgumentParser(add_help=True, exit_on_error=True)
        parser.add_argument(
            "--webhook-url",
            dest="WEBHOOK_URL",
            help="Webhook URL for notifications",
        )
        parser.add_argument(
            "--log-level",
            dest="LOG_LEVEL",
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="INFO",
            help="Set the logging level",
        )
        parser.add_argument(
            "--debug",
            dest="LOG_LEVEL",
            action="store_const",
            const="DEBUG",
            help="Enable debug mode, short for --log-level=DEBUG",
        )
        parser.add_argument(
            "--log-function-call",
            dest="LOG_FUNCTION_CALL",
            action="store_true",
            help="Log function calls. Use with --debug or --log-level=DEBUG",
        )

        self.on_add_arguments(parser)

        args, _ = parser.parse_known_args()
        for key, value in vars(args).items():
            if value is not None:
                self._config[key] = value

    def _load_from_env(self) -> None:
        """Load configuration from environment variables (if not already set by args)."""
        for key, default in self.get_defaults().items():
            # skip if already set by command line args (value differs from default)
            if self._config.get(key) != default:
                continue
            env_value = os.environ.get(key)
            if env_value is not None:
                if isinstance(default, bool):
                    self._config[key] = self.str_to_bool(env_value)
                else:
                    self._config[key] = env_value

    def str_to_bool(self, value: str | bool) -> bool:
        if isinstance(value, bool):
            return value
        return value.lower() in ("1", "true", "yes")

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __getattr__(self, key: str) -> Any:
        if key in self._config:
            return self.get(key)
        defaults = self.get_defaults()
        if key in defaults:
            return defaults[key]  # type: ignore  # last resort
        raise AttributeError(f"Config has no attribute '{key}'")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self._config[key] = value

    @property
    def webhook_url(self) -> str | None:
        return self._config.get("WEBHOOK_URL")

    @property
    def debug(self) -> bool:
        return self.log_level == "DEBUG"

    @property
    def log_level(self) -> str:
        return self._config.get("LOG_LEVEL", "INFO")

    @property
    def log_function_call(self) -> bool:
        return self.str_to_bool(self._config.get("LOG_FUNCTION_CALL", False))

    @property
    def restart_on_failure(self) -> bool:
        return self.str_to_bool(self._config.get("RESTART_ON_FAILURE", False))

    @property
    def base_delay(self) -> int:
        return int(self._config.get("BASE_DELAY", 1))

    @property
    def max_backoff(self) -> int:
        return int(self._config.get("MAX_BACKOFF", 300))

    @property
    def max_retries(self) -> int:
        return int(self._config.get("MAX_RETRIES", 0))

    @property
    def name(self) -> str:
        return self._config.get("NAME", "pm2-scripts")
