"""e2fsck and debugfs on each OS: the clock's user data is ext4, edited on the computer before the flash.

- Linux: from the e2fsprogs package.
- macOS: Homebrew's e2fsprogs (`brew install e2fsprogs`), which Homebrew keeps out of PATH.
- Windows: Cygwin's e2fsprogs 1.44.5 and the DLLs it needs, downloaded at run time from Cygwin's own
  release mirror, pinned by checksum in manifest.json, and run from the tool's cache with no Cygwin
  install. Cygwin is LGPL; nothing of it is bundled.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from lck import doc, userdata
from lck.runner import platform_id

# Cygwin packages, by manifest id: e2fsprogs and every DLL debugfs.exe and e2fsck.exe load.
CYGWIN = ["cygwin-e2fsprogs", "cygwin-runtime", "cygwin-libuuid1", "cygwin-libintl8", "cygwin-libiconv2",
          "cygwin-libgcc1", "cygwin-libss2", "cygwin-libcom-err2", "cygwin-libext2fs2", "cygwin-libe2p2",
          "cygwin-libblkid1"]
BREW = ["/opt/homebrew/opt/e2fsprogs/sbin", "/usr/local/opt/e2fsprogs/sbin"]


class E2fsMissing(Exception):
    pass


def _windows(ctx) -> tuple[Path, Path]:
    from lck.downloads import fetch
    folder = ctx.paths.cache / "e2fsprogs-windows"
    exe = folder / "debugfs.exe", folder / "e2fsck.exe"
    if all(f.exists() for f in exe):
        return exe
    roots = [Path(fetch(i, ctx)) for i in CYGWIN]
    folder.mkdir(parents=True, exist_ok=True)
    for root in roots:
        for f in list((root / "usr" / "bin").glob("*.dll")) + [root / "usr" / "sbin" / "debugfs.exe",
                                                                root / "usr" / "sbin" / "e2fsck.exe"]:
            if f.exists():
                shutil.copy2(f, folder / f.name)
    if not all(f.exists() for f in exe):
        raise E2fsMissing("The Cygwin e2fsprogs download has no debugfs.exe or e2fsck.exe. Please report this.")
    return exe


def locate(ctx) -> None:
    """Find (or, on Windows, fetch) debugfs and e2fsck, and point lck/userdata.py at them."""
    plat = platform_id()
    if plat == "windows":
        debugfs, e2fsck = _windows(ctx)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(debugfs.parent), env.get("PATH", "")])
        env["CYGWIN"] = "nodosfilewarning"
        userdata.DEBUGFS, userdata.E2FSCK, userdata.ENV = str(debugfs), str(e2fsck), env
        return
    search = os.pathsep.join([os.environ.get("PATH", ""), *(BREW if plat == "darwin" else []),
                              "/sbin", "/usr/sbin"])
    debugfs, e2fsck = shutil.which("debugfs", path=search), shutil.which("e2fsck", path=search)
    if not (debugfs and e2fsck):
        how = ("Install it with Homebrew: brew install e2fsprogs. See " + doc("docs/MACOS.md") if plat == "darwin"
               else "Install the e2fsprogs package. See " + doc("docs/LINUX.md", "step 2") + ", or " + doc("docs/PYTHON.md"))
        raise E2fsMissing("debugfs and e2fsck (e2fsprogs) aren't installed. " + how + ".")
    userdata.DEBUGFS, userdata.E2FSCK, userdata.ENV = debugfs, e2fsck, None
