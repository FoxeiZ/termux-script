import json
import socket
import struct
from io import BytesIO


def recv_len(conn: socket.socket) -> int:
    header = b""
    while len(header) < 4:
        chunk = conn.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("Connection closed while reading length prefix")
        header += chunk
    return struct.unpack("!I", header)[0]


def recv_with_len(conn: socket.socket, length: int, chunk_size: int = 4096) -> bytes:
    io_buf = BytesIO()
    remaining = length
    while remaining > 0:
        chunk = conn.recv(min(chunk_size, remaining))
        if not chunk:
            raise ConnectionError("Connection closed while reading data")
        io_buf.write(chunk)
        remaining -= len(chunk)
    return io_buf.getvalue()


def recv(conn: socket.socket) -> bytes:
    len_prefix = recv_len(conn)
    data = recv_with_len(conn, len_prefix)
    if len(data) != len_prefix:
        raise ConnectionError("Received data length does not match length prefix")
    return data


def recv_str(conn: socket.socket) -> str:
    data_bytes = recv(conn)
    return data_bytes.decode("utf-8")


def recv_json(conn: socket.socket) -> object:
    json_str = recv_str(conn)
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
