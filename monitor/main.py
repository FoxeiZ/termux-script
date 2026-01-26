# ruff: noqa: F401

import os

from lib.config import Config
from lib.manager import IS_TERMUX, Manager
from plugins import (
    InterfaceMonitorPlugin,
    LongProcessPlugin,
    LongProcessPluginWithError,
    LongProcessPluginWithLongOutput,
    NativeLongProcessPlugin,
    NativeLongProcessPluginRoot,
    SystemMonitorPlugin,
    SystemServerPlugin,
    TailscaledPlugin,
    TestCron2Min,
    TestCronPerMin,
)

if __name__ == "__main__":
    manager = Manager(webhook_url=Config.webhook_url)
    if Config.load_test_plugins:
        # manager.register_plugin(LongProcessPlugin)
        # manager.register_plugin(LongProcessPluginWithError)
        # manager.register_plugin(LongProcessPluginWithLongOutput)
        manager.register_plugin(TestCronPerMin)
        manager.register_plugin(TestCron2Min)
        manager.register_plugin(NativeLongProcessPlugin)
        manager.register_plugin(NativeLongProcessPluginRoot)

    if IS_TERMUX:
        manager.register_plugin(
            InterfaceMonitorPlugin,
            reboot=True,
            hotspot=True,
            reboot_threshold=1800,
        )
        manager.register_plugin(SystemServerPlugin)
        manager.register_plugin(SystemMonitorPlugin)
        manager.register_plugin(TailscaledPlugin, auth_key=Config.tailscale_auth_key)

    manager.run()
