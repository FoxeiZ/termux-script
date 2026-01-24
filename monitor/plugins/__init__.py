from .interface_monitor import InterfaceMonitorPlugin
from .plugin_test.cron_test import TestCron2Min, TestCronPerMin
from .plugin_test.long_process import (
    LongProcessPlugin,
    LongProcessPluginWithError,
    LongProcessPluginWithLongOutput,
    NativeLongProcessPlugin,
    NativeLongProcessPluginRoot,
)
from .system_monitor import SystemMonitorPlugin
from .system_server_monitor import SystemServerPlugin
from .tailscale import TailscaledPlugin

__all__ = [
    "InterfaceMonitorPlugin",
    "LongProcessPlugin",
    "LongProcessPluginWithError",
    "LongProcessPluginWithLongOutput",
    "NativeLongProcessPlugin",
    "NativeLongProcessPluginRoot",
    "SystemMonitorPlugin",
    "SystemServerPlugin",
    "TailscaledPlugin",
    "TestCron2Min",
    "TestCronPerMin",
]
