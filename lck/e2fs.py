"""e2fsck and debugfs on each OS: the clock's user data is ext4, edited on the computer before the flash.

The ready-made programs carry their own, in an `e2fsprogs` folder next to the program, so nothing has to
be installed (built from the pinned upstream source by scripts/build-e2fsprogs.sh on Linux and macOS, and
Cygwin's build on Windows; licences and source alongside). That copy comes first. Run from source, it's:
- Linux: from the e2fsprogs package.
- macOS: Homebrew's e2fsprogs (`brew install e2fsprogs`), which Homebrew keeps out of PATH.
- Windows: Cygwin's e2fsprogs 1.44.5 and the DLLs it needs, downloaded at run time from Cygwin's own
  release mirror, pinned by checksum in manifest.json, and run from the tool's cache with no Cygwin
  install.
"""
from __future__ import annotations

import os
import shutil
import sys
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


def bundled() -> Path | None:
    """The e2fsprogs folder shipped next to the ready-made program, if this is one and it has it."""
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent / "e2fsprogs"
    exe = ".exe" if platform_id() == "windows" else ""
    return folder if (folder / f"debugfs{exe}").exists() and (folder / f"e2fsck{exe}").exists() else None


def _use(folder: Path) -> None:
    exe = ".exe" if platform_id() == "windows" else ""
    env = None
    if platform_id() == "windows":
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(folder), env.get("PATH", "")])
        env["CYGWIN"] = "nodosfilewarning"
    userdata.DEBUGFS, userdata.E2FSCK = str(folder / f"debugfs{exe}"), str(folder / f"e2fsck{exe}")
    userdata.ENV = env


def gather_windows(ctx, folder: Path) -> Path:
    """Cygwin's debugfs.exe, e2fsck.exe and the DLLs they load, in one folder."""
    from lck.downloads import fetch
    folder = Path(folder)
    roots = [Path(fetch(i, ctx)) for i in CYGWIN]
    folder.mkdir(parents=True, exist_ok=True)
    for root in roots:
        for f in list((root / "usr" / "bin").glob("*.dll")) + [root / "usr" / "sbin" / "debugfs.exe",
                                                                root / "usr" / "sbin" / "e2fsck.exe"]:
            if f.exists():
                shutil.copy2(f, folder / f.name)
    if not all((folder / n).exists() for n in ("debugfs.exe", "e2fsck.exe")):
        raise E2fsMissing("The Cygwin e2fsprogs download has no debugfs.exe or e2fsck.exe. Please report this.")
    return folder


def _windows(ctx) -> tuple[Path, Path]:
    folder = ctx.paths.cache / "e2fsprogs-windows"
    if not all((folder / n).exists() for n in ("debugfs.exe", "e2fsck.exe")):
        gather_windows(ctx, folder)
    return folder / "debugfs.exe", folder / "e2fsck.exe"


def bundle_windows(dest: str) -> None:
    """For the release build: Cygwin's e2fsprogs into the Windows zip, with where it came from."""
    import tempfile
    from lck.steps import Context, Paths
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Context(runner=None, ui=None, paths=Paths(Path(tmp)), state={})
        folder = gather_windows(ctx, Path(dest))
    from lck.downloads import load_manifest
    m = load_manifest()
    (folder / "SOURCE.txt").write_text(
        "debugfs.exe and e2fsck.exe from Cygwin's e2fsprogs 1.44.5 (GPL-2.0), with the Cygwin DLLs they load\n"
        "(cygwin1.dll: LGPL-3.0; the others: their packages' licences). Unchanged, from Cygwin's release mirror;\n"
        "the source packages sit next to each in the same folders:\n" +
        "".join(f"  {m[i]['url']}  sha256 {m[i]['sha256']}\n" for i in CYGWIN), encoding="utf-8")


def locate(ctx) -> None:
    """Find (or, on Windows, fetch) debugfs and e2fsck, and point lck/userdata.py at them."""
    plat = platform_id()
    own = bundled()
    if own is not None:
        _use(own)
        return
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
