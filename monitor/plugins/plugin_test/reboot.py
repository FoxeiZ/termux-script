import asyncio
import contextlib

from lib.ipc import send_json
from lib.plugin.cron import Plugin
from lib.types import IPCCommand, IPCCommandInternal, IPCRequestInternal


class TestRebootAfter(Plugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_seconds = 5

    async def start(self):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.after_seconds)
            return

        async with self.manager.internal_ipc() as (_, writer):
            request: IPCRequestInternal = {
                "cmd": IPCCommand.INTERNAL,
                "internal_cmd": IPCCommandInternal.REBOOT,
                "kwargs": {},
                "args": ["test"],
                "password": self.manager.ipc_password,
            }
            await send_json(writer, request)
