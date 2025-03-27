from __future__ import annotations

import json
import pathlib
import random
import socket
import string
import time
from typing import TYPE_CHECKING, overload

from . import constants

if TYPE_CHECKING:
    from typing import Literal, Self


class CustomSocket:
    if TYPE_CHECKING:
        _socket: socket.socket | None
        _mode: constants.SocketMode

    def __init__(
        self,
        socket: socket.socket | None = None,
        mode: constants.SocketMode = constants.SocketMode.UNSET,
    ):
        self._socket = socket
        self._mode = mode
        self._closed = False

    @classmethod
    def build_socket(
        cls,
        family: int = socket.AF_INET,
        type: int = socket.SOCK_STREAM,
        proto: int = 0,
    ):
        return cls(socket=socket.socket(family, type, proto))

    @property
    def mode(self) -> constants.SocketMode:
        return self._mode

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def socket(self) -> socket.socket:
        if self._socket is None:
            raise ValueError("Socket is not initialized")
        return self._socket

    @socket.setter
    def socket(self, value: socket.socket | Self):
        if isinstance(value, CustomSocket):
            self._socket = value.socket
        elif isinstance(value, socket.socket):
            self._socket = value
        else:
            raise ValueError("Invalid value type")

    def _send(self, data: bytes, wait_ack: bool = True):
        # TODO: Implement timeout, retry
        self.socket.send(data)
        if wait_ack:
            ack = self.socket.recv(constants.SIGNAL_SIZE)
            if ack != constants.ACK:
                raise ConnectionError("Failed to send data")

    def _generate_packet_id(self) -> bytes:
        """Generate a random packet ID as bytes."""
        chars = string.ascii_letters + string.digits
        random_str = "".join(
            random.choice(chars) for _ in range(constants.PACKET_ID_SIZE)
        )
        return random_str.encode("ascii")

    def _create_packet_header(
        self,
        packet_type: bytes,
        data_length: int,
        packet_id: bytes | None = None,
        need_ack: bool = True,
    ) -> bytes:
        """Create a packet header with the given type, length, and optional ID."""
        if packet_id is None:
            packet_id = self._generate_packet_id()
        length_bytes = data_length.to_bytes(
            constants.PACKET_LENGTH_SIZE, byteorder="big"
        )
        ack_flag = constants.NEED_ACK if need_ack else b"\x00"
        return packet_id + packet_type + ack_flag + length_bytes

    def _parse_packet_header(self, header: bytes) -> tuple[bytes, bytes, bool, int]:
        """Parse a packet header into ID, type, need_ack flag, and length."""
        if len(header) != constants.PACKET_HEADER_SIZE:
            raise ValueError(
                f"Invalid header size: {len(header)}, expected {constants.PACKET_HEADER_SIZE}"
            )

        packet_id = header[: constants.PACKET_ID_SIZE]
        packet_type = header[
            constants.PACKET_ID_SIZE : constants.PACKET_ID_SIZE
            + constants.PACKET_TYPE_SIZE
        ]
        ack_flag = header[
            constants.PACKET_ID_SIZE
            + constants.PACKET_TYPE_SIZE : constants.PACKET_ID_SIZE
            + constants.PACKET_TYPE_SIZE
            + constants.PACKET_ACK_NEED_SIZE
        ]
        need_ack = ack_flag == constants.NEED_ACK

        length_bytes = header[
            constants.PACKET_ID_SIZE
            + constants.PACKET_TYPE_SIZE
            + constants.PACKET_ACK_NEED_SIZE :
        ]
        length = int.from_bytes(length_bytes, byteorder="big")

        return packet_id, packet_type, need_ack, length

    def send_packet(
        self, data: bytes, chunk_size: int = 4096, wait_ack: bool = True
    ) -> None:
        """Send data as one or more packets, handling chunking if needed."""
        packet_id = self._generate_packet_id()

        # If data fits in a single packet
        if len(data) <= chunk_size:
            header = self._create_packet_header(
                constants.COMPLETE, len(data), packet_id, need_ack=wait_ack
            )
            self._send(header + data, wait_ack=wait_ack)
            return

        # Send data in chunks
        total_sent = 0
        while total_sent < len(data):
            chunk = data[total_sent : total_sent + chunk_size]

            # Determine packet type
            if total_sent + len(chunk) >= len(data):
                packet_type = constants.END
            elif total_sent == 0:
                packet_type = constants.CONTINUE
            else:
                packet_type = constants.CONTINUE

            header = self._create_packet_header(
                packet_type, len(chunk), packet_id, need_ack=wait_ack
            )
            self._send(header + chunk, wait_ack=wait_ack)
            total_sent += len(chunk)

    def receive_packet(self) -> tuple[bytes, bytes, bytes]:
        """Receive a packet, returning packet ID, type, and data."""
        header = self.socket.recv(constants.PACKET_HEADER_SIZE)
        if not header or len(header) < constants.PACKET_HEADER_SIZE:
            raise ConnectionError("Connection closed or insufficient data received")

        packet_id, packet_type, need_ack, length = self._parse_packet_header(header)
        data = self.socket.recv(length)

        # Only send ACK if it was requested
        if need_ack:
            self.socket.send(constants.ACK)

        return packet_id, packet_type, data

    def receive_complete_packet(self) -> bytes:
        """Receive a complete packet, reassembling chunks if necessary."""
        packet_id, packet_type, data = self.receive_packet()

        # If this is a complete packet, return it directly
        if packet_type == constants.COMPLETE:
            return data

        # Otherwise, we need to collect all chunks
        chunks = bytearray(data)
        while packet_type != constants.END:
            new_packet_id, packet_type, data = self.receive_packet()
            if new_packet_id != packet_id:
                raise ValueError(
                    f"Mismatched packet IDs: {new_packet_id} != {packet_id}"
                )
            chunks.extend(data)

        return bytes(chunks)

    def send(self, data: bytes, wait_ack: bool = True):
        """Send data using the packet protocol."""
        self.send_packet(data, wait_ack=wait_ack)

    def receive(self, size: int = -1) -> bytes:
        """
        If size != -1, keep reading chunks until we get at least `size` bytes.
        If size == -1, read only one chunk.
        NOTE: This is chunk-based, so a single chunk may exceed `size`.
        """
        # Try to use the packet protocol first
        if size == -1:
            return self.receive_complete_packet()

        data_collected = bytearray()
        while len(data_collected) < size:
            chunk = self.receive_complete_packet()
            data_collected.extend(chunk)
        return bytes(data_collected)

    @overload
    def ping(self) -> float: ...

    @overload
    def ping(self, wait_pong: Literal[True]) -> float: ...

    @overload
    def ping(self, wait_pong: Literal[False]) -> None: ...
    def ping(self, wait_pong: bool = True) -> float | None:
        start = time.perf_counter()
        self.send(constants.PING, wait_ack=False)
        if wait_pong:
            pong_sig = self.receive(constants.SIGNAL_SIZE)
            elapsed = time.perf_counter() - start
            if pong_sig != constants.PONG:
                raise ConnectionError("Failed to ping")
            return elapsed
        return None

    def pong(self):
        data = self.receive(constants.SIGNAL_SIZE)
        if data != constants.PING:
            raise ConnectionError("Failed to pong, invalid data received")
        self.send(constants.PONG)

    def send_string(
        self,
        data: str,
        encoding: Literal["ascii", "utf-8", "utf-16", "utf-32"] = "utf-8",
        wait_ack: bool = True,
    ):
        self.send(constants.STR_ENCODING[encoding], wait_ack=wait_ack)
        self.send(data.encode(encoding), wait_ack=wait_ack)

    def receive_string(self) -> str:
        encoding = self.receive(constants.ENCODING_SIZE)
        data = self.receive()
        return data.decode(constants.STR_ENCODING_REV[encoding])

    def send_json(self, data: dict, wait_ack: bool = True):
        self.send_string(json.dumps(data), wait_ack=wait_ack)

    def receive_json(self) -> dict:
        return json.loads(self.receive_string())

    def send_file(
        self, path: pathlib.Path | str, wait_ack: bool = True, buffer_size: int = 1024
    ):
        if isinstance(path, str):
            path = pathlib.Path(path)

        if not path.exists():
            raise FileNotFoundError(f"File {path} not found")

        self.send_string(path.name, wait_ack=wait_ack)
        self.send_string(str(path.stat().st_size), wait_ack=wait_ack)

        with open(path, "rb") as file:
            # Try using sendfile if available and no ACK needed
            if hasattr(socket, "sendfile") and not wait_ack:
                try:
                    remaining = path.stat().st_size
                    sent = 0
                    while remaining > 0:
                        sent_bytes = self.socket.sendfile(
                            file, offset=sent, count=remaining
                        )
                        if sent_bytes == 0:  # Connection closed
                            raise ConnectionError(
                                "Connection closed during file transfer"
                            )
                        sent += sent_bytes
                        remaining -= sent_bytes
                    return
                except (AttributeError, OSError):
                    # Reset file pointer if sendfile failed
                    file.seek(0)

            # Fallback to chunk-based method
            while data := file.read(buffer_size):
                self.send(data, wait_ack=wait_ack)

    def receive_file(self, path: pathlib.Path | str):
        name = self.receive_string()
        if isinstance(path, str):
            path = pathlib.Path(path)

        size = int(self.receive_string())
        if path.is_dir():
            path = path / name

        with open(path, "wb") as file:
            received = 0
            while received < size:
                data = self.receive(-1)
                if not data:  # Connection closed
                    break
                file.write(data)
                received += len(data)

        if path.stat().st_size != size:
            raise ConnectionError(
                f"Failed to receive file {path}, size mismatch, sender has {size} / we received {path.stat().st_size}"
            )

    def close(self):
        if self._socket:
            self._socket.close()
            self._socket = None
