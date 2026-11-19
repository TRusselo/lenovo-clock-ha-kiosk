"""Which mode is the clock in? The screen stays dark in BROM, the preloader and
fastboot, so the tool shows this instead, from the clock's USB IDs.

Unlike the Smart Display, the clock never shows up as an adb device on USB:
under Android its USB-A port is host-only. Once Android runs, the tool talks
to it over Wi-Fi only, at its own address (see lck/clock.py). Other adb
devices on this computer are never touched.
"""
from __future__ import annotations

import os
import sys
import time
from enum import Enum

from lck import doc


class Mode(Enum):
    NONE = "none"
    BROM = "BROM"
    PRELOADER = "preloader"
    DA = "BROM, working"       # mtkclient's download agent, after it has taken over BROM
    FASTBOOT = "fastboot"


USB_IDS = {
    (0x0E8D, 0x0003): Mode.BROM,
    (0x0E8D, 0x2000): Mode.PRELOADER,
    (0x0E8D, 0x2001): Mode.DA,
    (0x0BB4, 0x0C01): Mode.FASTBOOT,
}
POLL_SECONDS = 0.25
HINT_AFTER = 30
NOTHING_HINT = ("Nothing yet. If the screen lit up with the Lenovo logo or the clock face, the clock is "
                "starting normally: the buttons weren't held as it powered on. Unplug its power and try "
                "again, holding the buttons before you plug it in. Check the USB cable is in the clock's "
                "USB port and this computer, and that the power is the round barrel plug.")
PRELOADER_HINT = ("That's the preloader, not BROM: only one button was held, or they were let go too "
                  "soon. Unplug the power and try again with VOLUME UP and VOLUME DOWN held together. "
                  "It often takes two or three tries.")


class MultipleDevices(Exception):
    pass


class ModeTimeout(Exception):
    pass


class UsbBackendMissing(Exception):
    """pyusb has no libusb backend."""


class UsbNoPermission(Exception):
    pass


LIBUSB_MISSING = ("This needs libusb, which couldn't be loaded. Install libusb-1.0 with your package "
                  "manager (for example: sudo apt install libusb-1.0-0), then run the same command again. "
                  "See " + doc("docs/LINUX.md") + ".")
NO_PERMISSION = ("The clock is connected, but this computer account isn't allowed to use it. Do the "
                 "one-time USB access setup in " + doc("docs/LINUX.md", "step 3") + ", then unplug the "
                 "clock's power and run the same command again.")


def usb_access(bus: int, address: int) -> bool:
    """Can this account open the USB device? Only Linux has per-device permissions."""
    if not sys.platform.startswith("linux"):
        return True
    return os.access(f"/dev/bus/usb/{bus:03d}/{address:03d}", os.R_OK | os.W_OK)


def usb_scan() -> list[tuple[Mode, int, int]]:
    """Every clock-mode USB device: (mode, bus, address)."""
    try:
        import usb.core
        devs = list(usb.core.find(find_all=True))
    except Exception as e:  # usb.core.NoBackendError and import errors
        if type(e).__name__ == "NoBackendError" or isinstance(e, ImportError):
            raise UsbBackendMissing(LIBUSB_MISSING) from e
        raise
    return [(USB_IDS[(d.idVendor, d.idProduct)], d.bus, d.address) for d in devs
            if (d.idVendor, d.idProduct) in USB_IDS]


def detect(scan=usb_scan, access=usb_access, sleep=time.sleep) -> list[Mode]:
    """The clock's modes on USB. A device file that can't be opened is only a permission
    problem if the device is still there a moment later: when the clock leaves a mode (it
    started normally, or was unplugged as asked) between the listing and the check, its file
    is already gone (e2e run 3: two false stops)."""
    found = scan()
    refused = [(m, b, a) for m, b, a in found if m in (Mode.BROM, Mode.FASTBOOT) and not access(b, a)]
    if refused:
        sleep(1)
        again = scan()
        if any(d in again and not access(d[1], d[2]) for d in refused):
            raise UsbNoPermission(NO_PERMISSION)
        found = again
    return [m for m, _b, _a in found]


def current(ctx) -> Mode:
    """The one clock mode on USB right now (NONE if nothing)."""
    found = (ctx.state.get("detect") or detect)()
    if len(found) > 1:
        raise MultipleDevices(f"More than one clock is connected ({', '.join(m.value for m in found)}). "
                              "Unplug every other clock or MediaTek device and try again.")
    return found[0] if found else Mode.NONE


