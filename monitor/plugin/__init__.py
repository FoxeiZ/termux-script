from .interface_monitor import InterfaceMonitorPlugin
from .plugin_test.cron_test import TestCron2Min, TestCronPerMin
from .plugin_test.long_process import (
    LongProcessPlugin,
    LongProcessPluginWithError,
    LongProcessPluginWithLongOutput,
)
from .system_monitor import SystemMonitorPlugin
from .system_server_monitor import SystemServerPlugin
from .tailscale import TailscalePlugin

__all__ = [
    "InterfaceMonitorPlugin",
    "TailscalePlugin",
    "LongProcessPlugin",
    "LongProcessPluginWithError",
    "LongProcessPluginWithLongOutput",
    "SystemServerPlugin",
    "SystemMonitorPlugin",
    "TestCronPerMin",
    "TestCron2Min",
]
