from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Literal

####  Signal Constants  ####
SIGNAL_SIZE = 1
ACK = b"\x11"
NEED_ACK = b"\x12"
PING = b"\x13"
PONG = b"\x14"
SHUTDOWN = b"\x1f"


####  String encoding  ####
if TYPE_CHECKING:
    STR_ENCODING: dict[Literal["ascii", "utf-8", "utf-16", "utf-32"], bytes]
    STR_ENCODING_REV: dict[bytes, Literal["ascii", "utf-8", "utf-16", "utf-32"]]

ENCODING_SIZE = 1
ASCII = b"\x21"
UTF8 = b"\x22"
UTF16 = b"\x23"
UTF32 = b"\x24"
STR_ENCODING = {
    "ascii": ASCII,
    "utf-8": UTF8,
    "utf-16": UTF16,
    "utf-32": UTF32,
}
STR_ENCODING_REV = {v: k for k, v in STR_ENCODING.items()}


#### Mode Constants ####
class SocketMode(enum.Enum):
    UNKNOWN = -1
    UNSET = 0
    SERVER = 1
    CLIENT = 2


#  Unique identifier for the packet, if the packet is split into multiple parts, all parts will have the same ID, ID is generated randomly from strings, then encoded to bytes
PACKET_ID_SIZE = 2
#  Type of the packet, used to determine the purpose of the packet, refer to the "Packet Types" below
PACKET_TYPE_SIZE = 1
#  Do we need an acknowledgment for this packet?
PACKET_ACK_NEED_SIZE = 1
#  Length of the packet, used to determine the size of the packet
PACKET_LENGTH_SIZE = 4
#  Total size of the packet header
PACKET_HEADER_SIZE = (
    PACKET_ID_SIZE + PACKET_TYPE_SIZE + PACKET_ACK_NEED_SIZE + PACKET_LENGTH_SIZE
)

# A packet is a sequence of bytes that is sent over the network
# The first two bytes of the packet is the packet ID
###  Packet Types  ###
COMPLETE = b"\x31"
# More data to receive
CONTINUE = b"\x32"
# End of data
END = b"\x33"
