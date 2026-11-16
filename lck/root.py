"""Step 14: root with Magisk, on boot_a only.

The clock has no debug build, so the Smart Display's `adb root` route doesn't
exist here, and disabling verity would likely stop it starting. Magisk
patches only the boot image: system and verified boot stay untouched.

The stock boot image is patched on the clock itself, with Magisk's own
boot_patch.sh, as the Magisk app does. LEGACYSAR matters: LK always passes
skip_initramfs, and Magisk's kernel patch turns it into want_initramfs so its
ramdisk loads. Undo: flash boot-stock.img (kept in the work folder) to boot_a.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from lck import backup, clock, images
from lck.clock import SetupFailed, shell
from lck.downloads import fetch
from lck.flash import fbmulti
from lck.modes import POWER_CYCLE
from lck.steps import Step
from lck.ui import choose

MAGISK_PKG = "com.topjohnwu.magisk"
DEVICE_DIR = "/data/local/tmp/lck-magisk"
FILES = {
    "assets/boot_patch.sh": "boot_patch.sh",
    "assets/util_functions.sh": "util_functions.sh",
    "lib/armeabi-v7a/libbusybox.so": "busybox",
    "lib/armeabi-v7a/libinit-ld.so": "init-ld",
    "lib/armeabi-v7a/libmagisk.so": "magisk",
    "lib/armeabi-v7a/libmagiskboot.so": "magiskboot",
    "lib/armeabi-v7a/libmagiskinit.so": "magiskinit",
}
ENV = "KEEPVERITY=true KEEPFORCEENCRYPT=true LEGACYSAR=true"


class RootFailed(Exception):
    pass


def patch_files(ctx) -> Path:
    apk = fetch("magisk", ctx)
    out = images.work(ctx) / "magisk"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        for src, name in FILES.items():
            (out / name).write_bytes(z.read(src))
    (out / "stub.apk").write_bytes(b"")
    return out


def stock_boot(ctx) -> Path:
    """The stock boot image of the build on slot A, from the backup."""
    f = images.work(ctx) / "images" / "boot-stock.img"
    if f.exists():
        return f
    running = clock.prop(ctx, "ro.build.id")
    parts = backup.extract(ctx, images.backup_dir(ctx), ["boot_a", "boot_b", "system_a", "system_b"],
                           images.work(ctx) / "parts-boot")
    for s in "ab":
        if images.build_id(ctx, parts[f"system_{s}"]) == running:
            f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(parts[f"boot_{s}"], f)
            return f
    raise RootFailed(f"The backup has no stock boot image for the clock's build ({running}).")


def patch(ctx, boot: Path) -> Path:
    """Patch `boot` on the clock, with Magisk's script, and bring the result back."""
    files = patch_files(ctx)
    out = images.work(ctx) / "images" / "boot-magisk.img"
    shell(ctx, "rm", "-rf", DEVICE_DIR, check=False)
    shell(ctx, "mkdir", "-p", DEVICE_DIR)
    with ctx.ui.busy("Patching the boot image with Magisk, on the clock"):
        for f in sorted(files.iterdir()):
            clock.adb(ctx, "push", f, f"{DEVICE_DIR}/{f.name}", timeout=120)
        clock.adb(ctx, "push", boot, f"{DEVICE_DIR}/boot.img", timeout=300)
        r = clock.adb(ctx, "shell", f"cd {DEVICE_DIR} && chmod 755 * && {ENV} sh ./boot_patch.sh boot.img",
                      check=False, timeout=600)
        if r.code != 0 or "new-boot.img" not in shell(ctx, "ls", DEVICE_DIR, check=False):
            raise RootFailed("Magisk couldn't patch the boot image. Nothing was flashed; the log has why.")
        clock.adb(ctx, "pull", f"{DEVICE_DIR}/new-boot.img", out, timeout=300)
    shell(ctx, "rm", "-rf", DEVICE_DIR, check=False)
    if out.stat().st_size == 0 or out.read_bytes()[:8] != b"ANDROID!":
        raise RootFailed("The patched boot image came back damaged. Nothing was flashed.")
    return out


class RootStep(Step):
    name = "root"
    title = "Root the clock with Magisk"

    def done(self, ctx) -> bool:
        return clock.has_root(ctx)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        boot = stock_boot(ctx)
        patched = patch(ctx, boot)
        ctx.ui.say("Now the patched boot image goes on slot A. Only boot_a is written.")
        fbmulti(ctx, [(patched, "boot_a")], title="Flashing the patched boot image")
        ctx.ui.physical(POWER_CYCLE)
        clock.wait_booted(ctx, why="the clock to start with Magisk")
        if "package:" not in shell(ctx, "pm", "path", MAGISK_PKG, check=False):
            with ctx.ui.busy("Installing the Magisk app"):
                clock.adb(ctx, "install", "-r", fetch("magisk", ctx), timeout=300)
        if not clock.has_root(ctx):
            self._first_run(ctx)
        if not clock.has_root(ctx):
            raise RootFailed("The clock still refuses root to adb. Open Magisk's Superuser tab on the clock "
                             "and switch Shell on, then run the same command again.")
        ctx.ui.keep(f"Rooted. To undo, flash {boot} to boot_a (see the manual's Restore section).")

    def _first_run(self, ctx) -> None:
        shell(ctx, "monkey", "-p", MAGISK_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        ctx.ui.physical("Magisk is open on the clock. If it says Requires Additional Setup, tap OK: the "
                        "clock restarts on its own. Close any popup offering to patch: it's done already.")
        if choose(ctx.ui, "Press Enter once the clock has restarted, or type stop: ", "stop"):
            raise RootFailed("Stopped before Magisk was set up. Run the same command again to carry on.")
        clock.wait_booted(ctx, why="the clock to restart")
        shell(ctx, "su", "-c", "id", check=False, timeout=30)   # asks Magisk, which logs the request
        shell(ctx, "monkey", "-p", MAGISK_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        ctx.ui.physical("In Magisk, open the Superuser tab (the shield at the bottom) and switch Shell on. "
                        "Stay out of the Modules tab: its file picker has no way back on this screen.")
        while not clock.has_root(ctx):
            if choose(ctx.ui, "Press Enter once Shell is switched on, or type stop: ", "stop"):
                raise RootFailed("Stopped before root was granted. Run the same command again to carry on.")
            shell(ctx, "su", "-c", "id", check=False, timeout=30)