def wait_for_mode(target: Mode, ctx, *, prompt: str, timeout: float = 180,
                  sleep=time.sleep, clock=time.monotonic) -> None:
    """Show the prompt and wait until the clock is in `target` mode. Never gives up:
    after `timeout` the prompt is shown again, and the user can press Ctrl-C."""
    ui = ctx.ui
    mode = current(ctx)
    if mode == target:
        ui.say(f"The clock is already in {target.value} mode.")
        return
    if mode == Mode.DA and target == Mode.FASTBOOT:
        # Still held by the download agent from a backup: unplug first, rather than the box and "wrong mode"
        # at once (e2e run 7: the fastboot box twice). Plain BROM is left alone: it's on the way to fastboot,
        # and someone already holding VOLUME UP mustn't be told to unplug (review of the run 7 fixes).
        ui.physical(f"The clock is still in {mode.value} mode from the last job. Unplug its round power plug "
                    "first.")
        with ui.busy("Waiting for the clock's power to be unplugged") as job:
            while current(ctx) != Mode.NONE:
                sleep(0.5)
            job.done("The clock is unplugged")
    ui.physical(prompt)
    start, last, hinted, wrong = clock(), None, False, None
    brom_seen = None          # when BROM was last seen, in a wait for fastboot
    while True:
        mode = current(ctx)
        if target == Mode.FASTBOOT and mode == Mode.BROM:
            brom_seen = clock()
        if (target == Mode.FASTBOOT and brom_seen is not None and mode == Mode.NONE
                and clock() - brom_seen >= STARTED_AFTER):
            # BROM, then nothing for a long while: there's always a short gap with nothing on USB
            # between BROM and fastboot, but this long means the buttons were let go on the way and
            # the clock started normally. Once Android runs, its USB shows nothing at all.
            ui.redraw()
            ui.say(STARTED_NORMALLY)
            ui.physical(prompt)
            brom_seen = None
        if mode.value != last:
            ui.status(mode.value)
            last = mode.value
        if mode == target:
            # keep(): the step ends at once and the next screen would wipe it (e2e run 4).
            ui.keep(f"Found it, in {target.value} mode. Let go of the buttons if you're still holding them.")
            return
        if mode == Mode.PRELOADER and target == Mode.BROM and wrong != mode:
            ui.say(PRELOADER_HINT)
            wrong = mode
        elif (mode not in (Mode.NONE, target, Mode.PRELOADER) and wrong != mode
              and not (target == Mode.FASTBOOT and mode == Mode.BROM)):
            # Holding VOLUME UP passes through BROM on the way to fastboot: that's not a mistake.
            ui.redraw()
            ui.say(f"The clock is in {mode.value} mode, not {target.value}. Unplug its power and try again:")
            ui.physical(prompt)
            wrong = mode
        elapsed = clock() - start
        if mode == Mode.NONE and not hinted and elapsed >= HINT_AFTER:
            ui.say(NOTHING_HINT)
            hinted = True
        if elapsed > timeout:
            ui.redraw()
            ui.physical(prompt)
            ui.say("Still waiting. Do the steps above whenever you're ready, or press Ctrl-C to stop.")
            start, hinted, wrong, last = clock(), False, None, None
        sleep(POLL_SECONDS)


# VOLUME UP alone reaches BROM: every catch in e2e run 6 used it (BROM is on the way to fastboot,
# and mtkclient, already waiting, keeps the clock there).
BROM_PROMPT = ("For **BROM mode**: unplug the clock's round power plug. Hold VOLUME UP, plug the power back in, and "
               "keep holding for **about 5 seconds**, or until Clock mode says BROM, then let go. The screen stays "
               "dark. It can take two or three tries.")
BROM_AGAIN = ("BROM mode again, this time to write. " + BROM_PROMPT)
FASTBOOT_PROMPT = ("For **fastboot mode**: unplug the clock's round power plug. Hold VOLUME UP, plug the power back "
                   "in, and keep holding for **about 10 seconds**, until the tool says it found the clock. On "
                   "the way it passes through BROM mode at 3 to 5 seconds: \"Clock mode: BROM\" while you "
                   "hold is normal, so keep holding. The screen stays dark.")
POWER_CYCLE = ("To start the clock normally: unplug the clock's round power plug, wait 3 seconds, and plug it "
               "back in. Hold no buttons. The first start takes a few minutes.")
STARTED_AFTER = 15         # seconds with nothing on USB after BROM, in a fastboot wait
STARTED_NORMALLY = ("The clock went past BROM and started normally: the buttons were let go too soon, and once "
                    "Android runs, the clock's USB shows nothing. Try again, holding VOLUME UP the whole time:")
IN_A_MODE = ("The clock is still in {mode} mode, with its screen dark. To start the clock normally: Unplug "
             "its round power plug and plug it back in, holding no buttons.")


def wait_to_leave(ctx, mode: Mode, *, sleep=time.sleep) -> None:
    """Wait, with the box on screen, until the clock has left `mode` (it was power-cycled)."""
    with ctx.ui.busy(f"Waiting for the clock to leave {mode.value} mode") as job:
        while current(ctx) == mode:
            sleep(0.5)
        job.done(f"The clock left {mode.value} mode")


def stuck_hint(ctx=None) -> str:
    """For a run that stopped: the clock left in BROM or fastboot shows a dark screen."""
    try:
        found = ((ctx.state.get("detect") if ctx is not None else None) or detect)()
    except Exception:
        return ""
    for m in (Mode.FASTBOOT, Mode.BROM, Mode.PRELOADER, Mode.DA):
        if m in found:
            return IN_A_MODE.format(mode=m.value)
    return ""
