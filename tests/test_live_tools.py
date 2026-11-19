"""Live checks of the tools the conversion leans on, on the system the tests run on: they download and run
real programs, so they're skipped unless LCK_LIVE=1 (the CI 'live' job sets it on Windows, macOS and Linux).

They prove what the unit tests can't: that this OS's debugfs and e2fsck (Cygwin's on Windows, Homebrew's
on macOS) edit an ext4 user data image the way the setup needs, and that mtkclient starts through uv."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lck import e2fs, mtk, userdata
from lck.runner import RealRunner, Tools
from lck.steps import Context, Paths
from tests.fakes import FakeUI

pytestmark = pytest.mark.skipif(os.environ.get("LCK_LIVE") != "1", reason="live tool checks: set LCK_LIVE=1")


def _ctx(tmp_path):
    paths = Paths(tmp_path / "work")
    c = Context(runner=RealRunner(), ui=FakeUI(), paths=paths, state={"progress_id": "clock"})
    c.state["tools"] = Tools.locate(paths.cache)
    return c


def _mke2fs() -> str:
    folder = Path(userdata.DEBUGFS).parent if os.sep in userdata.DEBUGFS or "/" in userdata.DEBUGFS else None
    for name in ("mke2fs.exe", "mke2fs"):
        if folder and (folder / name).exists():
            return str(folder / name)
    found = shutil.which("mke2fs", path=os.pathsep.join([os.environ.get("PATH", ""), *e2fs.BREW, "/sbin",
                                                             "/usr/sbin"]))
    assert found, "mke2fs not found next to debugfs"
    return found


def test_this_systems_e2fsprogs_edits_a_user_data_image(tmp_path):
    c = _ctx(tmp_path)
    e2fs.locate(c)
    if os.name == "nt":                       # Cygwin's mke2fs sits in the same package
        shutil.copy2(next((c.paths.cache / "cygwin-e2fsprogs").rglob("mke2fs.exe")),
                     Path(userdata.DEBUGFS).parent / "mke2fs.exe")
    tree = tmp_path / "img" / "tree"
    (tree / "system" / "users" / "0").mkdir(parents=True)
    (tree / "system" / "users" / "0" / "settings_global.xml").write_text(
        '<settings version="-1">\n<setting id="1" name="adb_enabled" value="0" package="android" />\n</settings>\n')
    for d in ("property", "misc/adb", "misc/wifi"):
        (tree / d).mkdir(parents=True)
    img = tmp_path / "img" / "userdata test.img"          # a space in the name, as in real folders
    with open(img, "wb") as f:
        f.truncate(16 << 20)
    r = subprocess.run([_mke2fs(), "-F", "-t", "ext4", "-d", "tree", img.name], cwd=img.parent,
                       capture_output=True, text=True, env=userdata.ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    pub = tmp_path / "img" / "adbkey.pub"
    pub.write_text("QAAAAfake-key-for-the-test user@host\n")
    userdata.inject(str(img), str(pub), runner=c.runner)
    userdata.add_wifi(str(img), "Home Net", "a-long-password")
    assert userdata.wifi_networks(str(img)) == 1
    assert b'name="adb_enabled" value="1"' in userdata.cat(str(img), "/system/users/0/settings_global.xml")
    assert userdata.cat(str(img), "/property/persist.adb.tcp.port") == b"5555"
    label = subprocess.run([userdata.DEBUGFS, "-R", f"ea_get {userdata.WIFI_STORE} security.selinux", img.name],
                           cwd=img.parent, capture_output=True, text=True, env=userdata.ENV).stdout
    assert "wifi_data_file" in label
    r = subprocess.run([userdata.E2FSCK, "-fn", img.name], cwd=img.parent, capture_output=True, text=True,
                       env=userdata.ENV)
    assert r.returncode == 0, r.stdout + r.stderr


def test_mtkclient_starts_through_uv(tmp_path):
    c = _ctx(tmp_path)
    from lck.downloads import fetch, load_manifest, platform_entry
    from lck.runner import platform_arch, platform_id
    fetch(platform_entry("uv", load_manifest(), platform_arch()), c)
    fetch(f"platform-tools-{platform_id()}", c)
    mtk.prepare(c)
    r = c.runner.run([*mtk._python(c), mtk.script(c), "--help"], check=False, timeout=600)
    assert "usage" in r.out.lower(), r.out[-2000:]
