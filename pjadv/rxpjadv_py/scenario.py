from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .common import PathLike, require_signature, write_bytes

SCENARIO_SIGNATURE = b"PJADV_SF0001"
SCENARIO_CMD_START = 32  # 12-byte signature + uint32 flag + 16 unknown bytes


@dataclass(frozen=True)
class Command:
    index: int
    offset: int
    words: tuple[int, ...]

    @property
    def opcode(self) -> int:
        return self.words[0]

    @property
    def length(self) -> int:
        return len(self.words) * 4


class Scenario:
    def __init__(self, path: PathLike):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        require_signature(self.data, SCENARIO_SIGNATURE, "Scenario")
        self.commands = self._scan()

    def _scan(self) -> list[Command]:
        if len(self.data) < SCENARIO_CMD_START:
            raise ValueError("Scenario: file too small")
        pos = SCENARIO_CMD_START
        out: list[Command] = []
        idx = 0
        size = len(self.data)
        while pos < size:
            if pos + 4 > size:
                raise ValueError(f"Scenario: truncated opcode at 0x{pos:X}")
            count = self.data[pos]
            cmd_cnt = 0 if count > 0x7F else count
            cmd_len = 4 if cmd_cnt == 0 else cmd_cnt * 4
            if cmd_len <= 0 or pos + cmd_len > size:
                raise ValueError(f"Scenario: command scan error at 0x{pos:X}, len={cmd_len}")
            words = struct.unpack_from("<" + "I" * (cmd_len // 4), self.data, pos)
            out.append(Command(idx, pos, words))
            idx += 1
            pos += cmd_len
        if pos != size:
            raise ValueError("Scenario: command scan ended at invalid boundary")
        return out

    def command_words(self, index: int) -> list[int]:
        cmd = self.commands[index]
        return list(struct.unpack_from("<" + "I" * (cmd.length // 4), self.data, cmd.offset))

    def set_word(self, cmd_index: int, word_index: int, value: int) -> None:
        cmd = self.commands[cmd_index]
        if word_index < 0 or word_index >= len(cmd.words):
            raise IndexError(word_index)
        struct.pack_into("<I", self.data, cmd.offset + word_index * 4, value & 0xFFFFFFFF)

    def save(self, path: PathLike) -> None:
        write_bytes(path, self.data)
