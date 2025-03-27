from typing import TypedDict


class AudioInfo(TypedDict):
    channels: int
    rate: int
    frames_per_buffer: int


class StreamInfo(AudioInfo):
    format: int
    input: bool
    output: bool
    input_device_index: int


class CodecInfo(AudioInfo):
    name: str
