from __future__ import annotations

import json
import socket
from threading import Event, Thread
from time import sleep
from typing import TYPE_CHECKING, cast

import pyaudio

if TYPE_CHECKING:
    from socket import _RetAddress
    from typing import Literal

    from _types import StreamInfo


class ClientThread(Thread):
    if TYPE_CHECKING:
        client_socket: socket.socket
        address: tuple
        server: AudioServer

    def __init__(
        self, client_socket: socket.socket, address: _RetAddress, server: "AudioServer"
    ):
        Thread.__init__(self)
        self.client_socket = client_socket
        self.address = address
        self.server = server

    def send_stream_info(self):
        stream_info = json.dumps(self.server.stream_info).encode(
            errors="xmlcharrefreplace"
        )
        self.client_socket.send(len(stream_info).to_bytes(4, "big"))
        self.client_socket.send(stream_info)

    def run(self):
        print(f"Connection from {self.address}")
        self.send_stream_info()
        while not self.server.is_shutdown:
            try:
                data = self.server.get_next_frame()
                if not data:
                    break

                self.client_socket.send(data)
            except ConnectionResetError:
                break

        print(f"Connection from {self.address} closed")
        self.client_socket.close()
        del self.server._client_threads[self.address]


class AudioServer:
    if TYPE_CHECKING:
        host: str
        port: int
        server_socket: socket.socket
        audio: pyaudio.PyAudio
        stream: pyaudio.Stream
        _stream_info: StreamInfo
        _notifier: Event
        _shutdown: bool
        _audio_frame: bytes | None
        # _audio_listener_thread: Thread
        _socket_listener_thread: Thread
        _client_threads: dict[_RetAddress, ClientThread]

    def __init__(self, host="127.0.0.1", port=12345, buffer_size=1024):
        self.host = host
        self.port = port
        self.buffer_size = buffer_size

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((self.host, self.port))

        self.audio = pyaudio.PyAudio()
        self._stream_info = self.select_audio_device(index=38)
        # self._stream_info = self.select_audio_device(
        #     "Input", filter_channels=2, filter_name="Virtual", filter_rate=48000
        # )
        self.stream = self.audio.open(
            **self._stream_info,
            stream_callback=self._audio_callback,
        )

        self._notifier = Event()
        self._shutdown = False
        self._audio_frame = None

        self._client_threads = {}
        self._socket_listener_thread = Thread(target=self._socket_listener, daemon=True)
        # self._audio_listener_thread = Thread(target=self._audio_listener, daemon=True)
        # self._audio_listener_thread.start()

    @property
    def stream_info(self):
        return self._stream_info

    def wait_for_next_frame(self, timeout: float | None = None):
        return self._notifier.wait(timeout)

    def get_next_frame(self):
        self.wait_for_next_frame()
        return self._audio_frame

    def select_audio_device(
        self,
        index: int | str | None = None,
        filter_type: Literal["Input", "Output"] = "Input",
        filter_name: str | None = None,
        filter_channels: int | None = None,
        filter_rate: int | None = None,
        **kwargs,
    ) -> StreamInfo:
        if not index:
            device_count = self.audio.get_device_count()
            if device_count == 0:
                print("No audio devices found")
                exit(1)

            for i in range(device_count):
                device = self.audio.get_device_info_by_index(i)
                name = cast(str, device["name"])
                channels = device[f"max{filter_type}Channels"]
                rate = int(device["defaultSampleRate"])
                if (
                    (filter_name and filter_name not in name)
                    or (filter_channels and filter_channels != channels)
                    or (filter_rate and filter_rate != rate)
                    or channels == 0
                ):
                    continue
                print(f"{i}: {name} ({channels} channels) [{rate} Hz]")

            try:
                index = input("Enter the index of the device you want to use: ")
            except KeyboardInterrupt:
                print("Exiting...")
                exit(0)

        selected_device = self.audio.get_device_info_by_index(int(index))
        return {
            "channels": int(selected_device[f"max{filter_type}Channels"]),
            "rate": int(selected_device["defaultSampleRate"]),
            "format": kwargs.get("format", pyaudio.paInt16),
            "input_device_index": int(index),
            "input": True if filter_type == "Input" else False,
            "output": True if filter_type == "Output" else False,
            "frames_per_buffer": self.buffer_size,
        }

    # def _audio_listener(self):
    #     while not self._shutdown:
    #         frame = self.stream.read(1024)
    #         self._audio_frame = frame
    #         self._notifier.set()
    #         self._notifier.clear()

    @property
    def is_shutdown(self):
        return self._shutdown

    def _audio_callback(self, in_data, frame_count, time_info, status):
        self._audio_frame = in_data
        # print(
        #     f"audio received: {len(in_data)} bytes, frame_count: {frame_count}, time_info: {time_info}, status: {status}"
        # )
        self._notifier.set()
        self._notifier.clear()
        return (in_data, pyaudio.paContinue)

    def _socket_listener(self):
        while not self._shutdown:
            try:
                client_socket, address = self.server_socket.accept()
            except KeyboardInterrupt:
                break

            client_thread = ClientThread(client_socket, address, self)
            self._client_threads[address] = client_thread
            client_thread.start()

    def start(self):
        self.server_socket.listen(5)
        print(f"Listening on {self.host}:{self.port}")

        # self._audio_listener_thread.start()
        self._socket_listener_thread.start()
        while not self._shutdown:
            try:
                sleep(1)
            except KeyboardInterrupt:
                self.close()

    def close(self):
        if self._shutdown:
            return

        self.stream.stop_stream()
        self.stream.close()
        self.audio.terminate()
        self.server_socket.close()
        self._shutdown = True
        self._notifier.set()
        # self._audio_listener_thread.join()
        for client_thread in self._client_threads.values():
            client_thread.join()


if __name__ == "__main__":
    server = AudioServer(host="0.0.0.0", buffer_size=1024)
    try:
        server.start()
    except KeyboardInterrupt:
        server.close()
