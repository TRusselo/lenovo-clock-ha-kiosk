"""The clock's partition table, read from the first 34 sectors of its eMMC user
area (a backup's full_user.bin starts there)."""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

SECTOR = 512
GPT_BYTES = 34 * SECTOR


@dataclass(frozen=True)
class Partition:
    name: str
    first_lba: int
    sectors: int

    @property
    def offset(self) -> int:
        return self.first_lba * SECTOR

    @property
    def size(self) -> int:
        return self.sectors * SECTOR


def _header(head: bytes) -> bytes:
    h = head[SECTOR:2 * SECTOR]
    if h[:8] != b"EFI PART":
        raise ValueError("no GPT header: this isn't the start of the clock's disk")
    return h


def read_gpt(head: bytes) -> list[Partition]:
    h = _header(head)
    lba, n, size = struct.unpack("<QII", h[72:88])
    table = head[lba * SECTOR:lba * SECTOR + n * size]
    if len(table) < n * size:
        raise ValueError("the partition table is cut short")
    parts = []
    for k in range(n):
        e = table[k * size:(k + 1) * size]
        if e[:16] == bytes(16):
            continue
        first, last = struct.unpack("<QQ", e[32:48])
        name = e[56:128].decode("utf-16-le").split("\0", 1)[0]
        parts.append(Partition(name, first, last - first + 1))
    return sorted(parts, key=lambda p: p.first_lba)


def disk_guid(head: bytes) -> str:
    return str(uuid.UUID(bytes_le=_header(head)[56:72]))


def signature(head: bytes) -> tuple:
    """What makes a clock's disk its own: its GUID and its partition layout."""
    return (disk_guid(head), tuple(read_gpt(head)))


def by_name(head: bytes) -> dict[str, Partition]:
    return {p.name: p for p in read_gpt(head)}
