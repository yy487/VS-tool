from __future__ import annotations

import json
import struct
from pathlib import Path

from .common import PathLike, c_string, decode_c_string, encode_text, require_signature, write_bytes

TEXTDATA_SIGNATURE = b"PJADV_TF0001"
TEXTDATA_HDR_SIZE = 16


class TextData:
    def __init__(self, path: PathLike | None = None, data: bytes | bytearray | None = None):
        if path is not None and data is not None:
            raise ValueError("TextData: pass either path or data, not both")
        self.path = Path(path) if path is not None else None
        self.data = bytearray(Path(path).read_bytes() if path is not None else data or b"")
        self.appended: list[bytes] = []
        if self.data:
            require_signature(self.data, TEXTDATA_SIGNATURE, "TextData")
            self.text_count = struct.unpack_from("<I", self.data, 12)[0]
            self.last_offset = len(self.data)
        else:
            self.text_count = 0
            self.last_offset = TEXTDATA_HDR_SIZE
            self.data = bytearray(TEXTDATA_SIGNATURE + struct.pack("<I", 0))

    def get_raw(self, offset: int) -> bytes:
        if offset < 0 or offset >= len(self.data):
            raise ValueError(f"TextData: text offset out of range: 0x{offset:X}")
        return c_string(self.data, offset)

    def get_text(self, offset: int, encoding: str = "cp932") -> str:
        return decode_c_string(self.data, offset, encoding)

    def append_raw(self, raw: bytes) -> int:
        off = self.last_offset
        self.appended.append(bytes(raw))
        self.last_offset += len(raw) + 2
        return off

    def append_text(self, text: str, encoding: str = "cp932") -> int:
        return self.append_raw(encode_text(text, encoding))

    def to_bytes(self) -> bytes:
        out = bytearray()
        old_count = struct.unpack_from("<I", self.data, 12)[0]
        out.extend(TEXTDATA_SIGNATURE)
        out.extend(struct.pack("<I", old_count + len(self.appended)))
        out.extend(self.data[TEXTDATA_HDR_SIZE:])
        for item in self.appended:
            out.extend(item)
            out.extend(b"\x00\x00")
        return bytes(out)

    def save(self, path: PathLike) -> None:
        write_bytes(path, self.to_bytes())

    def dump_json(self, path: PathLike, encoding: str = "cp932") -> None:
        arr: list[str] = []
        pos = TEXTDATA_HDR_SIZE
        for _ in range(self.text_count):
            raw = c_string(self.data, pos)
            arr.append(raw.decode(encoding, errors="strict"))
            pos += len(raw) + 2
        payload = {"info": {"signature": TEXTDATA_SIGNATURE.decode("ascii"), "text_count": self.text_count}, "texts": arr}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def xor_bytes(data: bytes | bytearray, key: int) -> bytes:
    key &= 0xFF
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = b ^ key
        key = (key + 0x5C) & 0xFF
    return bytes(out)


def xor_file(src: PathLike, dst: PathLike, key: int) -> None:
    write_bytes(dst, xor_bytes(Path(src).read_bytes(), key))
