"""Stopping a background helper stops what it started too (issue 1, Windows).

mtkclient runs under uv, as uv's child. Killing uv alone left mtkclient running: on Windows it kept the
backup file open, and the next five runs stopped with WinError 32 on full_user.bin (on Linux, checked with
uv 0.11.7: the child is still running 3 seconds after uv is killed). It also kept claiming the clock while
the next try's mtkclient waited for it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lck import backup
from lck.runner import RealRunner, stop

# A parent that starts a child and writes its pid, as uv starts mtkclient.
PARENT = ("import subprocess, sys, time\n"
          "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
          "open(sys.argv[1], 'w').write(str(c.pid))\n"
          "time.sleep(120)\n")


def _alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:    # a zombie waiting for its new parent to collect it is gone, for this purpose
        return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
    except OSError:
        return True


def test_stopping_a_helper_stops_its_child_too(tmp_path):
    pid_file = tmp_path / "child.pid"
    proc = RealRunner().spawn([sys.executable, "-c", PARENT, str(pid_file)], out_file=tmp_path / "out.txt")
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        time.sleep(0.1)
    child = int(pid_file.read_text())
    assert _alive(child)
    stop(proc)
    for _ in range(50):
        if not _alive(child):
            break
        time.sleep(0.1)
    alive = _alive(child)
    if alive:
        os.kill(child, 9)
    assert not alive, "the child outlived its stopped parent"
    assert proc.poll() is not None


def test_a_fake_helper_is_only_asked_to_stop():
    # Tests' stand-ins aren't real processes: their pid may be someone else's.
    class Fake:
        pid = os.getpid()
        killed = False

        def kill(self):
            self.killed = True
    f = Fake()
    stop(f)
    assert f.killed


def test_nothing_stops_a_helper_with_kill_alone():
    root = Path(__file__).resolve().parent.parent / "lck"
    for f in root.glob("*.py"):
        if f.name == "runner.py":      # stop() itself
            continue
        for m in re.finditer(r"\bproc\.kill\(\)", f.read_text(encoding="utf-8")):
            line = f.read_text(encoding="utf-8")[:m.start()].count("\n") + 1
            pytest.fail(f"{f.name}:{line} kills only the helper, not what it started: use runner.stop(proc)")


def test_a_backup_file_still_held_says_what_holds_it(tmp_path, monkeypatch):
    out = tmp_path / "backup" / "clock"
    out.mkdir(parents=True)
    (out / backup.RAW).write_bytes(b"part of a dump")

    def held(self, missing_ok=False):
        raise PermissionError(32, "The process cannot access the file because it is being used by another "
                                  "process", str(self))
    monkeypatch.setattr(Path, "unlink", held)
    from tests.fakes import FakeUI
    from tests.test_clock_tool import ctx_for
    with pytest.raises(backup.BackupMismatch, match="mtkclient from an earlier run"):
        backup.backup(ctx_for(tmp_path, ui=FakeUI()), out)
