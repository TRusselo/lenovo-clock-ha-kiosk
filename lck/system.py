"""Over adb on Wi-Fi: find the clock, switch Google off (after root), add a keyboard."""
from __future__ import annotations

from lck import clock
from lck.clock import SetupFailed, shell
from lck.downloads import fetch
from lck.steps import Step

CAST = "com.google.android.apps.mediashell"
# Cast is a persistent app in /oem/app: Android starts it at boot even when disabled, and it
# grew to about 594 MB of the clock's 1 GB. `pm uninstall --user 0` answers Success, but Android
# Things installs it again for user 0 at every boot (e2e run 2). What sticks: a Magisk boot
# script that mounts an empty folder over Cast's app folder before Android looks there. So
# this step comes after root. Undo: delete the script and the folder, and restart.
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
WHERE_IP = ("To find the clock's address, on the clock: swipe up from the bottom, tap the gear (Settings), then "
            "About device. It's under IP address. Your router's list of devices shows it too.")
ASK_IP = "Type the clock's address, such as 10.0.0.23, and press Enter: "


def channel(ctx) -> str:
    """The update channel update_engine targets; none-channel once misc has it."""
    lines = shell(ctx, "update_engine_client", "--show_channel", check=False, timeout=30).strip().splitlines()
    return lines[-1] if lines else ""


def ask_ip(ctx) -> None:
    ctx.ui.say(WHY_WIFI)
    ctx.ui.physical(WHERE_IP)
    while True:
        answer = ctx.ui.ask(ASK_IP).strip()
        if clock.valid_ip(answer):
            ctx.state["ip"] = answer
            return
        ctx.ui.say(f'"{answer}" isn\'t an address. It looks like four numbers with dots between them.')


class ConnectStep(Step):
    name = "connect"
    title = "Find the clock on Wi-Fi and check it started slot A"

    def done(self, ctx) -> bool:
        return clock.converted(ctx) and "none-channel" in channel(ctx)

    def run(self, ctx) -> None:
        if not ctx.state.get("ip"):
            ask_ip(ctx)
        while not clock.connect(ctx):
            ctx.ui.say(f"No clock answers at {ctx.state['ip']} yet. The first start takes a few minutes; "
                       "the screen shows the clock face when it's ready.")
            clock.wait_booted(ctx, timeout=300)
            if not clock.connect(ctx):
                ctx.state.pop("ip")
                ask_ip(ctx)
        clock.require_clock(ctx)
        clock.wait_booted(ctx)
        slot = clock.prop(ctx, "ro.boot.slot_suffix")
        if slot != "_a":
            raise SetupFailed(f"The clock started slot {slot.strip('_').upper() or '?'}, not A: slot A didn't "
                              "start, and it fell back. Please report this, with the log.")
        if "none-channel" not in channel(ctx):
            raise SetupFailed("Google's updates aren't blocked (the update channel isn't none-channel). Run "
                              "the setup again from the misc step: lck setup --from misc")
        ctx.ui.say(f"Connected to the clock at {ctx.state['ip']}, on slot A, with Google's updates blocked.")


def _packages(ctx, *flags: str) -> set[str]:
    """Packages installed for user 0 (with -d: only the disabled ones)."""
    out = shell(ctx, "pm", "list", "packages", *flags, "--user", "0", check=False)
    return {l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")}


def cast_hidden(ctx) -> bool:
    return CAST not in _packages(ctx) and not shell(ctx, "pidof", CAST, check=False).strip()


class DegoogleStep(Step):
    name = "degoogle"
    title = "Switch off Cast, Google Assistant and the check-ins"

    def done(self, ctx) -> bool:
        installed = _packages(ctx)
        disabled = _packages(ctx, "-d")
        return CAST not in installed and all(p in disabled or p not in installed for p in DISABLE)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        if not clock.has_root(ctx):
            raise SetupFailed("Hiding Cast needs root, and adb doesn't have it. Run the root step first: "
                              "lck setup --from root")
        installed = _packages(ctx)
        with ctx.ui.busy("Switching off Google's apps"):
            for p in DISABLE:
                if p in installed:
                    shell(ctx, "pm", "disable-user", "--user", "0", p, check=False)
            if CAST in installed:
                local = ctx.paths.root / "work" / "hide-cast.sh"
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(HIDE_CAST, encoding="utf-8")
                clock.adb(ctx, "push", local, "/data/local/tmp/hide-cast.sh", timeout=60)
                shell(ctx, "su", "-c", f"mkdir -p {HIDE_SCRIPT.rsplit('/', 1)[0]} {EMPTY_DIR} && "
                                       f"cp /data/local/tmp/hide-cast.sh {HIDE_SCRIPT} && chmod 755 {HIDE_SCRIPT} "
                                       f"&& rm /data/local/tmp/hide-cast.sh", timeout=60)
        clock.reboot(ctx, why="the clock to restart without them")
        if CAST in _packages(ctx):
            raise SetupFailed(f"Cast came back after the restart: the boot script at {HIDE_SCRIPT} didn't hide "
                              "it. Please report this, with the log.")
        back = [p for p in DISABLE if p in _packages(ctx) and p not in _packages(ctx, "-d")]
        if back:
            raise SetupFailed(f"{', '.join(back)} came back on after the restart. Please report this, with "
                              "the log.")
        ctx.ui.keep("The clock's screen stays blank, or shows four dots, until Kiosk Satellite is installed in "
                    "a few steps: that's expected. Swiping up still opens the settings.")
        if not shell(ctx, "pidof", THINGS_APP, check=False).strip():
            ctx.ui.say("Note: Lenovo's Things app (volume keys, light sensor) isn't running yet. It usually "
                       "starts within a minute.")


class KeyboardStep(Step):
    name = "keyboard"
    title = "Add an on-screen keyboard"

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
