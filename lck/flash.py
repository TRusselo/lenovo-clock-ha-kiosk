"""Steps 3 and 7: unlock, and flash slot A, in LK's fastboot.

LK's fastboot hangs on the getvar queries Google's fastboot client sends
before a flash, so flashing goes through fbmulti.py (downloaded, pinned),
which sends download and flash straight over USB. It waits for the clock,
so it starts first and the clock goes into fastboot after.

Only the slot-A images, userdata and misc are ever flashed (assert_safe).
misc goes last: if anything before it fails, the clock still starts slot B.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import zipfile
from pathlib import Path

from lck import clock
from lck.downloads import fetch
from lck.images import work
from lck.modes import FASTBOOT_PROMPT, POWER_CYCLE, Mode, current, stuck_hint, wait_for_mode, wait_to_leave
from lck.runner import Result
from lck.steps import Step
from lck.ui import choose
from lck.uvrun import uv_path, uv_python

FLASH_SET = {"boot_a", "vendor_a", "vbmeta_a", "system_a", "oem_a", "userdata", "misc"}
WIPE_WARNING = ("This clock was already unlocked before this run: it may be converted already, or put back "
                "to stock with lck restore, which leaves it unlocked. Flashing replaces slot A and its user "
                "data with the images built from the backup: anything set up on it since goes.")


class FlashFailed(Exception):
    pass


class ForbiddenCommand(Exception):
    pass


def assert_safe(pairs: list[tuple[Path, str]]) -> None:
    for _f, part in pairs:
        if part not in FLASH_SET:
            raise ForbiddenCommand(f"refusing to flash {part}")


def _pieces(folder: Path, name: str) -> list[Path]:
    return sorted(folder.glob(f"{name}_chunk.*"), key=lambda p: int(p.suffix[1:]))


def flash_list(folder: Path) -> list[tuple[Path, str]]:
    folder = Path(folder)
    pairs = [(folder / "boot.img", "boot_a"), (folder / "vendor.img", "vendor_a"),
             (folder / "vbmeta.img", "vbmeta_a")]
    pairs += [(p, "system_a") for p in _pieces(folder, "system")]
    pairs += [(p, "oem_a") for p in _pieces(folder, "oem")]
    pairs += [(p, "userdata") for p in _pieces(folder, "userdata")]
    pairs.append((folder / "misc.img", "misc"))
    missing = [str(f.name) for f, _ in pairs if not f.exists()]
    if missing or not any(p == "system_a" for _, p in pairs) or not any(p == "userdata" for _, p in pairs):
        raise FlashFailed("The images to flash aren't all there (" + (", ".join(missing) or "pieces missing")
                          + "). Run the setup again from the images step: lck setup --from images")
    return pairs


def fbmulti(ctx, pairs: list[tuple[Path, str]], *, title: str, sleep=time.sleep) -> None:
    """Start fbmulti.py, ask for fastboot, and show progress until it's done."""
    assert_safe(pairs)
    args = [*uv_python(uv_path(ctx), "--with", "pyusb"), fetch("fbmulti.py", ctx)]
    args += [f"{f}={p}" for f, p in pairs]
    log = work(ctx) / "fbmulti-output.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = ctx.runner.spawn(args, out_file=log)
    try:
        prompted = current(ctx) != Mode.FASTBOOT
        if prompted:
            ctx.ui.physical(FASTBOOT_PROMPT)
        else:
            ctx.ui.say("The clock is still in fastboot mode from before: nothing to hold.")
        last = None
        while proc.poll() is None and current(ctx) != Mode.FASTBOOT:
            mode = current(ctx)
            if mode.value != last:
                ctx.ui.status(mode.value)
                last = mode.value
            sleep(0.25)
        if proc.poll() is None and prompted:
            ctx.ui.say("Found it. Let go of VOLUME UP. Leave the clock plugged in until this finishes.")
        with ctx.ui.busy(title) as job:
            while proc.poll() is None:
                done = _flashed(log)
                job.note(f"{done} of {len(pairs)}", done, len(pairs))
                sleep(1)
    except BaseException:
        proc.kill()
        ctx.runner.finished(proc)
        raise
    text = ctx.runner.finished(proc)
    if proc.returncode != 0 or "ALL DONE" not in text:
        last = [l for l in text.splitlines() if l.strip()][-1:] or ["no output"]
        raise FlashFailed(f"Flashing stopped ({last[0].strip()}). The clock still starts its old slot. "
                          "Unplug its power and run the same command again.")


def _flashed(log: Path) -> int:
    try:
        return log.read_text(errors="replace").count("<- OKAY") // 2
    except OSError:
        return 0


