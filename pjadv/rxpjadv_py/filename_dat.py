from __future__ import annotations

import struct
from pathlib import Path

from .common import PathLike, c_string, require_signature

FILENAME_SIGNATURE = b"PJADV_FL0001"
NAME_SIZE = 32


def read_filename_dat(path: PathLike, encoding: str = "cp932") -> list[str]:
    data = Path(path).read_bytes()
    require_signature(data, FILENAME_SIGNATURE, "FileNameDat")
    count = struct.unpack_from("<I", data, 12)[0]
    pos = 16
    need = pos + count * NAME_SIZE
    if need != len(data):
        raise ValueError(f"FileNameDat: count/table mismatch, count={count}, file_size={len(data)}")
    names: list[str] = []
    for i in range(count):
        raw = data[pos + i * NAME_SIZE:pos + (i + 1) * NAME_SIZE]
        names.append(c_string(raw).decode(encoding, errors="strict"))
    return names
