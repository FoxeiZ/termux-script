import json
import socket
import struct
from io import BytesIO

DEFAULT_RECV_TIMEOUT = 30.0


def recv_len(conn: socket.socket, timeout: float | None = DEFAULT_RECV_TIMEOUT) -> int:
    original_timeout = conn.gettimeout()
    if timeout is not None:
        conn.settimeout(timeout)
    try:
        header = b""
        while len(header) < 4:
            chunk = conn.recv(4 - len(header))
            if not chunk:
                raise ConnectionError("Connection closed while reading length prefix")
            header += chunk
        return struct.unpack("!I", header)[0]
    finally:
        conn.settimeout(original_timeout)


def recv_with_len(
    conn: socket.socket,
    length: int,
    chunk_size: int = 4096,
    timeout: float | None = DEFAULT_RECV_TIMEOUT,
) -> bytes:
    original_timeout = conn.gettimeout()
    if timeout is not None:
        conn.settimeout(timeout)
    try:
        io_buf = BytesIO()
        remaining = length
        while remaining > 0:
            chunk = conn.recv(min(chunk_size, remaining))
            if not chunk:
                raise ConnectionError("Connection closed while reading data")
            io_buf.write(chunk)
            remaining -= len(chunk)
        return io_buf.getvalue()
    finally:
        conn.settimeout(original_timeout)


def recv(conn: socket.socket, timeout: float | None = DEFAULT_RECV_TIMEOUT) -> bytes:
    len_prefix = recv_len(conn, timeout)
    data = recv_with_len(conn, len_prefix, timeout=timeout)
    if len(data) != len_prefix:
        raise ConnectionError("Received data length does not match length prefix")
    return data


def recv_str(conn: socket.socket, timeout: float | None = DEFAULT_RECV_TIMEOUT) -> str:
    data_bytes = recv(conn, timeout)
    return data_bytes.decode("utf-8")


def recv_json(conn: socket.socket, timeout: float | None = DEFAULT_RECV_TIMEOUT) -> object:
    json_str = recv_str(conn, timeout)
    return json.loads(json_str)


def send_len(conn: socket.socket, data_len: int) -> None:
    header = struct.pack("!I", data_len)
    conn.sendall(header)


def send(conn: socket.socket, data: bytes, chunk_size: int = 4096) -> None:
    io_buf = BytesIO(data)
    remaining = len(data)
    send_len(conn, remaining)
    while remaining > 0:
        chunk = io_buf.read(min(chunk_size, remaining))
        if not chunk:
            break
        conn.sendall(chunk)
        remaining -= len(chunk)


def send_str(conn: socket.socket, data: str) -> None:
    data_bytes = data.encode("utf-8")
    send(conn, data_bytes)


def send_json(conn: socket.socket, obj: object) -> None:
    json_str = json.dumps(obj)
    send_str(conn, json_str)
