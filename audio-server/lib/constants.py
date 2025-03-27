import enum


class MessageType(enum.Enum):
    """Enum class for message types."""

    STREAM_INFO = 1
    CODEC_INFO = 2
    AUDIO_DATA = 3
    ERROR = 4
