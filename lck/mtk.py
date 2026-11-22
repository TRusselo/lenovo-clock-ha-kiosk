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

from lck import windrivers
from lck.runner import stop
from lck.downloads import fetch
from lck.modes import (helper_env, BROM_PROMPT, HINT_AFTER, NOTHING_HINT, PRELOADER_HINT, STARTED_AFTER, Mode,
                       UsbNoPermission, current)
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

# mtkclient (fetched, not ours to edit) swallows the reason it can't open the clock's USB device: its
# handshake loop logs it at debug level only (issue 1: Windows, no UsbDk, nothing said). This runs it
# unchanged, and prints, each once, with "[lck]" in front: why opening USB failed, whether libusb uses
# UsbDk (Windows), and when the handshake with BROM worked.
MARK = "[lck] "
CONNECTED = MARK + "connected to the clock in BROM mode"
OPEN_FAILED = MARK + "USB open failed: "
MTK_SHIM = """import os, runpy, sys
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
def say(text):
    print("[lck] " + text, flush=True)
say("mtkclient on " + sys.platform + ", Python " + sys.version.split()[0])
try:
    from mtkclient.Library.Connection import usblib
    from mtkclient.Library import Port as port
except Exception as e:
    say("can't watch mtkclient's USB connection: %r" % (e,))
else:
    seen = set()
    _connect = usblib.UsbClass.connect
    def connect(self, *a, **k):
        try:
            return _connect(self, *a, **k)
        except Exception as e:
            msg = type(e).__name__ + ": " + str(e)
            if msg not in seen:
                seen.add(msg)
                say("USB open failed: " + msg)
            raise
    usblib.UsbClass.connect = connect
    _init = usblib.UsbClass.__init__
    def init(self, *a, **k):
        _init(self, *a, **k)
        if sys.platform.startswith("win") and "backend" not in seen:
            seen.add("backend")
            if self.backend is None:
                say("libusb: not loaded")
            else:
                try:
                    code = self.backend.lib.libusb_set_option(self.backend.ctx, 1)
                except Exception as e:
                    code = repr(e)
                say("libusb UsbDk backend: %s (libusb_set_option returned %s)" % ("on" if code == 0 else "OFF", code))
    usblib.UsbClass.__init__ = init
    _handshake = port.Port.handshake
    def handshake(self, *a, **k):
        ok = _handshake(self, *a, **k)
        if ok:
            say("connected to the clock in BROM mode")
        return ok
    port.Port.handshake = handshake
runpy.run_path(script, run_name="__main__")
"""
NO_CONNECT_AFTER = 10      # seconds after BROM showed with no handshake: something stops mtkclient
NOT_SUPPORTED = ("NotImplementedError", "Operation not supported", "LIBUSB_ERROR_NOT_SUPPORTED")


def connected(text: str) -> bool:
    """mtkclient's handshake with BROM worked (the shim's line, or mtkclient's own next lines)."""
    return CONNECTED in text or "Detected regular mode" in text or "Detected iot mode" in text


def open_failures(text: str) -> list[str]:
    return [l[len(OPEN_FAILED):].strip() for l in text.splitlines() if l.startswith(OPEN_FAILED)]


def no_driver(text: str) -> bool:
    """libusb can list the device but not open it: on Windows, no UsbDk (or WinUSB) for it."""
    return any(s in f for f in open_failures(text) for s in NOT_SUPPORTED)


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
    """Fetch mtkclient and its packages now, so nothing downloads while the clock waits in BROM."""
    script(ctx)
    with ctx.ui.busy("Setting up mtkclient's packages"):
        ctx.runner.run([*_python(ctx), "-c", "import usb, Cryptodome, Crypto, unicorn, capstone"],
                       timeout=1800)


NEXT_SESSION = ("Now unplug the clock's round power plug: it goes into BROM mode once more, this time to write. Wait "
                "3 seconds, then do the steps below.")
STALE = ("The clock is still held by the last try. Unplug its round power plug, wait 3 seconds, then do the "
         "steps below.")


def why_failed(text: str) -> str:
    """The reason an attempt failed, in words (e2e run 6: retries gave none)."""
    if "flash size 0x0" in text or "Couldn't detect partition" in text:
        return "the clock was still held by the last try, so nothing could be read. Nothing was written"
    if "USBError" in text or "Errno 19" in text or "Errno 5" in text:
        if "Write:" in text or "Wrote " in text:
            # e2e run 8: a restore lost USB partway through its writes. The next try writes it all again.
            return "the USB connection dropped, with the backup partly written. The next try writes it all again"
        return "the USB connection dropped. Nothing was written"
    if not text.strip():
        return "the clock didn't reach BROM mode"
    return "mtkclient stopped early. Nothing was written"


