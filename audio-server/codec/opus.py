from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if sys.platform == "win32":
    import os

    # get current path of the file we in
    PWD = os.path.dirname(os.path.abspath(__file__))
    # add the path to the environment variable
    os.environ["PATH"] += os.pathsep + PWD

import opuslib

from .base import BaseCodec

if TYPE_CHECKING:
    from _types import CodecInfo


class OpusCodec(BaseCodec):
    if TYPE_CHECKING:
        _encoder: opuslib.Encoder | None
        _decoder: opuslib.Decoder | None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._encoder = None
        self._decoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            self._encoder = opuslib.Encoder(
                self.rate, self.channels, opuslib.APPLICATION_AUDIO
            )
        return self._encoder

    @property
    def decoder(self):
        if self._decoder is None:
            self._decoder = opuslib.Decoder(self.rate, self.channels)
        return self._decoder

    def encode(self, data: bytes) -> bytes:
        return self.encoder.encode(data, self.frames_per_buffer)

    def decode(self, data: bytes) -> bytes:
        return self.decoder.decode(data, self.frames_per_buffer)

    def close(self):
        pass

    def to_dict(self) -> CodecInfo:
        return {
            "name": "opus",
            "channels": self.channels,
            "rate": self.rate,
            "frames_per_buffer": self.frames_per_buffer,
        }
