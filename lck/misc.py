"""The clock's `misc` partition: A/B slot state and the OTA channel.

Layout (struct bootloader_message_ab, AOSP bootable/recovery bootloader_message.h):

    0x000  bootloader_message   2048 bytes
    0x800  slot_suffix[32]      LK keeps libavb's AvbABData here: "\\0AB0", version 1.0,
                                per slot {priority, tries_remaining, successful_boot, reserved},
                                12 reserved bytes, big-endian CRC32 of the first 28 bytes
    0x820  update_channel[128]  Android Things' update_engine reads the target channel here.
                                Empty means ro.update.default_channel ("stable-channel").
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MISC_SIZE = 0x100000
AB_OFFSET = 0x800
AB_SIZE = 32
CHANNEL_OFFSET = 0x820
CHANNEL_SIZE = 128
MAGIC = b"\0AB0"


@dataclass(frozen=True)
class Slot:
    priority: int
    tries: int
    successful: bool


def ab_block(a: Slot, b: Slot) -> bytes:
    blob = MAGIC + bytes([1, 0, 0, 0])
    for s in (a, b):
        blob += bytes([s.priority, s.tries, int(s.successful), 0])
    blob += bytes(12)
    return blob + struct.pack(">I", zlib.crc32(blob))


def decode_ab(misc: bytes) -> tuple[Slot, Slot]:
    blob = misc[AB_OFFSET:AB_OFFSET + AB_SIZE] if len(misc) > AB_SIZE else misc
    if blob[:4] != MAGIC:
        raise ValueError("no \\0AB0 block")
    if struct.unpack(">I", blob[28:32])[0] != zlib.crc32(blob[:28]):
        raise ValueError("A/B block CRC mismatch")
    return tuple(Slot(blob[o], blob[o + 1], bool(blob[o + 2])) for o in (8, 12))  # type: ignore[return-value]


def read_channel(misc: bytes) -> str:
    raw = misc[CHANNEL_OFFSET:CHANNEL_OFFSET + CHANNEL_SIZE]
    return raw.split(b"\0", 1)[0].decode()


def trial_slot_a(misc: bytes, channel: str = "none-channel", tries: int = 3) -> bytes:
    """Return a copy of `misc` that boots slot A on trial with slot B as the fallback,
    and points update_engine at `channel`."""
    if len(misc) != MISC_SIZE:
        raise ValueError(f"misc must be {MISC_SIZE} bytes, got {len(misc)}")
    if not channel.endswith("-channel") or len(channel) >= CHANNEL_SIZE:
        raise ValueError("channel must end with -channel and be shorter than 128 bytes")
    out = bytearray(misc)
    out[AB_OFFSET:AB_OFFSET + AB_SIZE] = ab_block(Slot(15, tries, False), Slot(14, 0, True))
    ch = channel.encode()
    out[CHANNEL_OFFSET:CHANNEL_OFFSET + CHANNEL_SIZE] = ch + bytes(CHANNEL_SIZE - len(ch))
    return bytes(out)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Build a misc image: boot slot A on trial, pin the OTA channel.")
    ap.add_argument("src", help="current misc image (1 MiB, from the backup)")
    ap.add_argument("out")
    ap.add_argument("--channel", default="none-channel")
    args = ap.parse_args()
    out = trial_slot_a(open(args.src, "rb").read(), channel=args.channel)
    open(args.out, "wb").write(out)
    a, b = decode_ab(out)
    print(f"slot A {a} | slot B {b} | channel {read_channel(out)}")


if __name__ == "__main__":
    main()
