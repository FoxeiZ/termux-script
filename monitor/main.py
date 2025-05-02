import os
import sys

from lib.manager import PluginManager
from plugin import (
    InterfaceMonitorPlugin,
    LongProcessPlugin,
    LongProcessPluginWithError,
    SystemMonitorPlugin,
    SystemServerPlugin,
)

# __all__ = ["PluginManager", "InterfaceMonitorPlugin", "TailscalePlugin"]


if __name__ == "__main__":
    manager = PluginManager(
        webhook_url=os.environ.get(
            "WEBHOOK_URL",
            sys.argv[1] if len(sys.argv) > 1 else None,
        ),
    )
    if os.environ.get("DEBUG", "0") == "1":
        manager.register_plugin(LongProcessPlugin)
        manager.register_plugin(LongProcessPluginWithError)
        # manager.register_plugin(LongProcessPluginWithLongOutput)

    manager.register_plugin(InterfaceMonitorPlugin)
    manager.register_plugin(SystemServerPlugin)
    manager.register_plugin(SystemMonitorPlugin)
    manager.run()
