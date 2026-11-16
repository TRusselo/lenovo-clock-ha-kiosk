"""Steps 4 to 6: build what gets flashed to slot A, from the clock's own backup.

There's no leaked debug build for the clock, so its own stock software goes
back on slot A: the slot whose build matches the backup's user data. The
user data is edited on this computer first (lck/userdata.py), so the clock
comes up with adb over Wi-Fi and this computer's key, and misc boots slot A
with the update channel pointed nowhere (lck/misc.py).

LK's fastboot takes at most 96 MiB per download, so the big images go as
sparse pieces (img2simg, then sparsesplit.py), each checked against its
partition's size (lck/sparse.py).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from lck import backup, clock, misc, sparse, userdata
from lck.downloads import fetch
from lck.steps import Step

SLOT_IMAGES = ["boot", "vendor", "vbmeta", "system", "oem"]
NEEDED = [f"{n}_{s}" for n in SLOT_IMAGES for s in "ab"] + ["misc", "userdata"]
PIECE = 94371840          # 90 MiB: under LK's 96 MiB max-download-size


class ImagesFailed(Exception):
    pass


def work(ctx) -> Path:
    return ctx.paths.root / "work" / ctx.state.get("progress_id", "clock")


def backup_dir(ctx) -> Path:
    return ctx.paths.backup / ctx.state.get("progress_id", "clock")


def _parts(ctx) -> dict[str, Path]:
    folder = backup_dir(ctx)
    if backup.verify_backup(folder):
        raise ImagesFailed(f"There's no complete backup in {folder}, and the setup rebuilds the clock from "
                           "it. Run the backup step first: lck setup --from backup")
    return backup.extract(ctx, folder, NEEDED, work(ctx) / "parts")


def _debugfs_cat(ctx, image: Path, path: str) -> str:
    r = ctx.runner.run(["debugfs", "-R", f"cat {path}", image], check=False, timeout=120, log_output=False)
    return r.out


def build_id(ctx, system_img: Path) -> str:
    """ro.build.id of a system image, or "" when it isn't Android (Alpine on slot B)."""
    text = _debugfs_cat(ctx, system_img, "/system/build.prop")
    m = re.search(r"^ro\.build\.id=(\S+)", text, re.M)
    return m.group(1) if m else ""


def userdata_build(ctx, userdata_img: Path) -> str:
    """The build the user data was last used with, from packages.xml's fingerprint."""
    text = _debugfs_cat(ctx, userdata_img, "/system/packages.xml")
    m = re.search(r'fingerprint="[^"/]*/[^"/]*/[^"/:]*:[^/]*/([^/"]+)/', text)
    return m.group(1) if m else ""


def is_empty(path: Path, probe: int = 1 << 20) -> bool:
    with open(path, "rb") as f:
        return f.read(probe).count(0) == probe


def _sparse(ctx, raw: Path, name: str, out: Path, limit: int) -> list[Path]:
    simg = out / f"{name}.simg"
    ctx.runner.run(["img2simg", raw, simg], timeout=1800)
    for old in out.glob(f"{name}_chunk.*"):
        old.unlink()
    ctx.runner.run([*_python(ctx), fetch("sparsesplit.py", ctx), simg, out / f"{name}_chunk", PIECE],
                   timeout=1800)
    simg.unlink()
    pieces = sorted(out.glob(f"{name}_chunk.*"), key=lambda p: int(p.suffix[1:]))
    for p in pieces:
        with open(p, "rb") as f:
            head = f.read(sparse.HEADER.size)
        if not sparse.fits(head, limit):
            raise ImagesFailed(f"{p.name} would write past the end of its partition. Nothing was flashed.")
    return pieces


def _python(ctx) -> list:
    from lck.uvrun import uv_path, uv_python
    return uv_python(uv_path(ctx))


