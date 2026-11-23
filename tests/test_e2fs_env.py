"""debugfs's command library (libss) dlopens the host's libreadline at start, even for -R. The Linux release's
debugfs is static, and a static glibc program segfaults doing that: on a PC with libreadline installed every
debugfs call died (exit 139) and setup stopped at "Build slot A" (e2e run 9). SS_READLINE_PATH=none makes
libss skip the load, so every way of finding e2fsprogs sets it."""
from __future__ import annotations

import pytest

from lck import e2fs, userdata


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    for name in ("DEBUGFS", "E2FSCK", "ENV"):
        monkeypatch.setattr(userdata, name, getattr(userdata, name))


def test_the_bundled_e2fsprogs_never_loads_readline(tmp_path, monkeypatch):
    monkeypatch.setattr(e2fs, "platform_id", lambda: "linux")
    monkeypatch.setenv("LCK_TEST_KEEP", "1")
    e2fs._use(tmp_path)
    assert userdata.ENV["SS_READLINE_PATH"] == "none"
    assert userdata.ENV["LCK_TEST_KEEP"] == "1"          # the rest of the environment is kept


def test_windows_keeps_its_path_and_cygwin_settings_too(tmp_path, monkeypatch):
    monkeypatch.setattr(e2fs, "platform_id", lambda: "windows")
    e2fs._use(tmp_path)
    assert userdata.ENV["SS_READLINE_PATH"] == "none"
    assert userdata.ENV["PATH"].startswith(str(tmp_path))
    assert userdata.ENV["CYGWIN"] == "nodosfilewarning"


def test_a_systems_own_e2fsprogs_gets_it_too(tmp_path, monkeypatch):
    monkeypatch.setattr(e2fs, "platform_id", lambda: "linux")
    monkeypatch.setattr(e2fs, "bundled", lambda: None)
    monkeypatch.setattr(e2fs.shutil, "which", lambda name, path=None: f"/usr/sbin/{name}")
    e2fs.locate(None)
    assert userdata.DEBUGFS == "/usr/sbin/debugfs"
    assert userdata.ENV["SS_READLINE_PATH"] == "none"
