import struct
import zlib

import pytest

from lck import misc
from lck.misc import Slot

# The A/B block read from a real clock's misc (both slots successful, B active).
REAL_AB = bytes.fromhex("00414230" "01000000" "0e000100" "0f000100" + "00" * 12 + "119c9cae")


def blank_misc():
    m = bytearray(misc.MISC_SIZE)
    m[misc.AB_OFFSET:misc.AB_OFFSET + 32] = REAL_AB
    return bytes(m)


def test_decode_real_block():
    a, b = misc.decode_ab(blank_misc())
    assert a == Slot(14, 0, True)
    assert b == Slot(15, 0, True)


def test_ab_block_round_trips():
    blk = misc.ab_block(Slot(15, 3, False), Slot(14, 0, True))
    assert len(blk) == 32 and blk[:4] == b"\0AB0"
    assert struct.unpack(">I", blk[28:])[0] == zlib.crc32(blk[:28])
    assert misc.decode_ab(blk) == (Slot(15, 3, False), Slot(14, 0, True))


def test_trial_slot_a_sets_slots_and_channel_only():
    src = blank_misc()
    out = misc.trial_slot_a(src)
    assert misc.decode_ab(out) == (Slot(15, 3, False), Slot(14, 0, True))
    assert misc.read_channel(out) == "none-channel"
    # nothing else changes
    assert out[:misc.AB_OFFSET] == src[:misc.AB_OFFSET]
    assert out[misc.CHANNEL_OFFSET + misc.CHANNEL_SIZE:] == src[misc.CHANNEL_OFFSET + misc.CHANNEL_SIZE:]


def test_channel_offset_matches_struct():
    # bootloader_message (2048) + slot_suffix[32]
    assert misc.CHANNEL_OFFSET == 2048 + 32


def test_empty_channel_reads_empty():
    assert misc.read_channel(blank_misc()) == ""


@pytest.mark.parametrize("bad", ["stable", "x" * 130 + "-channel"])
def test_rejects_bad_channel(bad):
    with pytest.raises(ValueError):
        misc.trial_slot_a(blank_misc(), channel=bad)


def test_rejects_wrong_size():
    with pytest.raises(ValueError):
        misc.trial_slot_a(b"\0" * 4096)


def test_bad_crc_is_refused():
    m = bytearray(blank_misc())
    m[misc.AB_OFFSET + 8] ^= 1
    with pytest.raises(ValueError):
        misc.decode_ab(bytes(m))
