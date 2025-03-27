from .base import RawCodec
from .opus import OpusCodec

CODEC_MAP = {
    "raw": RawCodec,
    "opus": OpusCodec,
}
