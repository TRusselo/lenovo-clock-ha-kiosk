"""bkerler/mtkclient, downloaded at run time (GPL-3.0, never bundled) at
kortregt's known-good commit, and run through uv (lck/uvrun.py) with only the
packages its command line needs.

The clock's BROM shows up for only 3 to 5 seconds before it hands over to the
preloader, so mtkclient has to be waiting already: the tool starts it first,
then asks for the buttons. One mtkclient session per BROM power-cycle: a
second one finds a stale BROM ("Wrong magic"). Several commands share one
session through mtkclient's `multi`.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from lck.downloads import fetch
from lck.modes import BROM_PROMPT, HINT_AFTER, NOTHING_HINT, PRELOADER_HINT, Mode, UsbNoPermission, current
from lck.uvrun import uv_path, uv_python

PACKAGES = ["pyusb", "pycryptodome", "pycryptodomex", "colorama", "pyserial", "keystone-engine",
            "capstone", "unicorn"]
TRIES = 5
REPROMPT_AFTER = 180
# Commands that write to the clock. Anything else mtkclient can do (writing the whole
# disk, the preloader, erasing, unlocking seccfg) is refused here.
READS = {"rf", "ro", "r", "printgpt"}
NEVER_WRITE = {"preloader", "proinfo", "nvram", "seccfg", "lk_a", "lk_b", "tee_a", "tee_b",
               "oem_bootloader_a", "oem_bootloader_b", "kb", "dkb"}
_PROGRESS = re.compile(r"(\d+(?:\.\d+)?)% (?:Read|Write)")


class MtkFailed(Exception):
    pass


class ForbiddenCommand(Exception):
    pass


def assert_safe(command: str) -> None:
    words = command.split()
    if not words:
        raise ForbiddenCommand("refusing an empty mtkclient command")
    if words[0] in READS:
        return
    if words[0] == "w" and len(words) == 3 and words[1] not in NEVER_WRITE:
        return
    raise ForbiddenCommand(f"refusing: mtk {command}")


def _python(ctx) -> list:
    return [*uv_python(uv_path(ctx), *[a for p in PACKAGES for a in ("--with", p)])]


def script(ctx) -> Path:
    return next(Path(fetch("mtkclient", ctx)).glob("mtkclient-*")) / "mtk.py"


def prepare(ctx) -> None:
    """Fetch mtkclient's packages now, so nothing downloads while the clock waits in BROM."""
    with ctx.ui.busy("Setting up mtkclient"):
        ctx.runner.run([*_python(ctx), "-c", "import usb, Cryptodome, Crypto, unicorn, capstone"],
                       timeout=1800)


def _ok(text: str, commands: list[str]) -> bool:
    if "Failed to" in text or "Error: Couldn't" in text or "Traceback" in text:
        return False
    for c in commands:
        word = c.split()[0]
        if word in ("rf", "ro", "r") and "Dumped" not in text:
            return False
        if word == "w" and f"Wrote {c.split()[2]}" not in text:
            return False
    return True


def run_in_brom(ctx, commands: list[str], *, cwd: Path, title: str, prompt: str = BROM_PROMPT,
                sleep=time.sleep, timeout: float = 4 * 3600) -> str:
    """Start mtkclient with `commands` (one session), ask for BROM, and show progress
    until it's done. A failed attempt (the preloader, a stale BROM) is retried with a
    fresh power-cycle; nothing is written in a failed attempt before the DA takes over."""
    for c in commands:
        assert_safe(c)
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    args = [*_python(ctx), script(ctx)]
    args += ["multi", ";".join(commands)] if len(commands) > 1 else commands[0].split()
    for attempt in range(1, TRIES + 1):
        log = cwd / "mtk-output.txt"
        proc = ctx.runner.spawn(args, out_file=log, cwd=cwd)
        try:
            ctx.ui.physical(prompt)
            text = _follow(ctx, proc, log, title, sleep, timeout, commands, prompt)
        except MtkFailed:
            text = ""
        except BaseException:
            proc.kill()
            ctx.runner.finished(proc)
            raise
        if proc.returncode == 0 and _ok(text, commands):
            return text
        if "error: " in text and "usage:" in text:
            # mtkclient refused the command line itself: retrying can't help.
            raise MtkFailed("mtkclient didn't accept its command. Please report this, with the log.")
        if attempt == TRIES:
            raise MtkFailed(f"mtkclient didn't finish after {TRIES} tries. The last lines of its "
                            "output are in the log.")
        ctx.ui.redraw()
        ctx.ui.say("That didn't take. Let's try again, from the start:")
    raise AssertionError("unreachable")


def _follow(ctx, proc, log: Path, title: str, sleep, timeout: float, commands, prompt: str) -> str:
    start = time.monotonic()
    last = None
    hinted = nothing_hinted = False
    reprompts = 1
    while proc.poll() is None:
        try:
            mode = current(ctx)
        except UsbNoPermission:
            proc.kill()
            ctx.runner.finished(proc)
            raise
        except Exception:
            mode = None
        if mode is not None and mode.value != last:
            ctx.ui.status(mode.value)
            last = mode.value
        if mode == Mode.PRELOADER and not hinted:
            ctx.ui.say(PRELOADER_HINT)
            hinted = True
            proc.kill()   # mtkclient's preloader path fails on this clock: start again
            break
        if mode == Mode.DA:
            break
        waited = time.monotonic() - start
        if mode == Mode.NONE and not nothing_hinted and waited > HINT_AFTER:
            ctx.ui.say(NOTHING_HINT)
            nothing_hinted = True
        if waited > REPROMPT_AFTER * reprompts:
            ctx.ui.redraw()
            ctx.ui.physical(prompt)
            ctx.ui.say("Still waiting. Do the steps above whenever you're ready, or press Ctrl-C to stop.")
            reprompts += 1
        if waited > timeout:
            proc.kill()
            break
        sleep(0.25)
    if proc.poll() is None and last == Mode.DA.value:
        ctx.ui.say("Found it. Let go of the buttons. Leave the clock plugged in until this finishes.")
        with ctx.ui.busy(title) as job:
            while proc.poll() is None:
                pct = _last_percent(log)
                if pct is not None:
                    job.note(f"{pct:.0f}%", pct, 100)
                if time.monotonic() - start > timeout:
                    proc.kill()
                sleep(1)
            text = ctx.runner.finished(proc)
            if proc.returncode != 0 or not _ok(text, commands):
                raise MtkFailed("mtkclient stopped with an error")
            return text
    proc.wait()
    return ctx.runner.finished(proc)


def _last_percent(log: Path):
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    found = _PROGRESS.findall(tail)
    return float(found[-1]) if found else None