def _ok(text: str, commands: list[str]) -> bool:
    if ("Failed to" in text or "Error: Couldn't" in text or "Traceback" in text
            or "flash size 0x0" in text):    # a leftover download agent reads 0 bytes (e2e run 6)
        return False
    for c in commands:
        word = c.split()[0]
        if word in ("rf", "ro", "r") and "Dumped" not in text:
            return False
        if word == "w" and f"Wrote {c.split()[2]}" not in text:
            return False
    return True


def release_stale(ctx, sleep=time.sleep, message: str = STALE) -> bool:
    """A clock still in BROM or held by mtkclient's download agent from a stopped or failed session
    (0e8d:2001) makes the next session read nothing. Only unplugging it frees it (e2e run 6).
    True when it asked for the unplug."""
    try:
        mode = current(ctx)
    except Exception:
        return False
    if mode in (Mode.DA, Mode.BROM, Mode.PRELOADER):
        ctx.ui.physical(message)
        with ctx.ui.busy("Waiting for the clock's power to be unplugged") as job:
            while current(ctx) != Mode.NONE:
                sleep(0.5)
            job.done("The clock is unplugged")
        return True
    return False


def run_in_brom(ctx, commands: list[str], *, cwd: Path, title: str, prompt: str = BROM_PROMPT,
                sleep=time.sleep, timeout: float = 4 * 3600, weights: list[int] | None = None,
                held_message: str = STALE) -> str:
    """Start mtkclient with `commands` (one session), ask for BROM, and show progress
    until it's done. A failed attempt (the preloader, a stale BROM) is retried with a
    fresh power-cycle; nothing is written in a failed attempt before the DA takes over."""
    for c in commands:
        assert_safe(c)
    windrivers.check_brom_driver(ctx, ctx.state.get("platform"))   # Windows: UsbDk, before the clock is asked for
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    args = [*_python(ctx), "-c", MTK_SHIM, script(ctx)]
    args += ["multi", ";".join(commands)] if len(commands) > 1 else commands[0].split()
    logger = ctx.state.get("log")
    for attempt in range(1, TRIES + 1):
        # The first try may follow a session that ended normally (restore reads, then writes): its
        # own words then (e2e run 7: "still held by the last try" read as a failure).
        message = held_message if attempt == 1 else STALE
        unplugged = release_stale(ctx, sleep, message)
        # Its own words already said what this session is for: the plain steps then, not "BROM mode again,
        # this time to write" a second time (review of the run 7 fixes).
        steps = BROM_PROMPT if unplugged and message is not STALE else prompt
        log = cwd / "mtk-output.txt"
        proc = ctx.runner.spawn(args, out_file=log, cwd=cwd, env=helper_env(ctx.paths.cache))
        try:
            ctx.ui.physical(steps)
            ctx.ui.status("waiting for BROM")
            text = _follow(ctx, proc, log, title, sleep, timeout, commands, steps, weights)
        except MtkFailed:
            # The output so far tells why (e2e run 7: a USB pull was reported as "didn't reach BROM").
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        except BaseException:
            stop(proc)
            ctx.runner.finished(proc)
            raise
        if proc.returncode == 0 and _ok(text, commands):
            return text
        if "error: " in text and "usage:" in text:
            # mtkclient refused the command line itself: retrying can't help.
            raise MtkFailed("mtkclient didn't accept its command. Please report this, with the log.")
        reason = why_failed(text)
        if logger:
            logger.info("BROM attempt %d of %d failed: %s", attempt, TRIES, reason)
        if attempt == TRIES:
            raise MtkFailed(f"mtkclient didn't finish after {TRIES} tries (the last time: {reason}). The last "
                            "lines of its output are in the log.")
        ctx.ui.redraw()       # a fresh screen: no old "Clock mode" lines (e2e run 6)
        ctx.ui.keep(f"That didn't take: {reason}. Try {attempt + 1} of {TRIES}, from the start:")
    raise AssertionError("unreachable")


def _guarded(ctx, sleep, seconds, state) -> None:
    """Sleep, but a single Ctrl-C (often meant as copy) only asks: a second one within 5 seconds stops."""
    try:
        sleep(seconds)
    except KeyboardInterrupt:
        now = time.monotonic()
        if now - state.get("ctrl_c", -99.0) < 5:
            raise
        state["ctrl_c"] = now
        ctx.ui.say("Press Ctrl-C again within 5 seconds to stop. (To copy text from the terminal, use "
                   "Ctrl+Shift+C.)")


