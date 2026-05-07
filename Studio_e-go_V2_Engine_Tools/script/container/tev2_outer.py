from __future__ import annotations

import re
import struct


TE_V2_WORD_XOR = 0x10EF01FE
TE_V2_STATE_CONST = 124076833
TXT0_XOR_PATTERN = bytes([0xFE, 0x01, 0xEF, 0x10])

ASCII_LITERAL_RE = re.compile(rb"[ -~]{4,}")


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


def i32(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


def transform_mode2_words(data: bytes, seed_u32: int) -> bytes:
    if len(data) & 3:
        raise ValueError("TE_V2 mode-2 payload size must be a multiple of 4")
    # Hot path for SCR decompile/compile.  Prealloc + struct avoids the
    # per-word slice/from_bytes/to_bytes allocation overhead of the original
    # implementation while keeping the exact same transform.
    out = bytearray(len(data))
    state = u32(seed_u32)
    word_no = 0
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        state_input = i32(state) if (word_no & 0xFF) else ~i32(state)
        state = u32(TE_V2_STATE_CONST + state_input)
        struct.pack_into("<I", out, offset, word ^ state)
        word_no += 1
    return bytes(out)


def decode_mode5_swapped(data: bytes, seed_u32: int, base_state_u32: int = TE_V2_STATE_CONST) -> bytes:
    if len(data) & 3:
        raise ValueError("TE_V2 mode-5 payload size must be a multiple of 4")
    out = bytearray(len(data))
    state = u32(seed_u32)
    word_no = 0
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if (word_no & 3) != 0:
            signed = i32(state)
            state = u32(2 * signed if signed >= 0 else ((2 * signed) | 1))
        state = u32(base_state_u32 + ~i32(state))
        swapped = int.from_bytes(word.to_bytes(4, "little")[::-1], "little")
        struct.pack_into("<I", out, offset, swapped ^ state)
        word_no += 1
    return bytes(out)


def encode_mode5_swapped(data: bytes, seed_u32: int, base_state_u32: int = TE_V2_STATE_CONST) -> bytes:
    if len(data) & 3:
        raise ValueError("TE_V2 mode-5 payload size must be a multiple of 4")
    out = bytearray(len(data))
    state = u32(seed_u32)
    word_no = 0
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if (word_no & 3) != 0:
            signed = i32(state)
            state = u32(2 * signed if signed >= 0 else ((2 * signed) | 1))
        state = u32(base_state_u32 + ~i32(state))
        encoded = (word ^ state).to_bytes(4, "little")[::-1]
        out[offset : offset + 4] = encoded
        word_no += 1
    return bytes(out)


def extract_ascii_literals(data: bytes, min_len: int = 4) -> list[dict[str, object]]:
    if min_len <= 1:
        raise ValueError("min_len must be greater than 1")
    pattern = re.compile(rb"[ -~]{" + str(min_len).encode("ascii") + rb",}")
    return [
        {
            "offset": match.start(),
            "length": match.end() - match.start(),
            "text": match.group().decode("ascii"),
        }
        for match in pattern.finditer(data)
    ]


def preview_u32_words(data: bytes, limit: int = 32) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    word_count = min(len(data) // 4, limit)
    for index in range(word_count):
        offset = index * 4
        value_u32 = int.from_bytes(data[offset : offset + 4], "little")
        raw = value_u32.to_bytes(4, "little")
        ascii_preview = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in raw)
        items.append(
            {
                "offset": offset,
                "value_u32": value_u32,
                "value_hex": f"0x{value_u32:08X}",
                "ascii_preview": ascii_preview,
            }
        )
    return items


def extract_nonzero_words(data: bytes, limit: int = 64) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for offset in range(0, len(data) - (len(data) & 3), 4):
        value_u32 = int.from_bytes(data[offset : offset + 4], "little")
        if value_u32 == 0:
            continue
        raw = value_u32.to_bytes(4, "little")
        ascii_preview = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in raw)
        items.append(
            {
                "offset": offset,
                "value_u32": value_u32,
                "value_hex": f"0x{value_u32:08X}",
                "ascii_preview": ascii_preview,
            }
        )
        if len(items) >= limit:
            break
    return items
