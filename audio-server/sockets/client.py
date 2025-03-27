from __future__ import annotations

import socket
from typing import TYPE_CHECKING

from . import constants
from .sockets import CustomSocket

if TYPE_CHECKING:
    pass


class ClientSocket(CustomSocket):
    def __init__(self, socket: socket.socket | None = None):
        super().__init__(socket=socket, mode=constants.SocketMode.CLIENT)

    def connect(self, host: str, port: int):
        self.socket.connect((host, port))
