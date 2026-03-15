from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from typing import Any

MAX_FRAME_SIZE = 8 * 1024 * 1024


def _validate_frame_size(length: int) -> None:
    if length > MAX_FRAME_SIZE:
        raise ConnectionError(f"Payload too large: {length} bytes (max {MAX_FRAME_SIZE})")


async def recv(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    _validate_frame_size(length)
    return await reader.readexactly(length)


async def recv_str(reader: asyncio.StreamReader) -> str:
    data = await recv(reader)
    return data.decode("utf-8")


async def recv_json(reader: asyncio.StreamReader) -> object:
    payload = await recv_str(reader)
    return json.loads(payload)


async def send(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(struct.pack("!I", len(data)))
    writer.write(data)
    await writer.drain()


async def send_json(writer: asyncio.StreamWriter, obj: object) -> None:
    payload = json.dumps(obj).encode("utf-8")
    await send(writer, payload)


class IPCServer:
    def __init__(
        self,
        host: str,
        port: int,
        on_message_received: Any,
        logger: Any,
    ) -> None:
        self.host = host
        self.port = port
        self._on_message_received = on_message_received
        self._logger = logger
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        if self._server is not None:
            self._logger.info("ipc server already running")
            return

        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self._logger.info("ipc tcp server listening on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return

        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._logger.info("ipc tcp server stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            raw = await asyncio.wait_for(recv_str(reader), timeout=30.0)
            response = self._on_message_received(raw)
            if asyncio.iscoroutine(response):
                response = await response

            await send_json(writer, response)

        except TimeoutError:
            self._logger.debug("ipc client %s timed out", peer)
        except asyncio.IncompleteReadError:
            self._logger.debug("ipc client %s disconnected during read", peer)
        except ConnectionError as exc:
            self._logger.debug("ipc client %s disconnected: %s", peer, exc)
        except Exception as exc:
            self._logger.warning("ipc client %s request failed: %s", peer, exc)
            with contextlib.suppress(ConnectionError):
                await send_json(
                    writer,
                    {
                        "status": "failed",
                        "message": str(exc),
                        "data": None,
                    },
                )
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
