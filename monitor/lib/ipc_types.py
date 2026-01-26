from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from .plugin.metadata import PluginMetadata


class IPCCommand(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LIST = "list"


class IPCRequest(TypedDict):
    cmd: str
    plugin_name: str
    args: list[Any]
    kwargs: dict[str, Any]
    force: bool


class IPCResponse(TypedDict):
    status: str
    message: str
    data: object | None


class PipeCommand(StrEnum):
    LOAD = "load"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LIST = "list"
    SHUTDOWN = "shutdown"


class PipeRequest(TypedDict):
    id: str
    cmd: PipeCommand
    plugin_name: str | None
    metadata: PluginMetadata | None
    args: list[Any]
    kwargs: dict[str, Any]
    force: bool


class PipeResponse(TypedDict):
    id: str
    status: str
    message: str
    data: Any | None
