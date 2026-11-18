"""Over adb on Wi-Fi: find the clock, switch Google off (after root), add a keyboard."""
from __future__ import annotations

import json
import re
import time

from lck import clock
from lck.clock import SetupFailed, shell
from lck.downloads import fetch
from lck.steps import Step

CAST = "com.google.android.apps.mediashell"
# Cast is a persistent app in /oem/app: Android starts it at boot even when disabled, and it
# grew to about 594 MB of the clock's 1 GB. `pm uninstall -k --user 0` didn't last on the clocks
# tested (e2e runs 2 and 3, with and without disabling first): it was back after a restart. What
# lasts: a Magisk boot script that mounts an empty folder over Cast's app folder. Undo: delete the
# script and the folder, and restart.
CAST_DIR = "/oem/app/com.google.android.apps.mediashell"
HIDE_SCRIPT = "/data/adb/post-fs-data.d/hide-cast.sh"
EMPTY_DIR = "/data/adb/empty-cast"
HIDE_CAST = f"""#!/system/bin/sh
# lck: hide Cast from Android. Undo: delete this file and {EMPTY_DIR}, then restart.
mkdir -p {EMPTY_DIR}
mount -o bind {EMPTY_DIR} {CAST_DIR}
"""
DISABLE = ["com.google.assistant.launcher", "com.google.assistant.core",
           "com.google.android.things.checkin", "com.google.android.things.customcheckin"]
# Lenovo's Things app drives the amp, mics, volume keys and light sensor. Never disable it.
THINGS_APP = "com.google.android.things.sparrow.oem"
IME = "rkr.simplekeyboard.inputmethod/.latin.LatinIME"
IME_PKG = "rkr.simplekeyboard.inputmethod"
WHY_WIFI = ("From here on the tool talks to the clock over Wi-Fi: once Android runs, the clock's USB port "
            "only works as a host, so the computer can't reach it over the cable.")
WHERE_IP = ("On the clock, swipe up from the bottom, tap the gear (Settings), then About device: the address is "
            "under IP address. If the clock shows Google's setup screen instead, the address is in the line at its "
            "top (IP=…). Your router's list of devices shows it too.")
WHERE_IP_FIRST_START = ("Wait until the clock has started (the first start takes a few minutes): it shows its clock "
                        "face, or Google's setup screen. On the setup screen, the address is in the line at the top "
                        "(IP=…). Otherwise swipe up from the bottom, tap the gear (Settings), then About device: the "
                        "address is under IP address. Your router's list of devices shows it too.")
ASK_IP = "Type the clock's address, such as 10.0.0.23, and press Enter: "
ASK_IP_OR_WIFI = "Type the clock's address, such as 10.0.0.23, or type wifi to enter the Wi-Fi again: "


def channel(ctx) -> str:
    """The update channel update_engine targets; none-channel once misc has it."""
    lines = shell(ctx, "update_engine_client", "--show_channel", check=False, timeout=30).strip().splitlines()
    return lines[-1] if lines else ""


WIFI_RETRY = ("If the clock says \"Can't connect to Wi-Fi\", or has no address (no IP= at the top of the setup "
              "screen, none under About device), it didn't get on the Wi-Fi: the name or password may have been "
              "mistyped. Type wifi to enter them again: the "
              "tool rebuilds the user data and writes only that again, in fastboot mode.")


def typed_wifi(ctx) -> bool:
    return ctx.state.get("wifi_ready") is False or bool(ctx.state.get("wifi_new"))


def ask_ip(ctx, starting: bool = False) -> str | None:
    """Sets ctx.state["ip"]. Returns "wifi" when the person wants to re-enter typed Wi-Fi details."""
    ctx.ui.say(WHY_WIFI)
    # Right after a flash the clock is still dark: "swipe up" means nothing yet (e2e run 4).
    ctx.ui.physical(WHERE_IP_FIRST_START if starting else WHERE_IP)
    retry = typed_wifi(ctx)
    if retry:
        ctx.ui.say(WIFI_RETRY)
    while True:
        answer = ctx.ui.ask(ASK_IP_OR_WIFI if retry else ASK_IP).strip()
        if retry and answer.lower() == "wifi":
            return "wifi"
        if clock.valid_ip(answer):
            ctx.state["ip"] = answer
            return None
        ctx.ui.say(f'"{answer}" isn\'t an address. It looks like four numbers with dots between them.')


