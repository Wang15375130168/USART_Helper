"""Parser for the USART Helper binary protocol.

Device frame format:
    AA FF F1 Len Payload SC1 SC2

Len is the payload byte count only. Total frame size is therefore:
    4 header bytes + Len payload bytes + 2 checksum bytes = Len + 6

Payload bytes are decoded from CH1 upward using the currently configured
channel data types. For example:
    Len = 0x04, default types int16/int16/... -> CH1 + CH2
    Len = 0x14, default firmware test types  -> CH1..CH7

Checksum uses two rolling 8-bit sums over all bytes from AA through the last
payload byte:
    SC1 = SC1 + byte
    SC2 = SC2 + SC1
"""
import struct

from PyQt5.QtCore import QObject, pyqtSignal


DATA_TYPE_DEFS = {
    'int8': (1, '<b'),
    'uint8': (1, '<B'),
    'int16': (2, '<h'),
    'uint16': (2, '<H'),
    'int32': (4, '<i'),
    'uint32': (4, '<I'),
    'int64': (8, '<q'),
    'uint64': (8, '<Q'),
    'float32': (4, '<f'),
    'float64': (8, '<d'),
}

DEFAULT_SEGMENT_TYPES = [
    'int16', 'int16', 'uint16', 'uint16', 'int32',
    'int32', 'int32', 'int16', 'int16', 'int16',
]

NUM_SEGMENTS = len(DEFAULT_SEGMENT_TYPES)
HEADER_BYTES = bytes((0xAA, 0xFF, 0xF1))
HEADER_LEN = 4
CHECKSUM_LEN = 2
MIN_FRAME_LEN = HEADER_LEN + CHECKSUM_LEN
DATA_BYTES = sum(DATA_TYPE_DEFS[t][0] for t in DEFAULT_SEGMENT_TYPES)
FRAME_LEN = HEADER_LEN + DATA_BYTES + CHECKSUM_LEN