class ImagesStep(Step):
    name = "images"
    title = "Build slot A from the clock's own stock software"

    def done(self, ctx) -> bool:
        return clock.converted(ctx) or (work(ctx) / "images" / ".done").exists()

    def run(self, ctx) -> None:
        parts = _parts(ctx)
        out = work(ctx) / "images"
        out.mkdir(parents=True, exist_ok=True)
        if is_empty(parts["userdata"]):
            raise ImagesFailed("This backup's user data is empty: it was taken after the bootloader was "
                               "unlocked, which wipes it. The setup needs a backup from before unlocking.")
        with ctx.ui.busy("Finding the stock build that matches the user data"):
            ud = work(ctx) / "userdata_check.img"
            shutil.copyfile(parts["userdata"], ud)
            ctx.runner.run(["e2fsck", "-fy", ud], check=False, timeout=1800)
            wanted = userdata_build(ctx, ud)
            ud.unlink()
            builds = {s: build_id(ctx, parts[f"system_{s}"]) for s in "ab"}
        slot = next((s for s in "ab" if builds[s] and builds[s] == wanted), None)
        if slot is None:
            raise ImagesFailed(f"Neither slot's system ({builds['a'] or 'not Android'}, "
                               f"{builds['b'] or 'not Android'}) matches the build the user data was last "
                               f"used with ({wanted or 'unknown'}). Please report this, with the log.")
        ctx.ui.say(f"Slot {slot.upper()} has the matching stock build, {builds[slot]}. It goes on slot A.")
        lengths = {p.name: p.size for p in backup.gpt.read_gpt(backup.head_of(backup_dir(ctx)))}
        for n in ("boot", "vendor", "vbmeta"):
            shutil.copyfile(parts[f"{n}_{slot}"], out / f"{n}.img")
        shutil.copyfile(parts[f"boot_{slot}"], out / "boot-stock.img")
        with ctx.ui.busy("Making sparse pieces of system and oem"):
            _sparse(ctx, parts[f"system_{slot}"], "system", out, lengths["system_a"])
            _sparse(ctx, parts[f"oem_{slot}"], "oem", out, lengths["oem_a"])
        (out / "slot.txt").write_text(f"{slot}\n{builds[slot]}\n")
        (out / ".done").write_text("")


class UserdataStep(Step):
    name = "userdata"
    title = "Switch on adb over Wi-Fi in the clock's user data"

    def done(self, ctx) -> bool:
        return clock.converted(ctx) or (work(ctx) / "images" / "userdata_chunk.0").exists()

    def run(self, ctx) -> None:
        parts = _parts(ctx)
        out = work(ctx) / "images"
        length = parts["userdata"].stat().st_size
        img = work(ctx) / "userdata_things.img"
        key = Path.home() / ".android" / "adbkey.pub"
        if not key.exists():
            ctx.runner.run([ctx.state["tools"].adb, "start-server"], check=False, timeout=60)
        if not key.exists():
            raise ImagesFailed(f"No adb key at {key}. Run: adb keygen ~/.android/adbkey, then the same "
                               "command again.")
        with ctx.ui.busy("Editing a copy of the user data"):
            ctx.runner.run(["cp", "--sparse=always", parts["userdata"], img], timeout=1800)
            r = ctx.runner.run(["e2fsck", "-fy", img], check=False, timeout=1800)
            if r.code not in (0, 1):
                raise ImagesFailed("The backup's user data has file system errors e2fsck can't fix.")
            userdata.inject(str(img), str(key), runner=ctx.runner)
            with open(img, "r+b") as f:
                f.truncate(sparse.whole_blocks(length))
            r = ctx.runner.run(["e2fsck", "-fn", img], check=False, timeout=1800)
            if r.code != 0:
                raise ImagesFailed("The edited user data didn't pass its check. Nothing was flashed.")
        with ctx.ui.busy("Making sparse pieces of the user data"):
            _sparse(ctx, img, "userdata", out, length)
        img.unlink()
        ctx.ui.say("The user data holds your Wi-Fi password from the backup: the pieces stay in "
                   f"{out}, keep that folder private.")


class MiscStep(Step):
    name = "misc"
    title = "Boot slot A next, and block Google's updates"

    def done(self, ctx) -> bool:
        return clock.converted(ctx) or (work(ctx) / "images" / "misc.img").exists()

    def run(self, ctx) -> None:
        parts = _parts(ctx)
        data = misc.trial_slot_a(parts["misc"].read_bytes())
        a, _b = misc.decode_ab(data)
        if (a.priority, a.tries, misc.read_channel(data)) != (15, 3, "none-channel"):
            raise ImagesFailed("The new misc image didn't come out right. Nothing was flashed.")
        (work(ctx) / "images" / "misc.img").write_bytes(data)
