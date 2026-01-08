from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from typing import Any, TypedDict

    class _ConfigT(TypedDict):
        WEBHOOK_URL: str | None
        LOG_LEVEL: str
        LOG_FUNCTION_CALL: bool
        RUN_ROOT_ONLY: bool
        RUN_NON_ROOT_ONLY: bool
        RUN_SCRIPT_ONLY: bool
        RUN_ALL: bool
        TAILSCALE_AUTH_KEY: str | None
        LOAD_TEST_PLUGINS: bool
        SCRIPTS_USE_SCREEN: bool


class ConfigLoader:
    _defaults: ClassVar[_ConfigT] = {
        "WEBHOOK_URL": None,
        "LOG_LEVEL": "INFO",
        "LOG_FUNCTION_CALL": False,
        "RUN_ROOT_ONLY": False,
        "RUN_NON_ROOT_ONLY": False,
        "RUN_SCRIPT_ONLY": False,
        "RUN_ALL": False,
        "TAILSCALE_AUTH_KEY": None,
        "LOAD_TEST_PLUGINS": False,
        "SCRIPTS_USE_SCREEN": False,
    }

    if TYPE_CHECKING:
        _config: _ConfigT

    def __init__(self):
        self._config = self._defaults.copy()
        self._parse_args()
        self._load_from_env()

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
        parser.add_argument(
            "--run-root-only",
            dest="RUN_ROOT_ONLY",
            action="store_true",
            help="Run only plugins that require root privileges",
        )
        parser.add_argument(
            "--run-non-root-only",
            dest="RUN_NON_ROOT_ONLY",
            action="store_true",
            help="Run only plugins that do not require root privileges",
        )
        parser.add_argument(
            "--run-script-only",
            dest="RUN_SCRIPT_ONLY",
            action="store_true",
            help="Run only script plugins",
        )
        parser.add_argument(
            "--run-all",
            dest="RUN_ALL",
            action="store_true",
            help="Run all plugins regardless of root privileges",
        )
        parser.add_argument(
            "--tailscale-auth-key",
            dest="TAILSCALE_AUTH_KEY",
            help="Tailscale authentication key",
        )
        parser.add_argument(
            "--scripts-use-screen",
            dest="SCRIPTS_USE_SCREEN",
            action="store_true",
            help="Run scripts with screen wrapper",
        )

        args, _ = parser.parse_known_args()
        for key, value in vars(args).items():
            if value is not None:
                self._config[key] = value

    def _load_from_env(self):
        """Load configuration from environment variables (if not already set by args)."""
        for key, default in self._defaults.items():
            if key not in self._config:
                env_value = os.environ.get(key)
                if env_value is not None:
                    if isinstance(default, bool):
                        self._config[key] = env_value.lower() in ("1", "true", "yes")
                    else:
                        self._config[key] = env_value
                elif default is not None:
                    self._config[key] = default

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self._config[key] = value

    @property
    def webhook_url(self) -> str | None:
        """Get the webhook URL."""
        return self._config.get("WEBHOOK_URL")

    @property
    def debug(self) -> bool:
        """Get the debug mode."""
        return self.log_level == "DEBUG"

    @property
    def log_level(self) -> str:
        """Get the logging level."""
        return self._config.get("LOG_LEVEL", "INFO")

    @property
    def log_function_call(self) -> bool:
        """Get the log function call setting."""
        return self._config.get("LOG_FUNCTION_CALL", False)

    @property
    def run_root_only(self) -> bool:
        """Get the run root only setting."""
        return self._config.get("RUN_ROOT_ONLY", False)

    @property
    def run_non_root_only(self) -> bool:
        """Get the run non-root only setting."""
        return self._config.get("RUN_NON_ROOT_ONLY", False)

    @property
    def run_all(self) -> bool:
        """Get the run all setting."""
        return self._config.get("RUN_ALL", False)

    @property
    def tailscale_auth_key(self) -> str | None:
        """Get the Tailscale authentication key."""
        return self._config.get("TAILSCALE_AUTH_KEY")

    @property
    def scripts_use_screen(self) -> bool:
        """Get the script run with screen setting."""
        return self._config.get("SCRIPTS_USE_SCREEN", False)

    @property
    def load_test_plugins(self) -> bool:
        """Get the load test plugins setting."""
        return self._config.get("LOAD_TEST_PLUGINS", False)


Config = ConfigLoader()