class DataParser(QObject):
    """Streaming parser for AA FF F1 Len Payload SC1 SC2 frames."""

    frame_decoded = pyqtSignal(list)
    frames_decoded = pyqtSignal(list)
    parse_error = pyqtSignal(str)
    # Emits parsed channel count and payload byte length.
    format_detected = pyqtSignal(int, int)

    HEADER = 0xAA
    ADDR = 0xFF
    PKT_ID = 0xF1

    def __init__(self):
        super().__init__()
        self._buffer = bytearray()
        self._num_channels = NUM_SEGMENTS
        self._data_types = list(DEFAULT_SEGMENT_TYPES)
        self._data_length = DATA_BYTES
        self._reported_format = None
        self._checksum_enabled = True

    @property
    def num_channels(self):
        return self._num_channels

    @property
    def data_length(self):
        return self._data_length

    @property
    def data_types(self):
        return list(self._data_types)

    @property
    def data_bytes(self):
        return sum(DATA_TYPE_DEFS[t][0] for t in self._data_types)

    @property
    def frame_size(self):
        return HEADER_LEN + self._data_length + CHECKSUM_LEN

    def set_channel_data_type(self, ch_idx, data_type):
        """Set one channel type. New frames use the updated type order."""
        if ch_idx < 0 or ch_idx >= self._num_channels:
            return
        if data_type not in DATA_TYPE_DEFS:
            self.parse_error.emit(f"Unknown data type: {data_type}")
            return

        self._data_types[ch_idx] = data_type
        self._reported_format = None
        self._buffer.clear()

    def set_channel_data_types(self, data_types):
        """Set channel types in bulk."""
        for i, data_type in enumerate(data_types[:self._num_channels]):
            self.set_channel_data_type(i, data_type)

    def set_checksum_enabled(self, enabled):
        self._checksum_enabled = bool(enabled)

    def feed(self, data: bytes):
        """Feed arbitrary serial bytes and emit decoded frames."""
        if not data:
            return

        self._buffer.extend(data)
        decoded_frames = []

        while True:
            if not self._sync_to_frame_header():
                break

            if len(self._buffer) < HEADER_LEN:
                break

            payload_len = self._buffer[3]
            frame_len = HEADER_LEN + payload_len + CHECKSUM_LEN

            if len(self._buffer) < frame_len:
                break

            frame = bytes(self._buffer[:frame_len])

            if self._checksum_enabled and not self._checksum_valid(frame):
                got_sc1, got_sc2 = frame[-2], frame[-1]
                exp_sc1, exp_sc2 = self._expected_checksum(frame)
                self.parse_error.emit(
                    f"Checksum failed: Len=0x{payload_len:02X}, "
                    f"got SC1=0x{got_sc1:02X} SC2=0x{got_sc2:02X}, "
                    f"expected SC1=0x{exp_sc1:02X} SC2=0x{exp_sc2:02X}"
                )
                del self._buffer[0]
                continue

            payload = frame[HEADER_LEN:HEADER_LEN + payload_len]
            values = self._decode_payload(payload)
            if values is None:
                del self._buffer[0]
                continue

            del self._buffer[:frame_len]
            self._record_values(values, payload_len, decoded_frames)

        if decoded_frames:
            self.frames_decoded.emit(decoded_frames)

    def _sync_to_frame_header(self):
        """Keep the buffer aligned to AA FF F1, preserving partial prefixes."""
        while True:
            if len(self._buffer) < len(HEADER_BYTES):
                return False

            idx = self._buffer.find(HEADER_BYTES)
            if idx == 0:
                return True

            if idx > 0:
                del self._buffer[:idx]
                return True

            keep = self._partial_header_prefix_len()
            drop_count = len(self._buffer) - keep
            if drop_count > 0:
                del self._buffer[:drop_count]
                self.parse_error.emit(
                    f"Dropped {drop_count} byte(s) before frame header")
            return False

    def _partial_header_prefix_len(self):
        max_keep = min(len(self._buffer), len(HEADER_BYTES) - 1)
        for keep in range(max_keep, 0, -1):
            if self._buffer[-keep:] == HEADER_BYTES[:keep]:
                return keep
        return 0

    def _decode_payload(self, payload):
        """Decode exactly len(payload) bytes according to channel type order."""
        values = []
        offset = 0
        payload_len = len(payload)

        for ch_idx, data_type in enumerate(self._data_types, start=1):
            if offset == payload_len:
                return values

            size, fmt = DATA_TYPE_DEFS[data_type]
            remaining = payload_len - offset

            if remaining < size:
                self.parse_error.emit(
                    f"Len 0x{payload_len:02X} leaves {remaining} byte(s), "
                    f"but CH{ch_idx} type {data_type} needs {size} byte(s)")
                return None

            raw = payload[offset:offset + size]
            values.append(float(struct.unpack(fmt, raw)[0]))
            offset += size

        if offset < payload_len:
            self.parse_error.emit(
                f"Len 0x{payload_len:02X} has "
                f"{payload_len - offset} byte(s) beyond configured channels")
            return None

        return values

    @classmethod
    def _expected_checksum(cls, frame):
        sc1 = 0x00
        sc2 = 0x00
        for byte in frame[:-CHECKSUM_LEN]:
            sc1 = (sc1 + byte) & 0xFF
            sc2 = (sc2 + sc1) & 0xFF
        return sc1, sc2

    @classmethod
    def _checksum_valid(cls, frame):
        expected_sc1, expected_sc2 = cls._expected_checksum(frame)
        return frame[-2] == expected_sc1 and frame[-1] == expected_sc2

    def _record_values(self, values, payload_len, decoded_frames):
        self._data_length = payload_len
        decoded_frames.append(values)

        current_format = (len(values), payload_len)
        if current_format != self._reported_format:
            self._reported_format = current_format
            self.format_detected.emit(len(values), payload_len)
