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

import contextlib
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

from lck import backup, clock, images
from lck.clock import SetupFailed, shell
from lck.downloads import fetch
from lck.flash import fbmulti, restart_from_fastboot
from lck.steps import Step
from lck.ui import choose

MAGISK_PKG = "com.topjohnwu.magisk"
CAST_PKG = "com.google.android.apps.mediashell"
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


def made_from(boot: Path, magisk: Path) -> str:
    """What a patched image was made from: the stock boot image and the Magisk file used, by checksum
    (review of the run 7 fixes: a typed-in version could miss a Magisk update)."""
    return f"{backup.sha256(boot)} {backup.sha256(magisk)}"


def patch(ctx, boot: Path) -> Path:
    """Patch `boot` on the clock, with Magisk's script, and bring the result back."""
    out = images.work(ctx) / "images" / "boot-magisk.img"
    stamp = out.with_name(out.name + ".from")
    source = made_from(boot, fetch("magisk", ctx))
    if out.exists() and stamp.exists() and stamp.read_text() == source:
        # e2e run 7: a rerun patched again each time (about 1.5 min); the result doesn't change.
        ctx.ui.say("Using the boot image Magisk patched on the last run.")
        return out
    files = patch_files(ctx)
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
    stamp.write_text(source, encoding="utf-8", newline="\n")
    return out


def magisk_booted(ctx) -> bool:
    """The patched boot image is running: Magisk's su exists, even before adb is granted root
    (it answers "Permission denied", not "not found"). A resumed root step then needs no new
    patch and no fastboot trip (e2e run 3)."""
    out = shell(ctx, "su", "-c", "id", check=False, timeout=30)
    return "not found" not in out and "No such file" not in out and out.strip() != ""


def front_app(ctx, timeout: float = 120) -> str:
    m = re.search(r"mResumedActivity: \S+ \S+ (\S+)",
                  shell(ctx, "dumpsys", "activity", "activities", check=False, timeout=timeout))
    return m.group(1) if m else ""


def settle(ctx, sleep=time.sleep, quiet: float = 10, limit: float = 90) -> None:
    """After a restart, wait until the screen has stopped changing (Assistant's launcher comes up
    as Android finishes starting, and covered Magisk in e2e runs 3 and 4)."""
    end = time.monotonic() + limit
    last, since = None, time.monotonic()
    with ctx.ui.busy("Waiting for the clock's screen to settle"):
        while time.monotonic() < end:
            app = front_app(ctx)
            if app != last:
                last, since = app, time.monotonic()
            elif app and time.monotonic() - since >= quiet:
                return
            sleep(2)


def open_magisk(ctx, tries: int = 3, sleep=time.sleep) -> None:
    """Open the Magisk app on the clock's screen, and check it's in front: right after a restart
    the first launch can be lost or covered (e2e runs 3 and 4)."""
    for _ in range(tries):
        # On a clock not linked to Google, Cast's setup screen keeps opening over Magisk (e2e run 6).
        shell(ctx, "am", "force-stop", CAST_PKG, check=False)
        shell(ctx, "monkey", "-p", MAGISK_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        sleep(3)
        if front_app(ctx).startswith(MAGISK_PKG):
            return


@contextlib.contextmanager
def keep_magisk_in_front(ctx, every: float = 4.0):
    """While the person works in Magisk: if Cast's setup screen ("Link Account") takes the screen, which
    it does on a clock not linked to Google (e2e runs 6 and 7), stop Cast and bring Magisk back."""
    stop = threading.Event()

    def watch():
        # Short timeouts, and a look at `stop` before acting: a slow adb call (a Wi-Fi blip) mustn't keep
        # the thread past the prompt, or stop Cast and open Magisk after it (review of the run 7 fixes).
        while not stop.wait(every):
            try:
                if front_app(ctx, timeout=10).startswith(CAST_PKG) and not stop.is_set():
                    shell(ctx, "am", "force-stop", CAST_PKG, check=False, timeout=10)
                    if not stop.is_set():
                        shell(ctx, "monkey", "-p", MAGISK_PKG, "-c", "android.intent.category.LAUNCHER", "1",
                              check=False, timeout=10)
            except Exception:
                pass
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=every + 25)     # its adb calls are at most 10 s each


class RootStep(Step):
    name = "root"
    title = "Root the clock with Magisk"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        return clock.has_root(ctx)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        boot = stock_boot(ctx)
        if magisk_booted(ctx):
            ctx.ui.say("Magisk's patched boot image is already running: no new patch or flash needed.")
        else:
            patched = patch(ctx, boot)
            ctx.ui.say("Now the patched boot image goes on slot A. Only boot_a is written.")
            fbmulti(ctx, [(patched, "boot_a")], title="Flashing the patched boot image")
            restart_from_fastboot(ctx)
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
        old = clock.boot_id(ctx)
        open_magisk(ctx)
        ctx.ui.physical("Magisk is open on the clock. If it says Requires "
                        "Additional Setup, **tap OK**. Touch nothing else: the clock restarts by itself, and the tool "
                        "waits for it. If it doesn't ask (its setup was done on an earlier try), there's nothing to "
                        "tap. Close any popup offering to patch: that's done already.")
        # Cast's "Link Account" can cover Requires Additional Setup too (review of the run 7 fixes).
        with keep_magisk_in_front(ctx):
            restarted = clock.wait_restarted(ctx, old, why="the clock to restart, after you tap OK", give_up=180)
        if not restarted:
            if choose(ctx.ui, "No restart yet. If Magisk didn't ask for extra setup, press Enter to carry on; if "
                              "you tapped OK, wait for it to restart first. Or type stop: ", "stop"):
                raise RootFailed("Stopped before Magisk was set up. Run the same command again to carry on.")
        clock.wait_booted(ctx, why="the clock to start")
        settle(ctx)
        shell(ctx, "su", "-c", "id", check=False, timeout=30)   # asks Magisk, which logs the request
        open_magisk(ctx)
        ctx.ui.physical("Now let adb use root. In Magisk, open the Superuser tab "
                        "(the shield at the bottom):\n"
                        "  1. Tap + (add)\n"
                        "  2. Tick Shell in the list\n"
                        "  3. Tap the back arrow, top left. Shell is now listed, with its switch on\n"
                        "Stay out of the Modules tab: its file picker has no way back on this screen.")
        while not clock.has_root(ctx):
            with keep_magisk_in_front(ctx):
                stopped = choose(ctx.ui, "Press Enter once Shell is switched on, or type stop: ", "stop")
            if stopped:
                raise RootFailed("Stopped before root was granted. Run the same command again to carry on.")
            shell(ctx, "su", "-c", "id", check=False, timeout=30)
            if not clock.has_root(ctx) and not front_app(ctx).startswith(MAGISK_PKG):
                ctx.ui.say("Magisk isn't on the clock's screen any more: opening it again.")
                open_magisk(ctx)
