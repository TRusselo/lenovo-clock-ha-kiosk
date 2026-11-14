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
