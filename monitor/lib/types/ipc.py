from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class IPCCommand(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LIST = "list"
    INTERNAL = "internal"


class IPCCommandInternal(StrEnum):
    REBOOT = "internal_shutdown"
    UPDATE_STATE = "internal_update_state"


class IPCRequest(TypedDict):
    cmd: IPCCommand
    args: list[Any]
    kwargs: dict[str, Any]


class IPCRequestInternal(IPCRequest):
    internal_cmd: IPCCommandInternal
    password: str


class IPCRequestManager(IPCRequest):
    plugin_name: str
    force: bool


class IPCResponse(TypedDict):
    status: str
    message: str
    data: object | None
