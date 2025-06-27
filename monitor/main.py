# ruff: noqa: F401

from lib.config import Config
from lib.manager import PluginManager
from plugin import (
    InterfaceMonitorPlugin,
    LongProcessPlugin,
    LongProcessPluginWithError,
    LongProcessPluginWithLongOutput,
    SystemMonitorPlugin,
    SystemServerPlugin,
    TestCron2Min,
    TestCronPerMin,
)

if __name__ == "__main__":
    manager = PluginManager(
        webhook_url=Config.webhook_url,
    )
    if Config.debug:
        manager.register_plugin(LongProcessPlugin)
        manager.register_plugin(LongProcessPluginWithError)
        manager.register_plugin(LongProcessPluginWithLongOutput)
        manager.register_plugin(TestCronPerMin)
        manager.register_plugin(TestCron2Min)

    manager.register_plugin(InterfaceMonitorPlugin)
    manager.register_plugin(SystemServerPlugin)
    manager.register_plugin(SystemMonitorPlugin)
    manager.run()
