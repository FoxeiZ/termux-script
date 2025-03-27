from __future__ import annotations

from typing import TYPE_CHECKING

from . import constants
from .client import ClientSocket
from .sockets import CustomSocket

if TYPE_CHECKING:
    import socket


class ServerSocket(CustomSocket):
    def __init__(self, socket: socket.socket | None = None):
        super().__init__(socket=socket, mode=constants.SocketMode.SERVER)

    def accept(self) -> tuple[ClientSocket, tuple[str, int]]:
        sock, addr = self.socket.accept()
        return ClientSocket(sock), addr

    def bind(self, host: str, port: int):
        self.socket.bind((host, port))

    def listen(self, backlog: int = 5):
        self.socket.listen(backlog)