WIFI_WHY = ("After the flash, the tool can only reach the clock over Wi-Fi (once Android runs, its USB port can't "
            "be used), on the network the clock is connected to when it's backed up. A network it saved once isn't "
            "enough: it has to be one that works now.")
WIFI_QUESTION = ("Is the clock booted and connected to Wi-Fi? If not, connect it to Wi-Fi using the Google Home app "
                 "before typing **YES**. Or you can type **NO**, and enter your Wi-Fi name and password in the tool "
                 "later: ")
WIFI_SAVED_BACKUP = ("A backup of this clock is already saved, and the setup uses it as it is: answer for the clock as "
                     "it was when that backup was made.")


def ask_wifi_ready(ctx) -> bool:
    """Step 1's full stop (the person's wording, e2e run 5): YES, the clock is on a working Wi-Fi now
    (the backup then carries it), or NO, the Wi-Fi name and password are typed in later."""
    ctx.ui.say(WIFI_WHY)
    from lck.images import backup_dir
    from lck import backup
    if not backup.verify_backup(backup_dir(ctx)):
        ctx.ui.say(WIFI_SAVED_BACKUP)
    while True:
        answer = ctx.ui.ask(WIFI_QUESTION).strip().lower()
        if answer in ("yes", "y"):
            return True
        if answer in ("no", "n"):
            return False
        ctx.ui.say(f'"{answer}" isn\'t an answer here. Type YES or NO.')


def ask_wifi(ctx):
    """The Wi-Fi name and password, for the clock's user data."""
    from lck.userdata import UserdataFailed, check_wifi
    ctx.ui.say("The clock needs a Wi-Fi network to come back on after the flash. The tool adds yours to its user "
               "data. The password is kept only in the work folder's images, like a backup's own, and never logged.")
    while True:
        ssid = ctx.ui.ask("Wi-Fi name (as your phone shows it, 2.4 GHz if your router has two): ").strip()
        psk = ctx.ui.ask_secret("Wi-Fi password (not shown as you type): ")
        try:
            check_wifi(ssid, psk)
            return ssid, psk
        except UserdataFailed as e:
            ctx.ui.say(str(e))


def ask_if_ip_changed(ctx) -> None:
    """The clock is slow to answer after a restart: its address may have changed."""
    ctx.ui.say(f"No answer from the clock at {ctx.state.get('ip')} yet. If its screen shows the clock face or "
               "Kiosk Satellite, its address may have changed.")
    ctx.ui.physical(WHERE_IP)
    while True:
        answer = ctx.ui.ask("Type its new address, or press Enter to keep waiting: ").strip()
        if not answer:
            return
        if clock.valid_ip(answer):
            ctx.state["ip"] = answer
            return
        ctx.ui.say(f'"{answer}" isn\'t an address. It looks like four numbers with dots between them.')


def check_saved_ip(ctx) -> None:
    """E2E run 2: the clock's address changed between runs. An address from an earlier run is
    used only if a clock still answers there; otherwise ask once, before any step sends the
    person to fastboot for nothing."""
    f = ctx.paths.progress / f"{ctx.state.get('progress_id')}.json"
    try:
        saved = json.loads(f.read_text(encoding="utf-8")).get("ip")
    except (OSError, ValueError):
        return
    if not saved:
        return
    ctx.state["ip"] = saved
    # A clock that's just starting answers within a minute: give it that before asking (e2e run 3).
    with ctx.ui.busy(f"Looking for the clock at {saved}, its address on the last run"):
        end = time.monotonic() + 60
        while not clock.is_clock(ctx):
            if time.monotonic() > end:
                break
            time.sleep(5)
        else:
            return
    ctx.ui.say(f"No clock answers at {saved}, its address on the last run. If the clock is running "
               "Android now (its own screen, not dark), its address may have changed.")
    ctx.ui.physical(WHERE_IP)
    while True:
        answer = ctx.ui.ask("Type its address, or press Enter if it isn't running Android (stock, off, or "
                            "dark in BROM or fastboot): ").strip()
        if not answer:
            ctx.state["ip"] = None
            return
        if clock.valid_ip(answer):
            ctx.state["ip"] = answer
            return
        ctx.ui.say(f'"{answer}" isn\'t an address. It looks like four numbers with dots between them.')


