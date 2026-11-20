"""Windows only: is the UsbDk driver there for BROM mode, and what does Windows know about the
clock's USB devices? (Issue 1: Windows listed the clock in BROM mode, but mtkclient couldn't open
it, and said nothing about why.)

Windows gives the clock's BROM device (0e8d:0003, a USB serial device) its own serial driver.
libusb can list a device with that driver, but can't open it. mtkclient opens it through UsbDk,
which needs no per-device setup: that matters, because BROM lasts only 3 to 5 seconds.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from lck import doc

SKIP_ENV = "LCK_SKIP_USBDK_CHECK"
USBDK_MISSING = ("Windows needs the UsbDk driver to reach the clock in BROM mode (for the backup and the "
                 "restore), and it isn't installed on this computer. Install it once, as in "
                 + doc("docs/drivers.md", "Windows") + ": download the 64-bit .msi from "
                 "https://github.com/daynix/UsbDk/releases, run it, and restart Windows if it asks. Then run "
                 "the same command again. Nothing was done to the clock.")
USBDK_NOT_WORKING = ("Windows saw the clock in BROM mode, but couldn't open it: the UsbDk driver isn't working. "
                     "Install UsbDk (or install it again) as in " + doc("docs/drivers.md", "Windows") + ", "
                     "restart Windows, then unplug the clock's round power plug and run the same command "
                     "again. Nothing was written to the clock.")
NO_CONNECT = ("The clock showed up in BROM mode, but mtkclient hasn't connected to it. On Windows that usually "
              "means the UsbDk driver isn't installed or isn't working: see " + doc("docs/drivers.md", "Windows")
              + ". If the clock's screen lights up, it started normally: unplug its power and try again.")


class UsbDkMissing(Exception):
    pass


def is_windows(platform: str | None = None) -> bool:
    return (platform or sys.platform).startswith("win")


def _helper_dll() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "UsbDkHelper.dll"


def usbdk_installed(ctx, dll=None) -> bool:
    """UsbDk's service is registered, or its helper library is in System32 (where its installer
    puts it)."""
    dll = dll if dll is not None else _helper_dll()
    try:
        if Path(dll).is_file():
            return True
    except OSError:
        pass
    try:
        r = ctx.runner.run(["sc", "query", "UsbDk"], check=False, timeout=20)
    except Exception:  # sc missing or hung: the file check above decides
        return False
    return r.code == 0 and "UsbDk" in r.out


def check_brom_driver(ctx, platform: str | None = None, dll=None) -> None:
    """Before any BROM step on Windows: stop, with the install steps, if UsbDk isn't there.
    Once per run. LCK_SKIP_USBDK_CHECK=1 skips it, for a UsbDk this check can't see."""
    if not is_windows(platform) or ctx.state.get("usbdk_checked"):
        return
    logger = ctx.state.get("log")
    if os.environ.get(SKIP_ENV):
        if logger:
            logger.info("UsbDk check skipped (%s is set)", SKIP_ENV)
        ctx.state["usbdk_checked"] = True
        return
    found = usbdk_installed(ctx, dll)
    if logger:
        logger.info("UsbDk: %s", "found" if found else "NOT found")
    if not found:
        raise UsbDkMissing(USBDK_MISSING)
    ctx.state["usbdk_checked"] = True


def log_usb_devices(ctx, platform: str | None = None) -> None:
    """Into the run log: what Windows knows about MediaTek USB devices (the clock in BROM and the
    preloader), connected now or seen before, with the driver each one has. For a report."""
    logger = ctx.state.get("log")
    if not is_windows(platform) or logger is None:
        return
    try:
        r = ctx.runner.run(["pnputil", "/enum-devices", "/ids"], check=False, timeout=60, log_output=False)
    except Exception as e:
        logger.info("pnputil didn't run: %s", e)
        return
    blocks = mediatek_devices(r.out)
    logger.info("Windows' MediaTek USB devices (VID_0E8D):\n%s", "\n\n".join(blocks) if blocks else "(none)")


def mediatek_devices(text: str) -> list[str]:
    """pnputil's device blocks (separated by blank lines) that name VID_0E8D."""
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n")]
    return [b for b in blocks if "VID_0E8D" in b.upper()]
