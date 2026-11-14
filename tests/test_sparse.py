import pytest

from lck import sparse

GPT_SNIPPET = """\
system_a:            Offset 0x0000000008185000, Length 0x0000000020000000, Flags 0x0000000000000000, UUID 9f30c820, Type 0xf2778c4
oem_a:               Offset 0x00000000539a5000, Length 0x000000001f400000, Flags 0x0000000000000000, UUID 6b38c49b, Type 0xaa3434b2
userdata:            Offset 0x00000000921a5000, Length 0x000000013fe56e00, Flags 0x0000000000000000, UUID b29135bc, Type 0xbb7e6ed
"""


def header(blk_sz, total_blks):
    return sparse.HEADER.pack(sparse.SPARSE_MAGIC, 1, 0, 28, 12, blk_sz, total_blks, 1, 0)


def test_gpt_lengths():
    lens = sparse.gpt_lengths(GPT_SNIPPET)
    assert lens == {"system_a": 0x20000000, "oem_a": 0x1F400000, "userdata": 0x13FE56E00}


def test_userdata_round_up_does_not_fit():
    part = 0x13FE56E00
    # what img2simg declares for an untruncated image: one partial block rounded up
    assert not sparse.fits(header(4096, -(-part // 4096)), part)


def test_truncated_userdata_fits():
    part = 0x13FE56E00
    assert sparse.whole_blocks(part) == 1310294 * 4096
    assert sparse.fits(header(4096, 1310294), part)


def test_exact_fit():
    assert sparse.fits(header(4096, 0x20000000 // 4096), 0x20000000)


def test_not_sparse():
    with pytest.raises(ValueError):
        sparse.declared_size(b"\0" * 28)
