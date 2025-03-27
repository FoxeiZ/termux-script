from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, cast

import pyaudio
from codec._mapping import CODEC_MAP
from codec._types import CodecInfo, StreamInfo
from sockets import ClientSocket

if TYPE_CHECKING:
    from codec.base import BaseCodec


class AudioClient:
    if TYPE_CHECKING:
        host: str
        port: int
        socket: ClientSocket
        audio: pyaudio.PyAudio
        codec: BaseCodec
        stream: pyaudio.Stream | None
        stream_info: StreamInfo | None
        _avg_latency: float
        _print_latency_thread: threading.Thread | None

    def __init__(self, host="127.0.0.1", port=12345):
        self.host = host
        self.port = port
        self.socket = ClientSocket.build_socket()
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.stream_info = None

        self._avg_latency = 0
        self._print_latency_thread = None

    def receive_stream_info(self) -> StreamInfo:
        stream_info = self.socket.receive_json()
        return cast(StreamInfo, stream_info)

    def receive_codec_info(self) -> CodecInfo:
        codec_info = self.socket.receive_json()
        return cast(CodecInfo, codec_info)

    def connect(self):
        self.socket.connect(self.host, self.port)
        if self.stream_info is None:
            self.stream_info = self.receive_stream_info()
        print(f"Stream info: {self.stream_info}")

        codec_info = self.receive_codec_info()
        print(f"Codec info: {codec_info}")
        codec_cls = CODEC_MAP.get(codec_info["name"], CODEC_MAP["raw"])
        self.codec = codec_cls(**codec_info)

        self.stream = self.audio.open(
            format=self.stream_info["format"],
            channels=self.codec.channels,
            rate=self.codec.rate,
            output=True,
        )

    def print_latency(self, interval=1):
        while True:
            print(f"Average latency: {self._avg_latency * 1000:.2f} ms")
            time.sleep(interval)

    def start(self):
        if not self.stream:
            raise ValueError("Stream is not initialized")

        if self.stream_info is None:
            raise ValueError("Stream info is not initialized")

        self._print_latency_thread = threading.Thread(
            target=self.print_latency,
            daemon=True,
        )
        self._print_latency_thread.start()

        # last_time = None
        # buffer_size = self.codec.frame_size
        # buffer_size = self.stream_info.get("frames_per_buffer", 1024)
        try:
            while True:
                data = self.socket.receive()
                if not data:
                    break

                self.stream.write(self.codec.decode(data))
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()
        self.socket.close()


if __name__ == "__main__":
    client = AudioClient()
    client.connect()
    client.start()
