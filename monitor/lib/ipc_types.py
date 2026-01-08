from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class IPCCommand(StrEnum):
    """Available IPC commands."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LIST = "list"


class IPCRequest(TypedDict):
    """IPC request structure."""

    cmd: str
    plugin_name: str
    args: list[Any]
    kwargs: dict[str, Any]
    force: bool


class IPCResponse(TypedDict):
    """IPC response structure."""

    status: str
    message: str
    data: str | None
