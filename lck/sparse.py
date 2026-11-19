"""Checks for Android sparse images before they go to LK's fastboot.

`userdata` is the last partition and its length (0x13fe56e00) is not a multiple of 4 KiB.
img2simg rounds the declared size up, which would reach past the partition into the backup GPT.
Every chunk must declare total_blks * blk_sz <= the partition's length.
"""
from __future__ import annotations

import re
import struct

SPARSE_MAGIC = 0xED26FF3A
HEADER = struct.Struct("<IHHHHIIII")


def declared_size(header: bytes) -> int:
    magic, _maj, _min, _hdr, _chdr, blk_sz, total_blks, _chunks, _crc = HEADER.unpack(header[:HEADER.size])
    if magic != SPARSE_MAGIC:
        raise ValueError("not an Android sparse image")
    return blk_sz * total_blks


def fits(header: bytes, partition_len: int) -> bool:
    return declared_size(header) <= partition_len


def whole_blocks(partition_len: int, block: int = 4096) -> int:
    """Size to truncate a raw ext4 image to before img2simg."""
    return (partition_len // block) * block


GPT_LINE = re.compile(r"^(\w+):\s+Offset 0x([0-9a-f]+), Length 0x([0-9a-f]+)", re.I)


def gpt_lengths(gpt_txt: str) -> dict[str, int]:
    """Partition lengths from mtkclient's `printgpt` output."""
    out = {}
    for line in gpt_txt.splitlines():
        m = GPT_LINE.match(line.strip())
        if m:
            out[m.group(1)] = int(m.group(3), 16)
    return out


def main() -> None:
    """usage: python -m lck.sparse <gpt.txt> <partition> <chunk>...  (exit 1 if any chunk is too big)"""
    import sys

    lens = gpt_lengths(open(sys.argv[1]).read())
    part = lens[sys.argv[2]]
    bad = 0
    for f in sys.argv[3:]:
        size = declared_size(open(f, "rb").read(HEADER.size))
        ok = size <= part
        bad += not ok
        print(f"{f}: declares {size}, partition {part}: {'OK' if ok else 'TOO BIG'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()


# --- Writing: Android sparse images in pieces, in Python (no img2simg, no sparsesplit.py) ---------
#
# Zero blocks, and any block that's one 32-bit value repeated, become FILL chunks, as img2simg makes
# them; everything else is RAW. Each piece covers the whole partition: the blocks other pieces write
# are DONT_CARE in it, as sparsesplit.py does, so the pieces can be flashed one after another.
RAW, FILL, DONT_CARE = 0xCAC1, 0xCAC2, 0xCAC3
BLOCK = 4096
CHUNK_HDR = struct.Struct("<HHII")


def _runs(path, blocks: int):
    """(type, first block, count, fill value) for each run of the image, in order."""
    run = None
    with open(path, "rb") as f:
        for i in range(blocks):
            b = f.read(BLOCK)
            if len(b) < BLOCK:
                b = b + bytes(BLOCK - len(b))
            word = b[:4]
            if b == word * (BLOCK // 4):
                kind, value = FILL, word
            else:
                kind, value = RAW, None
            if run and run[0] == kind and (kind == RAW or run[3] == value):
                run[2] += 1
            else:
                if run:
                    yield tuple(run)
                run = [kind, i, 1, value]
    if run:
        yield tuple(run)


def write_pieces(path, prefix, limit: int, blocks: int | None = None) -> list:
    """Split the raw image at `path` into sparse pieces of at most `limit` bytes each, named
    prefix.0, prefix.1, … Returns their paths."""
    import os
    size = os.path.getsize(path)
    total = blocks if blocks is not None else size // BLOCK
    pieces, cur, cur_bytes = [], [], HEADER.size + 2 * CHUNK_HDR.size
    for kind, start, n, value in _runs(path, total):
        while n:
            room = limit - cur_bytes - CHUNK_HDR.size
            if kind == FILL:
                take, cost = n, CHUNK_HDR.size + 4
            else:
                take = min(n, max(room // BLOCK, 0))
                cost = CHUNK_HDR.size + take * BLOCK
            if take == 0 or cost > room:
                pieces.append(cur)
                cur, cur_bytes = [], HEADER.size + 2 * CHUNK_HDR.size
                continue
            cur.append((kind, start, take, value))
            cur_bytes += cost
            start += take
            n -= take
    if cur:
        pieces.append(cur)
    out = []
    with open(path, "rb") as src:
        for k, chunks in enumerate(pieces):
            name = f"{prefix}.{k}"
            _write_piece(src, name, chunks, total)
            out.append(name)
    return out


def _write_piece(src, name, chunks, total: int) -> None:
    body = []
    pos = 0
    for kind, start, n, value in chunks:
        if start > pos:
            body.append((DONT_CARE, start - pos, None))
        body.append((kind, n, (start, value)))
        pos = start + n
    if pos < total:
        body.append((DONT_CARE, total - pos, None))
    with open(name, "wb") as f:
        f.write(HEADER.pack(SPARSE_MAGIC, 1, 0, HEADER.size, CHUNK_HDR.size, BLOCK, total, len(body), 0))
        for kind, n, extra in body:
            if kind == DONT_CARE:
                f.write(CHUNK_HDR.pack(DONT_CARE, 0, n, CHUNK_HDR.size))
            elif kind == FILL:
                f.write(CHUNK_HDR.pack(FILL, 0, n, CHUNK_HDR.size + 4))
                f.write(extra[1])
            else:
                start = extra[0]
                f.write(CHUNK_HDR.pack(RAW, 0, n, CHUNK_HDR.size + n * BLOCK))
                src.seek(start * BLOCK)
                left = n * BLOCK
                while left:
                    data = src.read(min(left, 1 << 22))
                    if not data:
                        data = bytes(min(left, 1 << 22))
                    f.write(data)
                    left -= len(data)
