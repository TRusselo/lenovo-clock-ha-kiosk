"""Copying a file off the clock over Wi-Fi tries again (issue 1, Mac: the patched boot image stalled at 92%
for 2.5 minutes, adb gave up, and setup stopped; the rerun copied it at once)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lck import clock
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

T = "10.0.0.7:5555"
DATA = b"ANDROID!" + bytes(4096)
REMOTE = "/data/local/tmp/lck-magisk/new-boot.img"


class PullRunner(FakeRunner):
    """adb pull writes what `copies` says, one per try: whole, cut short, or nothing (failed)."""

    def __init__(self, copies, **kw):
        super().__init__({("-s", T, "shell", "md5sum", REMOTE): Result(0, f"{hashlib.md5(DATA).hexdigest()}  {REMOTE}\n"),
                          ("-s", T, "get-state"): Result(0, "device\n")}, **kw)
        self.copies = list(copies)

    def run(self, args, **kw):
        args = [str(a) for a in args]
        if args[1:4] == ["-s", T, "pull"]:
            self.calls.append(args)
            data = self.copies.pop(0)
            if data is None:
                return Result(1, f"[ 92%] {REMOTE}\nadb: error: failed to read copy response")
            Path(args[5]).write_bytes(data)
            return Result(0, "1 file pulled")
        return super().run(args, **kw)


def _pulls(runner):
    return [c for c in runner.calls if c[1:4] == ["-s", T, "pull"]]


def test_a_copy_that_broke_off_is_tried_again(tmp_path):
    runner = PullRunner([None, DATA])
    ui = FakeUI()
    out = tmp_path / "boot-magisk.img"
    clock.pull(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"), REMOTE, out, sleep=lambda s: None)
    assert out.read_bytes() == DATA and len(_pulls(runner)) == 2
    assert any("again" in l for l in ui.lines)


def test_a_copy_cut_short_doesnt_count_even_when_adb_says_it_worked(tmp_path):
    runner = PullRunner([DATA[:1000], DATA])
    out = tmp_path / "boot-magisk.img"
    clock.pull(ctx_for(tmp_path, runner=runner, ip="10.0.0.7"), REMOTE, out, sleep=lambda s: None)
    assert out.read_bytes() == DATA and len(_pulls(runner)) == 2


def test_three_failed_copies_stop_with_plain_words_and_leave_nothing(tmp_path):
    runner = PullRunner([None, None, None])
    out = tmp_path / "boot-magisk.img"
    with pytest.raises(clock.SetupFailed, match="Wi-Fi"):
        clock.pull(ctx_for(tmp_path, runner=runner, ip="10.0.0.7"), REMOTE, out, sleep=lambda s: None)
    assert len(_pulls(runner)) == 3 and not out.exists()


def test_the_patch_step_copies_with_pull():
    # The boot image comes back through clock.pull, not a single adb pull.
    text = (Path(__file__).resolve().parent.parent / "lck" / "root.py").read_text(encoding="utf-8")
    assert '"pull"' not in text and "clock.pull(" in text