class ConnectStep(Step):
    name = "connect"
    title = "Find the clock on Wi-Fi and check it started slot A"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        return (clock.converted(ctx) and "none-channel" in channel(ctx)
                and shell(ctx, "settings", "get", "system", "user_rotation", check=False).strip() == "1")

    def run(self, ctx) -> None:
        if not ctx.state.get("ip"):
            while ask_ip(ctx, starting=True) == "wifi":
                self._retry_wifi(ctx)
        while not clock.connect(ctx):
            ctx.ui.say(f"No clock answers at {ctx.state['ip']} yet. The first start takes a few minutes; the "
                       "screen shows the clock face when it's ready. Its address can also change after a flash.")
            ctx.ui.physical(WHERE_IP)
            retry = typed_wifi(ctx)
            if retry:
                ctx.ui.say(WIFI_RETRY)
            answer = ctx.ui.ask("Type its address if it's different, " + ("type wifi to enter the Wi-Fi again, "
                                if retry else "") + "or press Enter to keep waiting: ").strip()
            if retry and answer.lower() == "wifi":
                self._retry_wifi(ctx)
                while ask_ip(ctx, starting=True) == "wifi":
                    self._retry_wifi(ctx)
                continue
            if clock.valid_ip(answer):
                ctx.state["ip"] = answer
                continue
            try:
                clock.wait_booted(ctx, timeout=300)
            except SetupFailed:
                ctx.state["ip"] = None
                while ask_ip(ctx) == "wifi":
                    self._retry_wifi(ctx)
        clock.require_clock(ctx)
        clock.wait_booted(ctx)
        slot = clock.prop(ctx, "ro.boot.slot_suffix")
        if slot != "_a":
            raise SetupFailed(f"The clock started slot {slot.strip('_').upper() or '?'}, not A: slot A didn't "
                              "start, and it fell back. Please report this, with the log.")
        if "none-channel" not in channel(ctx):
            raise SetupFailed("Google's updates aren't blocked (the update channel isn't none-channel). Run "
                              "the setup again from the misc step: lck setup --from misc")
        set_landscape(ctx)
        ctx.ui.say(f"Connected to the clock at {ctx.state['ip']}, on slot A, with Google's updates blocked.")


    @staticmethod
    def _retry_wifi(ctx) -> None:
        """Mistyped Wi-Fi: the clock started but can't join, so there's no adb. New details, the user
        data rebuilt from the backup with only them, and only userdata written again."""
        from lck import images
        from lck.flash import flash_userdata
        ctx.state["wifi_new"] = ask_wifi(ctx)
        images.rebuild_userdata(ctx)
        flash_userdata(ctx)
        ctx.state["ip"] = None


def set_landscape(ctx) -> None:
    """The clock's panel is portrait and it has no tilt sensor, so apps came up sideways (Magisk in
    runs 4 to 6). Fixed landscape, the right way up, for everything from here (e2e run 6, test 12)."""
    shell(ctx, "settings", "put", "system", "accelerometer_rotation", "0", check=False)
    shell(ctx, "settings", "put", "system", "user_rotation", "1", check=False)


