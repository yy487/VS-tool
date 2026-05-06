from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def read_bytes(path: PathLike) -> bytearray:
    return bytearray(Path(path).read_bytes())


def write_bytes(path: PathLike, data: bytes | bytearray) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(data))


def c_string(raw: bytes | bytearray, offset: int = 0) -> bytes:
    end = raw.find(b"\x00", offset)
    if end < 0:
        end = len(raw)
    return bytes(raw[offset:end])


def decode_c_string(raw: bytes | bytearray, offset: int = 0, encoding: str = "cp932") -> str:
    return c_string(raw, offset).decode(encoding, errors="strict")


def encode_text(text: str, encoding: str = "cp932") -> bytes:
    return text.encode(encoding, errors="strict")


def require_signature(data: bytes | bytearray, sig: bytes, label: str) -> None:
    if not data.startswith(sig):
        raise ValueError(f"{label}: unknown signature, expected {sig!r}")
