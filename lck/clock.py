"""Talking to the clock once Android runs: adb over Wi-Fi only, at the clock's
own address, always with -s. Other adb devices on this computer (a phone, a
Smart Display on USB) are never touched.

Before anything is written over adb, the device at that address must be a
Lenovo Smart Clock gen 1 (ro.product.device mt8167s_som).
"""
from __future__ import annotations

import ipaddress
import re
import time

from lck import doc

PORT = 5555
DEVICE = "mt8167s_som"


class SetupFailed(Exception):
    pass


class NotAClock(Exception):
    pass


def address(ctx) -> str | None:
    ip = ctx.state.get("ip")
    return f"{ip}:{PORT}" if ip else None


def valid_ip(text: str) -> bool:
    try:
        return ipaddress.ip_address(text.strip()).version == 4
    except ValueError:
        return False


def _run(ctx, *args, check=True, timeout=120, log_output=True):
    return ctx.runner.run([ctx.state["tools"].adb, *args], check=check, timeout=timeout,
                          log_output=log_output)


def connect(ctx, tries: int = 3) -> bool:
    """adb connect to the clock's address. True once it answers as a device."""
    target = address(ctx)
    if not target:
        return False
    for _ in range(tries):
        _run(ctx, "connect", target, check=False, timeout=20)
        state = _run(ctx, "-s", target, "get-state", check=False, timeout=15).out.strip()
        if state == "device":
            return True
        if "unauthorized" in state:
            raise SetupFailed("The clock refused this computer's adb key. The setup puts this computer's "
                              "key on the clock; if this computer's ~/.android/adbkey changed since, run "
                              "the setup again from the userdata step.")
        _run(ctx, "disconnect", target, check=False, timeout=15)
        time.sleep(2)
    return False


def adb(ctx, *args, check=True, timeout=120, log_output=True):
    target = address(ctx)
    if not target:
        raise SetupFailed("The tool doesn't know the clock's address yet. Run the same command with "
                          "--ip and the clock's address.")
    return _run(ctx, "-s", target, *args, check=check, timeout=timeout, log_output=log_output)


def shell(ctx, *args, check=True, timeout=120) -> str:
    return adb(ctx, "shell", *args, check=check, timeout=timeout).out


def prop(ctx, name: str) -> str:
    return shell(ctx, "getprop", name, check=False, timeout=20).strip()


def is_clock(ctx) -> bool:
    """Reachable over Wi-Fi and really a gen-1 Smart Clock. Never raises. Three tries:
    adb over Wi-Fi often says offline for a moment right after connecting."""
    try:
        return connect(ctx, tries=3) and prop(ctx, "ro.product.device") == DEVICE
    except Exception:
        return False


def require_clock(ctx) -> None:
    if not connect(ctx):
        raise SetupFailed(f"Can't reach the clock at {ctx.state.get('ip')} over Wi-Fi. Check it's on and on "
                          "the same network as this computer, and that the address is right: your router "
                          "lists it. Run the same command with --ip and the right address.")
    device = prop(ctx, "ro.product.device")
    if device != DEVICE:
        raise NotAClock(f"The device at {ctx.state.get('ip')} isn't a Lenovo Smart Clock gen 1 (it says "
                        f"{device or 'nothing'}). Nothing was changed on it. Check the address.")


def converted(ctx) -> bool:
    """Already running stock Android Things from slot A with adb over Wi-Fi: the
    backup, unlock, images and flash steps have nothing left to do."""
    if not ctx.state.get("ip") or not is_clock(ctx):
        return False
    return prop(ctx, "ro.boot.slot_suffix") == "_a"


def booted(ctx) -> bool:
    try:
        return connect(ctx, tries=1) and prop(ctx, "sys.boot_completed") == "1"
    except Exception:
        return False


def wait_booted(ctx, timeout=600, why="the clock to start", sleep=time.sleep) -> None:
    """Wait until Android has finished starting and adb over Wi-Fi answers. Silent when it has."""
    if booted(ctx):
        return
    end = time.monotonic() + timeout
    with ctx.ui.busy(f"Waiting for {why}"):
        while not booted(ctx):
            if time.monotonic() > end:
                raise SetupFailed(f"The clock didn't come back on Wi-Fi at {ctx.state.get('ip')} within "
                                  f"{timeout // 60} minutes. If its screen shows the clock face, check its "
                                  "address in your router, then run the same command with --ip. More help: "
                                  + doc("docs/GUIDE.md", "If the clock doesn't come back"))
            sleep(3)


def reboot(ctx, why="the clock to restart") -> None:
    adb(ctx, "reboot", check=False, timeout=30)
    time.sleep(10)
    _run(ctx, "disconnect", address(ctx), check=False, timeout=15)
    wait_booted(ctx, why=why)


def wifi_ip(ctx) -> str:
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", shell(ctx, "ip", "-4", "addr", "show", "wlan0", check=False))
    return m.group(1) if m else ""


def has_root(ctx) -> bool:
    try:
        return "uid=0(root)" in shell(ctx, "su", "-c", "id", check=False, timeout=30)
    except Exception:
        return False
