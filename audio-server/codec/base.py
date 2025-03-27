from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _types import CodecInfo


class BaseCodec:
    if TYPE_CHECKING:
        channels: int
        rate: int
        frames_per_buffer: int

    def __init__(self, *args, **kwargs):
        self.channels = kwargs.get("channels", 2)
        self.rate = kwargs.get("rate", 48000)
        self.frames_per_buffer = kwargs.get("frames_per_buffer", 1024)

        print(self.to_dict())

    def encode(self, data: bytes) -> bytes:
        raise NotImplementedError

    def decode(self, data: bytes) -> bytes:
        raise NotImplementedError

    def to_dict(self) -> CodecInfo:
        raise NotImplementedError

    def close(self):
        pass


class RawCodec(BaseCodec):
    def encode(self, data: bytes) -> bytes:
        return data

    def decode(self, data: bytes) -> bytes:
        return data

    def to_dict(self) -> CodecInfo:
        return {
            "name": "raw",
            "channels": self.channels,
            "rate": self.rate,
            "frames_per_buffer": self.frames_per_buffer,
        }