def _packages(ctx, *flags: str) -> set[str]:
    """Packages installed for user 0 (with -d: only the disabled ones)."""
    out = shell(ctx, "pm", "list", "packages", *flags, "--user", "0", check=False)
    return {l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")}


RESTRICTIONS = "/data/system/users/0/package-restrictions.xml"


def wait_saved(ctx, packages, timeout=45, sleep=time.sleep) -> bool:
    """Wait until Android has written the disables to storage. `pm disable-user` answers at once, but
    the file is written 5 to 10 s later, and `adb reboot` doesn't wait for it: a restart inside that
    window lost all four disables (e2e run 4). Needs root to read the file."""
    if not packages:
        return True
    end = time.monotonic() + timeout
    with ctx.ui.busy("Waiting for Android to save the changes") as job:
        while True:
            xml = clock.su(ctx, f"cat {RESTRICTIONS}", check=False)
            saved = [p for p in packages if re.search(rf'<pkg name="{re.escape(p)}"[^>]*enabled="3"', xml)]
            job.note(f"{len(saved)} of {len(packages)}", len(saved), len(packages))
            if len(saved) == len(packages):
                return True
            if time.monotonic() > end:
                return False
            sleep(2)


def cast_hidden(ctx) -> bool:
    return CAST not in _packages(ctx) and not shell(ctx, "pidof", CAST, check=False).strip()


class DegoogleStep(Step):
    name = "degoogle"
    title = "Switch off Cast, Google Assistant and the check-ins"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        installed = _packages(ctx)
        disabled = _packages(ctx, "-d")
        return CAST not in installed and all(p in disabled or p not in installed for p in DISABLE)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        installed = _packages(ctx)
        # Cast: only the Magisk boot script has kept it off through restarts on the clocks tested.
        # Uninstalling for user 0, with or without disabling first and a restart between (e2e runs 2
        # and 3), had it back after the next restart. Root comes before this step for that reason.
        if CAST in installed and not clock.has_root(ctx):
            raise SetupFailed("Switching Cast off for good needs root, and adb doesn't have it. Run the root step "
                              "first: lck setup --from root")
        with ctx.ui.busy("Switching off Google's apps"):
            for p in [*DISABLE, CAST]:
                if p in installed:
                    shell(ctx, "pm", "disable-user", "--user", "0", p, check=False)
            if CAST in installed:
                self._install_hide_script(ctx)
            disabled = [p for p in [*DISABLE, CAST] if p in installed]
            if not wait_saved(ctx, disabled):
                ctx.ui.say("Android hasn't saved every change yet; restarting anyway, and checking after.")
        # Before the restart: a stop at the checks below would otherwise leave it unexplained (e2e run 4).
        ctx.ui.keep("The clock's screen stays blank, or shows four dots, until Kiosk Satellite is installed in "
                    "a few steps: that's expected. Swiping up still opens the settings.")
        clock.reboot(ctx, why="the clock to restart with them switched off")
        if CAST in _packages(ctx):
            raise SetupFailed(f"Cast came back after the restart, even with the boot script at {HIDE_SCRIPT}. "
                              "Please report this, with the log.")
        back = [p for p in DISABLE if p in _packages(ctx) and p not in _packages(ctx, "-d")]
        if back:
            raise SetupFailed(f"{', '.join(back)} came back on after the restart. Please report this, with "
                              "the log.")
        if not shell(ctx, "pidof", THINGS_APP, check=False).strip():
            ctx.ui.say("Note: Lenovo's Things app (volume keys, light sensor) isn't running yet. It usually "
                       "starts within a minute.")

    @staticmethod
    def _install_hide_script(ctx) -> None:
        """A Magisk boot script that mounts an empty folder over Cast's app folder before Android
        looks there (e2e runs 2 and 3: Cast neither listed nor running after a restart)."""
        local = ctx.paths.root / "work" / "hide-cast.sh"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(HIDE_CAST, encoding="utf-8")
        clock.adb(ctx, "push", local, "/data/local/tmp/hide-cast.sh", timeout=60)
        clock.su(ctx, f"mkdir -p {HIDE_SCRIPT.rsplit('/', 1)[0]} {EMPTY_DIR} && "
                      f"cp /data/local/tmp/hide-cast.sh {HIDE_SCRIPT} && chmod 755 {HIDE_SCRIPT} && "
                      f"rm /data/local/tmp/hide-cast.sh")
        if "present" not in clock.su(ctx, f"test -x {HIDE_SCRIPT} && echo present", check=False):
            raise SetupFailed(f"The boot script didn't land at {HIDE_SCRIPT}. Nothing else was changed; please "
                              "report this, with the log.")


class KeyboardStep(Step):
    name = "keyboard"
    title = "Add an on-screen keyboard"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        return shell(ctx, "settings", "get", "secure", "default_input_method", check=False).strip() == IME

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        apk = fetch("simple-keyboard", ctx)
        if IME_PKG not in _packages(ctx):
            with ctx.ui.busy("Installing Simple Keyboard"):
                clock.adb(ctx, "install", "-r", apk, timeout=300)
        shell(ctx, "ime", "enable", IME)
        shell(ctx, "ime", "set", IME)
        if not self.done(ctx):
            raise SetupFailed("The keyboard installed, but Android didn't switch to it.")
