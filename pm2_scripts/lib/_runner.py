from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType

    from .plugin import Plugin


async def main(plugin: Plugin) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(plugin.start())

    shutdown_signal: asyncio.Future[None] = loop.create_future()
    shutdown_task: asyncio.Task[None] | None = None
    shutdown_initiated = False

    async def handle_graceful_shutdown() -> None:
        nonlocal shutdown_initiated
        if shutdown_initiated:
            return
        shutdown_initiated = True

        plugin.logger.info("graceful shutdown requested")
        plugin.stop()

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        except TimeoutError:
            plugin.logger.warning("plugin did not stop gracefully, forcing stop")
            plugin.force_stop()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except TimeoutError as exc:
                plugin.logger.error("plugin failed to stop after force stop; executing hard cancellation")
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(task, timeout=2.0)
                raise RuntimeError("failed to stop plugin cleanly") from exc

    def schedule_shutdown() -> None:
        nonlocal shutdown_task
        if shutdown_task and not shutdown_task.done():
            return
        if not shutdown_signal.done():
            shutdown_signal.set_result(None)
        shutdown_task = asyncio.create_task(handle_graceful_shutdown())

    def handle_sync_signal(_signum: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(schedule_shutdown)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, schedule_shutdown)
        except NotImplementedError:
            # fallback for windows
            signal.signal(sig, handle_sync_signal)

    try:
        done, _ = await asyncio.wait({task, shutdown_signal}, return_when=asyncio.FIRST_COMPLETED)
        if shutdown_signal in done and shutdown_task:
            await shutdown_task
        else:
            await task

    except asyncio.CancelledError:
        if not shutdown_initiated:
            shutdown_task = asyncio.create_task(handle_graceful_shutdown())
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(shutdown_task, timeout=15.0)
        raise
    finally:
        if shutdown_task and not shutdown_task.done():
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(shutdown_task, timeout=2.0)
        if not task.done():
            task.cancel()
            with contextlib.suppress(Exception):
                await task
