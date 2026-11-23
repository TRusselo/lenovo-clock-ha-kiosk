"""lck wifi: put a converted clock on another Wi-Fi network, or give it a new password, without setting up again.

Two ways, chosen by whether the clock answers over Wi-Fi:

- Over Wi-Fi (seconds, no USB): Android Things' own WifiSetupService, which adb's shell may start, no root
  needed. Tested on a converted clock (2026-11-22): the right password switches networks. A wrong one fails
  after about 30 seconds, and the clock goes back to the network it was on by itself, without restarting
  (it shows "Wi-Fi lost" for a moment). The service logs "connecting to <name> succeeded" or "failed".
- Over USB (the clock is off Wi-Fi, so adb can't reach it): BROM reads the clock's user data, the network
  replaces its saved networks (lck/userdata.py), e2fsck checks it, and fastboot writes it back as sparse
  pieces, as the setup writes it. Nothing else on the clock changes. Only the new network is kept: the clock is
  off its saved ones anyway, and with two saved networks in range it may pick the old one (live, 2026-11-22).

The name and password never go on a command line, so they never reach the run log: over Wi-Fi they go in a
small script pushed to the clock, which deletes itself as it runs.

A fixed address (lck/staticip.py): Google's Wi-Fi service only does DHCP, so over Wi-Fi the service joins first
(a wrong password still falls back by itself), then root writes the address into the saved network and the
clock restarts on it. Over USB it goes into the user data with the network.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from lck import clock, gpt, userdata

SERVICE = "com.google.wifisetup/.WifiSetupService"
REMOTE = "/data/local/tmp/lck-wifi.sh"
STORE = "/data/misc/wifi/WifiConfigStore.xml"
STORE_TMP = "/data/local/tmp/lck-wifi.xml"
WAIT = 90           # seconds for the service's answer: a wrong password takes about 30
_ANSWER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s.*WifiSetupService: connecting to (.*) (succeeded|failed)\s*$")


class WifiFailed(Exception):
    pass


def connect_script(ssid: str, psk: str) -> str:
    return (f"#!/system/bin/sh\nrm -f {REMOTE}\n"
            f"am startservice -n {SERVICE} -a WifiSetupService.Connect -e ssid {shlex.quote(ssid)} "
            f"-e passphrase {shlex.quote(psk)}\n")


def result(logcat: str, *, since: float, ssid: str) -> str | None:
    """The service's last answer about `ssid` since `since` (the clock's own clock): succeeded, failed, or None."""
    answer = None
    for line in logcat.splitlines():
        m = _ANSWER.match(line)
        if m and float(m.group(1)) >= since and m.group(2) == ssid:
            answer = m.group(3)
    return answer


def current_ssid(ctx) -> str:
    """The network the clock is on now. Kept out of the log, as the name typed in is."""
    out = clock.adb(ctx, "shell", "dumpsys", "wifi", check=False, timeout=30, log_output=False).out
    m = re.search(r"mWifiInfo SSID: (.*?), BSSID", out)
    return m.group(1) if m else ""


def _answer(ctx, since: float, ssid: str) -> str | None:
    out = clock.adb(ctx, "logcat", "-d", "-v", "epoch", "-s", "WifiSetupService:I", check=False, timeout=30,
                    log_output=False).out
    return result(out, since=since, ssid=ssid)


def over_wifi(ctx, ssid: str, psk: str, sleep=time.sleep, static=None) -> bool:
    """Tell the clock the new network over adb. True when it switched (or left for it), False when it
    couldn't join and stayed where it was. static: a StaticIp, written once it has joined."""
    ip = ctx.state["ip"]
    try:
        since = float(clock.shell(ctx, "date", "+%s", check=False, timeout=20).strip() or 0)
    except ValueError:
        since = 0.0
    with tempfile.TemporaryDirectory(dir=_work_root(ctx)) as tmp:
        local = Path(tmp) / "lck-wifi.sh"
        local.write_text(connect_script(ssid, psk), encoding="utf-8", newline="\n")
        clock.adb(ctx, "push", str(local), REMOTE, timeout=60)
    clock.shell(ctx, "sh", REMOTE, check=False, timeout=60)
    answer, reached = None, True
    with ctx.ui.busy(f"Waiting for the clock to join {ssid}") as job:
        for _ in range(WAIT // 5):
            sleep(5)
            reached = clock.connect(ctx, tries=1)
            if reached:
                answer = _answer(ctx, since, ssid)
                if answer:
                    break
        job.done(f"The clock answered: {answer}" if answer else "No answer from the clock")
    if answer == "failed":
        ctx.ui.say(f"The clock couldn't join {ssid}: the password is probably wrong, or the network is out of "
                   f"range. It went back on the network it was on, at {ip}: nothing changed. Run lck wifi again "
                   "to try again.", log=False)
        if ctx.state.get("log"):
            ctx.state["log"].info("The clock couldn't join the new network, and went back.")
        return False
    if reached and current_ssid(ctx) == ssid:
        if static is not None:
            set_static(ctx, ssid, psk, static)
            ctx.ui.ok(f"The clock is on {ssid}, at its fixed address {static.address}.")
            return True
        ctx.ui.ok(f"The clock is on {ssid}, at {clock.wifi_ip(ctx) or ip}.")
        return True
    if answer is None and reached:
        raise WifiFailed(f"The clock didn't say whether it joined {ssid}, and it's still at {ip}. Run lck wifi "
                         "again, and if it says the same, please report it with the log.")
    ctx.ui.ok(f"The clock left this network, for {ssid}. Its new address there is in your router's list of "
              "devices. If it doesn't show up there within a few minutes, run lck wifi again: with the clock "
              "off Wi-Fi, it uses USB.")
    if static is not None:
        ctx.ui.say(f"Its fixed address ({static.address}) isn't set yet: this computer can't reach the clock on "
                   "that network. Run lck wifi again from a computer on it, with the same name, password and "
                   "fixed address.", log=False)
    return True


def set_static(ctx, ssid: str, psk: str, static) -> None:
    """With root: the saved network again, with the fixed address, then a restart, and the clock found there.
    The file holds the password: pushed, never on a command line, and deleted on both sides."""
    old = clock.boot_id(ctx)
    xml = clock.adb(ctx, "shell", "su", "-c", shlex.quote(f"cat {STORE}"), check=False, timeout=30,
                    log_output=False).out
    if "</NetworkList>" not in xml:
        raise WifiFailed("The tool couldn't read the clock's saved networks with root, so its fixed address "
                         "isn't set. It stays on the network, with an automatic address.")
    new = userdata.with_network(xml, ssid, psk, keep_others=True, static=static)
    with tempfile.TemporaryDirectory(dir=_work_root(ctx)) as tmp:
        local = Path(tmp) / "lck-wifi.xml"
        local.write_text(new, encoding="utf-8", newline="\n")
        clock.adb(ctx, "push", str(local), STORE_TMP, timeout=60)
    clock.su(ctx, f"cp {STORE_TMP} {STORE} && chown 1000:1000 {STORE} && chmod 600 {STORE} && "
                  f"restorecon {STORE} && sync", check=False, timeout=30)
    clock.shell(ctx, "rm", "-f", STORE_TMP, check=False, timeout=15)
    # Straight after, so Android has no time to write the file over: it reads it as it starts. Its own adb
    # call: a reboot inside su left adb shell hanging until it timed out (live, 2026-11-23).
    try:
        clock.adb(ctx, "reboot", check=False, timeout=30)
    except subprocess.TimeoutExpired:
        pass
    ctx.state["ip"] = static.address
    if not clock.wait_restarted(ctx, old, why=f"the clock to restart at {static.address}", give_up=420):
        raise WifiFailed(f"The clock didn't come back at {static.address}. If its screen shows the clock face, "
                         "the address may not work on this network (it may be taken, or outside your router's "
                         "network). Run lck wifi again with the clock off Wi-Fi: it uses USB, and you can type "
                         "another address, or none for automatic.")


def _work_root(ctx) -> Path:
    folder = ctx.paths.root / "work"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def folder(ctx) -> Path:
    return _work_root(ctx) / f"wifi-{ctx.state.get('progress_id', 'clock')}"


def over_usb(ctx, ssid: str, psk: str, static=None) -> None:
    """Read the user data in BROM, save the network in it, write it back in fastboot mode. The copy is
    read again on every run: one read earlier could be older than the clock's user data now."""
    from lck import flash, images, mtk
    from lck.ui import choose
    work = folder(ctx)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    img = work / "userdata.img"
    try:
        images.e2fs_ready(ctx)
        mtk.run_in_brom(ctx, [f"ro 0 {gpt.GPT_BYTES} gpt.bin", "r userdata userdata.img"], cwd=work,
                        title=f"Reading the clock's user data, {READ_TIME}", weights=[1, 300])
        length = gpt.by_name((work / "gpt.bin").read_bytes())["userdata"].size
        if not img.exists() or img.stat().st_size < length:
            raise WifiFailed("The user data came out short. Nothing was written. Run lck wifi again.")
        with ctx.ui.busy("Saving the network in a copy of the user data"):
            r = images.e2fsck(ctx, img, "-fy")
            if r.code not in (0, 1):
                raise WifiFailed("The clock's user data has file system errors e2fsck can't fix. Nothing was "
                                 "written. Please report this, with the log.")
            if not userdata.exists(str(img), "/misc/adb/adb_keys"):
                raise WifiFailed("This clock was never converted (lck setup didn't run on it), so the tool "
                                 "doesn't change its Wi-Fi. Nothing was written. Set its Wi-Fi with the Google "
                                 "Home app.")
            userdata.set_wifi(str(img), ssid, psk, keep_others=False, static=static)
            if images.e2fsck(ctx, img, "-fn").code != 0:
                raise WifiFailed("The edited user data didn't pass its check. Nothing was written. Please "
                                 "report this, with the log.")
        with ctx.ui.busy("Making sparse pieces of the user data"):
            pieces = images._sparse(ctx, img, "userdata", work, length)
        ctx.ui.redraw()
        fixed = f", with the fixed address {static.address}," if static is not None else ""
        ctx.ui.say(f"The clock's user data now has {ssid} saved{fixed} in place of its other networks. Next, "
                   "fastboot mode writes it back, in under a minute. Nothing else on the clock changes.", log=False)
        if not choose(ctx.ui, "Type write to continue, or press Enter to stop: ", "write"):
            raise WifiFailed("Stopped. Nothing was written: the clock is as it was.")
        flash.fbmulti(ctx, [(p, "userdata") for p in pieces], title="Writing the user data")
        flash.restart_from_fastboot(ctx)
    finally:
        # The copy holds the Wi-Fi password: never left behind.
        shutil.rmtree(work, ignore_errors=True)
    if static is not None:
        ctx.state["ip"] = static.address
        ctx.ui.ok(f"Written. The clock starts, and joins {ssid} at {static.address}.")
        return
    ctx.ui.ok(f"Written. The clock starts, and joins {ssid}. Its address there is in your router's list of "
              "devices.")


READ_TIME = "about 20 minutes"