def _follow(ctx, proc, log: Path, title: str, sleep, timeout: float, commands, prompt: str,
            weights=None) -> str:
    start = time.monotonic()
    last = None
    hinted = nothing_hinted = False
    brom_at = None        # when BROM was last seen
    brom_first = None     # when BROM was first seen
    took_over = False     # mtkclient has the clock: its download agent on USB, or its handshake in its output
    windows = windrivers.is_windows(ctx.state.get("platform"))
    reprompts = 1
    guard: dict = {}
    while proc.poll() is None:
        text = _read(log)
        if windows and no_driver(text):
            # libusb lists the clock but can't open it: retrying can't help (issue 1).
            windrivers.log_usb_devices(ctx, ctx.state.get("platform"))
            raise windrivers.UsbDkMissing(windrivers.USBDK_NOT_WORKING)
        if connected(text):
            # On Windows the USB watcher may not see the download agent while UsbDk holds the clock, so
            # mtkclient's own word counts too: never the unplug hint once it's connected.
            took_over = True
            if last != Mode.DA.value:
                ctx.ui.status(Mode.DA.value)
                last = Mode.DA.value
            break
        try:
            mode = current(ctx)
        except UsbNoPermission:
            stop(proc)
            ctx.runner.finished(proc)
            raise
        except Exception:
            mode = None
        if mode is not None and mode.value != last:
            ctx.ui.status(mode.value)
            if mode == Mode.BROM:
                # mtkclient has the clock once BROM shows: the buttons can go now (e2e run 4).
                ctx.ui.say("Found BROM. Let go of the buttons. Leave the clock plugged in while it connects.")
            last = mode.value
        if mode == Mode.PRELOADER and not hinted:
            ctx.ui.say(PRELOADER_HINT)
            hinted = True
            stop(proc)   # mtkclient's preloader path fails on this clock: start again
            break
        if mode == Mode.DA:
            took_over = True
            break
        waited = time.monotonic() - start
        if mode == Mode.BROM:
            brom_at = time.monotonic()
            brom_first = brom_first or brom_at
        if (windows and brom_first is not None and not nothing_hinted
                and time.monotonic() - brom_first >= NO_CONNECT_AFTER):
            # BROM showed, but no handshake: on Windows that's the driver, not the buttons (issue 1).
            ctx.ui.say(windrivers.NO_CONNECT)
            windrivers.log_usb_devices(ctx, ctx.state.get("platform"))
            nothing_hinted = True
        # A short gap after BROM is the handover to the download agent, not a miss (e2e run 7); a long one
        # means the buttons were let go and the clock started normally, which is what the hint is for.
        if brom_at is None:
            nothing_long = waited > HINT_AFTER
        else:
            nothing_long = time.monotonic() - brom_at >= STARTED_AFTER
        if mode == Mode.NONE and not nothing_hinted and nothing_long:
            ctx.ui.say(NOTHING_HINT)
            nothing_hinted = True
        if waited > REPROMPT_AFTER * reprompts:
            ctx.ui.redraw()
            ctx.ui.physical(prompt)
            ctx.ui.say("Still waiting. Do the steps above whenever you're ready, or press Ctrl-C to stop.")
            reprompts += 1
        if waited > timeout:
            stop(proc)
            break
        _guarded(ctx, sleep, 0.25, guard)
    if proc.poll() is None and took_over:
        ctx.ui.say("Connected. Leave the clock plugged in until this finishes.")
        with ctx.ui.busy(title) as job:
            while proc.poll() is None:
                pct = overall(log, weights or [1] * len(commands))
                if pct is not None:
                    job.note(f"{pct:.0f}%", pct, 100)
                if time.monotonic() - start > timeout:
                    stop(proc)
                _guarded(ctx, sleep, 1, guard)
            text = ctx.runner.finished(proc)
            if proc.returncode != 0 or not _ok(text, commands):
                raise MtkFailed("mtkclient stopped with an error")
            return text
    proc.wait()
    return ctx.runner.finished(proc)


def _read(log: Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def overall(log: Path, weights: list[int]):
    """One bar for the whole session (e2e run 2: the bar filled once per partition): the
    commands finished so far, by size, plus the running one's own percent."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # mtkclient prints "Wrote parts/boot_a.bin to sector …" once a partition is written.
    flat = text.replace("\r", "\n")
    ends = list(re.finditer(r"(?:^|\s)(?:Wrote|Dumped) \S", flat))
    finished = min(len(ends), len(weights))
    # Only a percent printed after the last finished partition belongs to the running one: the
    # finished one's own "100.0%" just before its Wrote line flashed the bar to ~100% (e2e run 4).
    current_part = flat[ends[-1].end():] if ends else flat
    found = _PROGRESS.findall(current_part)
    pct = (float(found[-1]) if found else 0.0) if finished < len(weights) else 100.0
    total = sum(weights) or 1
    return 100 * (sum(weights[:finished]) + (weights[finished] * (pct or 0) / 100
                                             if finished < len(weights) else 0)) / total


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
