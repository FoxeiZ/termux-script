import argparse
import os
from typing import Any, Optional


class ConfigSingleton:
    """
    A singleton class for managing configuration values from environment variables
    and command-line arguments.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigSingleton, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize the configuration values."""
        self._config = {}
        self._parse_args()
        self._load_from_env()

    def _parse_args(self):
        """Parse command line arguments."""
        parser = argparse.ArgumentParser(description="Monitor application")
        parser.add_argument(
            "--webhook-url", dest="WEBHOOK_URL", help="Webhook URL for notifications"
        )
        parser.add_argument(
            "--debug", dest="DEBUG", action="store_true", help="Enable debug mode"
        )

        args, _ = parser.parse_known_args()
        for key, value in vars(args).items():
            if value is not None:
                self._config[key] = value

    def _load_from_env(self):
        """Load configuration from environment variables (if not already set by args)."""
        env_vars = {
            "WEBHOOK_URL": None,
            "DEBUG": False,
        }

        for key, default in env_vars.items():
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
    def webhook_url(self) -> Optional[str]:
        """Get the webhook URL."""
        return self._config.get("WEBHOOK_URL")

    @property
    def debug(self) -> bool:
        """Get the debug mode."""
        return self._config.get("DEBUG", False)


Config = ConfigSingleton()
