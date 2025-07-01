# ruff: noqa: F401

import os

from lib.config import Config
from lib.manager import PluginManager
from plugin import (
    InterfaceMonitorPlugin,
    LongProcessPlugin,
    LongProcessPluginWithError,
    LongProcessPluginWithLongOutput,
    ServerProxyPlugin,
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

    if (
        "com.termux" in os.environ.get("SHELL", "")
        or os.environ.get("PREFIX", "") == "/data/data/com.termux/files/usr"
    ):
        manager.register_plugin(InterfaceMonitorPlugin)
        manager.register_plugin(SystemServerPlugin)
        manager.register_plugin(SystemMonitorPlugin)

    manager.register_plugin(
        ServerProxyPlugin, port=5000, host="0.0.0.0", debug=Config.debug
    )
    manager.run()
