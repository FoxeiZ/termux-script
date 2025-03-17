from __future__ import annotations

import json
import socket
import threading
import time
from typing import TYPE_CHECKING

import pyaudio

if TYPE_CHECKING:
    from _types import StreamInfo


class AudioClient:
    if TYPE_CHECKING:
        host: str
        port: int
        socket: socket.socket
        audio: pyaudio.PyAudio
        stream: pyaudio.Stream | None
        stream_info: StreamInfo | None

    def __init__(self, host="127.0.0.1", port=12345):
        self.host = host
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.stream_info = None

        self._avg_latency = 0
        self._print_latency_thread = None

    def receive_stream_info(self) -> StreamInfo:
        data = self.socket.recv(4)
        data_len = int.from_bytes(data, "big")
        data = self.socket.recv(data_len)
        return json.loads(data)

    def connect(self):
        self.socket.connect((self.host, self.port))
        if self.stream_info is None:
            self.stream_info = self.receive_stream_info()
        print(f"Stream info: {self.stream_info}")

        self.stream = self.audio.open(
            format=self.stream_info["format"],
            channels=self.stream_info["channels"],
            rate=self.stream_info["rate"],
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

        last_time = None
        buffer_size = self.stream_info.get("frames_per_buffer", 1024)
        try:
            while True:
                data = self.socket.recv(buffer_size)
                current_time = time.time()
                if last_time is not None:
                    self._avg_latency = (
                        self._avg_latency * 0.9 + (current_time - last_time) * 0.1
                    )
                last_time = current_time

                if not data:
                    break

                self.stream.write(data)
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
