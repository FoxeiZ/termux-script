"""Process IPC protocol for manager↔plugin communication."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from typing import Any


class ProcessCommand(str, Enum):
    """Commands sent from manager to plugin process."""

    SETUP = "setup"
    START = "start"
    STOP = "stop"
    PING = "ping"


class ProcessEventType(str, Enum):
    """Event types sent from plugin to manager."""

    READY = "ready"
    ERROR = "error"
    RESULT = "result"
    LOG = "log"
    PONG = "pong"
    STOPPED = "stopped"


class CommandPayload(TypedDict, total=False):
    """Payload for command messages."""

    cmd: str  # ProcessCommand value
    args: list[Any]
    kwargs: dict[str, Any]


class EventPayload(TypedDict, total=False):
    """Payload for event messages."""

    event: str  # ProcessEventType value
    data: Any
    error: str | None
    traceback: str | None


class CommandMessage(TypedDict):
    """Command message from manager to plugin."""

    type: Literal["command"]
    payload: CommandPayload


class EventMessage(TypedDict):
    """Event message from plugin to manager."""

    type: Literal["event"]
    payload: EventPayload
