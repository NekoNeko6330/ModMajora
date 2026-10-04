# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Create BPS patches (https://github.com/blakesmith/rombp/blob/master/docs/bps_spec.md)"""

import zlib

SOURCE_READ = 0
TARGET_READ = 1
SOURCE_COPY = 2
TARGET_COPY = 3

# Minimum length of a run (of bytes equal to the source, or of a repeated byte) worth its own action
MIN_RUN = 16
BLOCK = 0x1000


def _encode_number(n: int, out: bytearray):
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            break
        out.append(x)
        n -= 1


def _encode_signed(n: int, out: bytearray):
    _encode_number((abs(n) << 1) | (1 if n < 0 else 0), out)


def _action(kind: int, length: int, out: bytearray):
    assert length > 0
    _encode_number(((length - 1) << 2) | kind, out)


def create_patch(source: bytes, target: bytes) -> bytes:
    out = bytearray(b"BPS1")
    _encode_number(len(source), out)
    _encode_number(len(target), out)
    _encode_number(0, out)  # no metadata

    src = memoryview(source)
    tgt = memoryview(target)
    n_src = len(source)
    n_tgt = len(target)
    target_rel = 0  # target-relative offset for TargetCopy

    literal_start = None

    def flush_literal(end: int):
        nonlocal literal_start
        if literal_start is not None and end > literal_start:
            _action(TARGET_READ, end - literal_start, out)
            out.extend(tgt[literal_start:end])
        literal_start = None

    def source_matches(i: int) -> int:
        """Length of the run of target bytes equal to the source, starting at i"""
        if i >= n_src:
            return 0
        end_max = min(n_src, n_tgt)
        j = i
        # Fast path by blocks
        while j + BLOCK <= end_max and src[j : j + BLOCK] == tgt[j : j + BLOCK]:
            j += BLOCK
        while j < end_max and src[j] == tgt[j]:
            j += 1
        return j - i

    def repeat_run(i: int) -> int:
        """Length of the run of identical bytes starting at i"""
        b = tgt[i]
        j = i + 1
        pattern = bytes([b]) * BLOCK
        while j + BLOCK <= n_tgt and tgt[j : j + BLOCK] == pattern:
            j += BLOCK
        while j < n_tgt and tgt[j] == b:
            j += 1
        return j - i

    i = 0
    while i < n_tgt:
        run = source_matches(i)
        if run >= MIN_RUN or (run > 0 and i + run == n_tgt):
            flush_literal(i)
            _action(SOURCE_READ, run, out)
            i += run
            continue
        rep = repeat_run(i)
        if rep >= MIN_RUN:
            flush_literal(i)
            # Write the first byte, then copy it over (overlapping copy)
            _action(TARGET_READ, 1, out)
            out.append(tgt[i])
            _action(TARGET_COPY, rep - 1, out)
            _encode_signed(i - target_rel, out)
            target_rel = i + rep - 1
            i += rep
            continue
        if literal_start is None:
            literal_start = i
        i += 1
    flush_literal(n_tgt)

    out += zlib.crc32(source).to_bytes(4, "little")
    out += zlib.crc32(target).to_bytes(4, "little")
    out += zlib.crc32(out).to_bytes(4, "little")
    return bytes(out)


def apply_patch(source: bytes, patch: bytes) -> bytes:
    """Apply a BPS patch (used to verify created patches)"""
    assert patch[:4] == b"BPS1"
    pos = 4

    def read_number():
        nonlocal pos
        data = 0
        shift = 1
        while True:
            x = patch[pos]
            pos += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                break
            shift <<= 7
            data += shift
        return data

    def read_signed():
        n = read_number()
        return -(n >> 1) if n & 1 else n >> 1

    source_size = read_number()
    target_size = read_number()
    metadata_size = read_number()
    pos += metadata_size
    assert source_size == len(source)
    out = bytearray()
    source_rel = 0
    target_rel = 0
    end = len(patch) - 12
    while pos < end:
        data = read_number()
        kind = data & 3
        length = (data >> 2) + 1
        if kind == SOURCE_READ:
            out += source[len(out) : len(out) + length]
        elif kind == TARGET_READ:
            out += patch[pos : pos + length]
            pos += length
        elif kind == SOURCE_COPY:
            source_rel += read_signed()
            out += source[source_rel : source_rel + length]
            source_rel += length
        else:
            target_rel += read_signed()
            for _ in range(length):
                out.append(out[target_rel])
                target_rel += 1
    assert len(out) == target_size
    assert zlib.crc32(out).to_bytes(4, "little") == patch[-8:-4]
    return bytes(out)
