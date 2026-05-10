# -*- coding: utf-8 -*-
"""AI5WIN/Okumura LZSS helper used by G24 tools."""

N = 4096
F = 18
INIT_POS = 0xFEE


def LZSS_decompress(src: bytes, expected_size=None) -> bytes:
    text_buf = bytearray(N)
    r = INIT_POS
    flags = 0
    ip = 0
    out = bytearray()
    src = bytes(src)
    while ip < len(src):
        flags >>= 1
        if (flags & 0x100) == 0:
            if ip >= len(src):
                break
            flags = src[ip] | 0xFF00
            ip += 1
        if flags & 1:
            if ip >= len(src):
                break
            c = src[ip]
            ip += 1
            out.append(c)
            text_buf[r] = c
            r = (r + 1) & 0xFFF
        else:
            if ip + 1 >= len(src):
                break
            lo = src[ip]
            hi = src[ip + 1]
            ip += 2
            pos = lo | ((hi & 0xF0) << 4)
            length = (hi & 0x0F) + 3
            for k in range(length):
                c = text_buf[(pos + k) & 0xFFF]
                out.append(c)
                text_buf[r] = c
                r = (r + 1) & 0xFFF
                if expected_size is not None and len(out) >= expected_size:
                    return bytes(out[:expected_size])
        if expected_size is not None and len(out) >= expected_size:
            return bytes(out[:expected_size])
    return bytes(out)


def compress_true(data: bytes) -> bytes:
    """Compatibility name: now uses greedy LZSS instead of literal-only."""
    return LZSS_compress(data)


def LZSS_compress_literal(data: bytes) -> bytes:
    data = bytes(data)
    out = bytearray()
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        out.append((1 << len(chunk)) - 1)
        out.extend(chunk)
    return bytes(out)


def LZSS_compress(data: bytes, max_candidates: int = 128) -> bytes:
    from collections import defaultdict, deque

    data = bytes(data)
    n = len(data)
    out = bytearray()
    pos = 0
    table = defaultdict(deque)

    def key_at(i):
        if i + 2 < n:
            return data[i:i + 3]
        return None

    def add_pos(i):
        k = key_at(i)
        if k is None:
            return
        dq = table[k]
        dq.append(i)
        while dq and i - dq[0] > N:
            dq.popleft()
        while len(dq) > 256:
            dq.popleft()

    while pos < n:
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if pos >= n:
                break
            best_len = 0
            best_abs = 0
            k = key_at(pos)
            dq = table.get(k) if k is not None else None
            if dq:
                while dq and pos - dq[0] > N:
                    dq.popleft()
                checked = 0
                for cand in reversed(dq):
                    dist = pos - cand
                    if dist <= 0 or dist > N:
                        continue
                    length = 0
                    while length < F and pos + length < n and data[cand + length] == data[pos + length]:
                        length += 1
                    if length > best_len:
                        best_len = length
                        best_abs = cand
                        if length == F:
                            break
                    checked += 1
                    if checked >= max_candidates:
                        break
            if best_len >= 3:
                ring_pos = (INIT_POS + best_abs) & 0xFFF
                out.append(ring_pos & 0xFF)
                out.append(((ring_pos >> 4) & 0xF0) | (best_len - 3))
                for j in range(best_len):
                    add_pos(pos + j)
                pos += best_len
            else:
                flags |= 1 << bit
                out.append(data[pos])
                add_pos(pos)
                pos += 1
        out[flag_pos] = flags
    return bytes(out)
