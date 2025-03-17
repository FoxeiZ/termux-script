from typing import NotRequired, TypedDict


class StreamInfo(TypedDict):
    channels: int
    rate: int
    format: int
    frames_per_buffer: NotRequired[int]
    input_device_index: int
    input: bool
    output: bool