class FlashStep(Step):
    name = "flash"
    title = "Flash slot A, the user data and misc"

    def done(self, ctx) -> bool:
        return clock.converted(ctx)

    def run(self, ctx) -> None:
        pairs = flash_list(work(ctx) / "images")
        if not ctx.state.get("unlocked_now"):
            ctx.ui.say("WARNING: " + WIPE_WARNING)
            if not choose(ctx.ui, "Type flash to flash it anyway, or press Enter to stop: ", "flash"):
                hint = stuck_hint(ctx)
                raise FlashFailed("Stopped before flashing. Nothing was changed." + (" " + hint if hint else ""))
        fbmulti(ctx, pairs, title="Flashing slot A, about 2 minutes")
        restart_from_fastboot(ctx)
        ctx.ui.keep("The clock is starting slot A. It reconnects to the Wi-Fi saved in the backup, with adb over "
                    "Wi-Fi switched on.")


def flash_userdata(ctx) -> None:
    """Write only the user data again (new Wi-Fi details), then start the clock."""
    folder = work(ctx) / "images"
    pairs = [(p, "userdata") for p in _pieces(folder, "userdata")]
    if not pairs:
        raise FlashFailed("The user data images aren't there. Run the setup again from the userdata step.")
    ctx.ui.say("Now only the user data goes on the clock again.")
    fbmulti(ctx, pairs, title="Flashing the user data")
    restart_from_fastboot(ctx)


def restart_from_fastboot(ctx) -> None:
    """The power-cycle box stays up until the clock has really left fastboot: the next
    step talks to it over Wi-Fi, which only works once Android runs."""
    ctx.ui.physical(POWER_CYCLE)
    wait_to_leave(ctx, Mode.FASTBOOT)


def _value(out: str, key: str) -> str:
    m = re.search(rf"^(?:\(bootloader\)\s*)?{re.escape(key)}[:=]\s*(\S*)", out, re.M)
    return m.group(1) if m else ""


def _fastboot(ctx, *args, timeout=60, check=False):
    """LK's fastboot can stop answering Google's client: a hang counts as no answer."""
    try:
        return ctx.runner.run([ctx.state["tools"].fastboot, *args], check=check, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(1, "")


def unlocked(ctx) -> tuple[bool, bool]:
    """(bootloader unlocked, AVB unlocked), from fastboot. No answer stops the run:
    guessing "locked" would unlock a clock that may already be converted."""
    bl = _value(_fastboot(ctx, "getvar", "unlocked", timeout=30).out, "unlocked")
    avb = _value(_fastboot(ctx, "getvar", "at-vboot-state", timeout=30).out, "avb-locked")
    if bl not in ("yes", "no") or avb not in ("0", "1"):
        raise FlashFailed("The clock's fastboot didn't say whether it's unlocked. Nothing was changed. Unplug "
                          "its power and run the same command again.")
    return bl == "yes", avb == "0"


class UnlockStep(Step):
    name = "unlock"
    title = "Unlock the bootloader and verified boot"

    def done(self, ctx) -> bool:
        return clock.converted(ctx)

    def run(self, ctx) -> None:
        from lck.images import ensure_wifi
        ensure_wifi(ctx)              # before the unlock, the first write
        ctx.ui.say("The bootloader's lock can only be read in fastboot mode. If the clock is unlocked already, "
                   "this only reads it.")
        wait_for_mode(Mode.FASTBOOT, ctx, prompt=FASTBOOT_PROMPT)
        bl, avb = unlocked(ctx)
        if bl and avb:
            ctx.ui.say("The bootloader and verified boot are already unlocked.")
            return
        ctx.state["unlocked_now"] = True
        if not bl:
            with ctx.ui.busy("Unlocking the bootloader"):
                _fastboot(ctx, "flashing", "unlock", timeout=120)
        if not avb:
            creds = self._credentials(ctx)
            env = dict(os.environ)
            env["PATH"] = os.pathsep.join([str(ctx.state["tools"].fastboot.parent), env.get("PATH", "")])
            with ctx.ui.busy("Unlocking verified boot"):
                ctx.runner.run([*uv_python(uv_path(ctx), "--with", "pycryptodome"),
                                fetch("at_auth_unlock.py", ctx), "-v", creds], timeout=300, env=env)
        bl, avb = unlocked(ctx)
        if not (bl and avb):
            raise FlashFailed("The unlock ran, but the clock still reports itself locked "
                              f"(bootloader {'unlocked' if bl else 'locked'}, verified boot "
                              f"{'unlocked' if avb else 'locked'}). Nothing else was changed.")
        ctx.ui.say("Unlocked. This wiped the clock's user data: the next steps rebuild it from the backup.")

    @staticmethod
    def _credentials(ctx) -> Path:
        folder = fetch("cube-avb-unlock", ctx)
        found = sorted(Path(folder).rglob("cube_unlock_credentials_v2.zip"))
        if not found:
            raise FlashFailed("The unlock download has no cube_unlock_credentials_v2.zip.")
        with zipfile.ZipFile(found[0]):
            pass
        return found[0]
