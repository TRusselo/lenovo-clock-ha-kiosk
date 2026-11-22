"""The clock's volume buttons. Ported from the Smart Display tool.

The buttons are GPIOs (VOLUME UP is GPIO 429, VOLUME DOWN is GPIO 576, the PMIC's key pin; both 1
while pressed), read by Android Things' sparrow.oem, which hands them to a Google listener that
doesn't run on the converted clock: they did nothing. The display tool's helper, VolKeys
(assets/volkeys/VolKeys.java, built into ks-volkeys.jar by scripts/build-volkeys.sh), watches the
two pins and injects volume key presses through InputManager, 5% a press, repeating while held.

The display tool starts it from an init file on /system. The clock's /system is its stock, verified
image, so here it's a Magisk module: Magisk runs its service.sh as root at every start. The mic switch
(GPIO 410) is left alone. Undo: remove the module in the Magisk app, then restart.
"""
from __future__ import annotations

import hashlib
import time

from lck import clock, doc
from lck.clock import SetupFailed
from lck.downloads import _repo_file
from lck.steps import Step

MODULE = "/data/adb/modules/lck-volkeys"
JAR = MODULE + "/ks-volkeys.jar"
TMP = "/data/local/tmp/lck-volkeys"
PINS = ("/sys/class/gpio/gpio429/value", "/sys/class/gpio/gpio576/value")   # up, down
MODULE_PROP = ("id=lck-volkeys\nname=Volume buttons (lck)\nversion=1\nversionCode=1\nauthor=lck\n"
               "description=Makes the clock's volume buttons change the volume.\n")
# LF endings whatever the computer: a CRLF script fails on Android ("sh\r": the display tool's issue 3).
# sparrow.oem exports the pins soon after the start; if it hasn't after a minute, this does.
SERVICE = """#!/system/bin/sh
# lck: the clock's volume buttons (VolKeys.java in the lck repo). Magisk runs this at every start.
MODDIR=${0%/*}
ps -A -o ARGS | grep -q '[k]s.VolKeys' && exit 0
until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 2; done
n=0
while [ ! -e /sys/class/gpio/gpio429/value ] || [ ! -e /sys/class/gpio/gpio576/value ]; do
  n=$((n + 1))
  if [ $n -ge 30 ]; then
    for g in 429 576; do
      [ -e /sys/class/gpio/gpio$g ] || { echo $g > /sys/class/gpio/export; echo in > /sys/class/gpio/gpio$g/direction; }
    done
  fi
  sleep 2
done
export CLASSPATH=$MODDIR/ks-volkeys.jar
while true; do
  app_process /system/bin ks.VolKeys /sys/class/gpio/gpio429/value /sys/class/gpio/gpio576/value
  sleep 5
done
"""


def bundled():
    return _repo_file("assets/volkeys/ks-volkeys.jar")


def running(ctx) -> bool:
    return "ks.VolKeys" in clock.shell(ctx, "ps", "-A", "-o", "ARGS", check=False)


def _installed(ctx) -> bool:
    want = hashlib.md5(bundled().read_bytes()).hexdigest()
    return clock.su(ctx, f"md5sum {JAR}", check=False).split()[:1] == [want]


def _volume(ctx) -> int:
    v = clock.shell(ctx, "settings", "get", "system", "volume_music_speaker", check=False).strip()
    return int(v) if v.isdigit() else -1


def _pressed(ctx, before: int, sign: int, wait: float, sleep) -> bool:
    for _ in range(int(wait)):
        if (_volume(ctx) - before) * sign >= 3:
            return True
        sleep(1)
    return False


def check_buttons(ctx, sleep=time.sleep, wait: float = 60) -> None:
    """Each button once, seen as a change of the music volume. Up first, unless it's at the top already."""
    ctx.ui.alert("NOT DONE YET! THIS TOOL NEEDS YOU TO PRESS THE VOLUME BUTTONS ON THE CLOCK.")
    order = [("VOLUME UP", 1), ("VOLUME DOWN", -1)]
    if _volume(ctx) >= 95:
        order.reverse()
    for name, sign in order:
        while True:
            before = _volume(ctx)
            ctx.ui.physical(f"Press {name} on the clock once.")
            if _pressed(ctx, before, sign, wait, sleep):
                break
            ctx.ui.redraw()
            ctx.ui.say(f"Still waiting for {name}. If the button doesn't change the volume, press Ctrl-C and run "
                       "the same command again: setup goes on without this check. See "
                       + doc("docs/GUIDE.md", "Volume buttons") + ".")
    ctx.ui.say("Volume buttons work.")


class VolumeKeysStep(Step):
    name = "volume-keys"
    title = "Make the volume buttons work"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        return _installed(ctx) and running(ctx)

    def run(self, ctx, sleep=time.sleep) -> None:
        clock.require_clock(ctx)
        if not _installed(ctx):
            folder = ctx.paths.root / "work" / "lck-volkeys"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "service.sh").write_bytes(SERVICE.encode())
            (folder / "module.prop").write_bytes(MODULE_PROP.encode())
            clock.adb(ctx, "push", bundled(), TMP + ".jar", timeout=60)
            clock.adb(ctx, "push", folder / "service.sh", TMP + ".sh", timeout=60)
            clock.adb(ctx, "push", folder / "module.prop", TMP + ".prop", timeout=60)
            clock.su(ctx, f"mkdir -p {MODULE} && cp {TMP}.jar {JAR} && cp {TMP}.sh {MODULE}/service.sh && "
                          f"cp {TMP}.prop {MODULE}/module.prop && chmod 644 {JAR} {MODULE}/module.prop && "
                          f"chmod 755 {MODULE}/service.sh && rm {TMP}.jar {TMP}.sh {TMP}.prop")
            if not _installed(ctx):
                raise SetupFailed(f"The volume button helper didn't land in {MODULE}. Nothing else was changed; "
                                  "please report this, with the log.")
        if not running(ctx):
            # Started now, the way Magisk starts it at every start: no restart needed for this step.
            clock.su(ctx, f"setsid sh {MODULE}/service.sh </dev/null >/dev/null 2>&1 &", check=False)
            for _ in range(20):
                if running(ctx):
                    break
                sleep(3)
            else:
                raise SetupFailed("The volume button helper didn't start. Nothing else was changed; please report "
                                  "this, with the log.")
        check_buttons(ctx, sleep)
