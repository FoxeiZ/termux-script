from .interface_monitor import InterfaceMonitorPlugin
from .long_process import LongProcessPlugin, LongProcessPluginWithError
from .system_monitor import SystemMonitorPlugin
from .system_server_monitor import SystemServerPlugin
from .tailscale import TailscalePlugin

__all__ = [
    "InterfaceMonitorPlugin",
    "TailscalePlugin",
    "LongProcessPlugin",
    "LongProcessPluginWithError",
    "SystemServerPlugin",
    "SystemMonitorPlugin",
]
