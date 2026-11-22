"""Current IANA time zone data, and the clock's time zone. Ported from the Smart Display tool.

The clock shipped with a 2021 edition of the time zone database and never gets updates, so regions
whose daylight saving rules changed since (Alberta, among others, keeps UTC-6 all year from 2026) are
an hour off for part of the year. assets/tzdata/tzdata is built from the official IANA release (public
domain) by spikes/tzdata/build_tzdata.py, in Android 8's format. On the Smart Display (Android Things
8.1, like the clock) the C library, Java and Kiosk Satellite's clock all followed it.

The display tool writes it over /system. The clock's /system is its stock, verified image, so here it
goes in as a Magisk module instead: Magisk mounts the file over the old one at every start, and the
clock's system image stays untouched. Undo: remove the module in the Magisk app (or delete its folder),
then restart.
"""
from __future__ import annotations

import struct

from lck import clock, doc
from lck.downloads import _repo_file
from lck.steps import Step

DEVICE_PATH = "/system/usr/share/zoneinfo/tzdata"
MODULE = "/data/adb/modules/lck-tzdata"
MODULE_FILE = MODULE + DEVICE_PATH
TMP = "/data/local/tmp/lck-tzdata"
MODULE_PROP = ("id=lck-tzdata\nname=Time zone data (lck)\nversion={v}\nversionCode=1\nauthor=lck\n"
               "description=Current IANA time zone data ({v}) over the clock's own 2021 copy.\n")
SYSTEM_FILE = "u:object_r:system_file:s0"
ZONE_PROP = "persist.sys.timezone"


def version(data: bytes) -> str:
    return data[6:12].split(b"\0", 1)[0].decode("ascii", "replace")


def zone_names(data: bytes | None = None) -> set[str]:
    """Every zone in the file (ZoneCompactor format): a 52-byte index entry per zone."""
    data = data if data is not None else bundled().read_bytes()
    index_offset, data_offset = struct.unpack(">ii", data[12:20])
    return {data[i:i + 40].rstrip(b"\0").decode("ascii")
            for i in range(index_offset, data_offset, 52)}


def bundled():
    return _repo_file("assets/tzdata/tzdata")


def _head(ctx, path: str) -> str:
    return clock.su(ctx, f"head -c 12 {path}", check=False)


def _version_of(ctx, path: str) -> str:
    """The version in the file's 12-byte header. Its last byte is a NUL ("tzdata2026f\\0"), which adb
    passes through and strip() leaves: cut there, as version() does."""
    head = _head(ctx, path).strip()
    return head.split("\0", 1)[0][6:].strip() if head.startswith("tzdata") else ""


def in_use(ctx) -> str:
    """The version the clock reads now (the module's, once Magisk has mounted it)."""
    return _version_of(ctx, DEVICE_PATH)


class TzdataStep(Step):
    name = "tzdata"
    title = "Update the time zone data"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def done(self, ctx) -> bool:
        want = version(bundled().read_bytes())
        return _version_of(ctx, MODULE_FILE) >= want and in_use(ctx) >= want

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        data = bundled().read_bytes()
        v = version(data)
        if _version_of(ctx, MODULE_FILE) < v:
            clock.adb(ctx, "push", bundled(), TMP, timeout=120)
            prop = ctx.paths.root / "work" / "lck-tzdata.prop"
            prop.parent.mkdir(parents=True, exist_ok=True)
            prop.write_text(MODULE_PROP.format(v=v), encoding="utf-8", newline="\n")
            clock.adb(ctx, "push", prop, TMP + ".prop", timeout=60)
            folder = MODULE_FILE.rsplit("/", 1)[0]
            # Magisk mounts the module's file as it is, label included: system_file, like the one it covers,
            # or apps couldn't read it.
            clock.su(ctx, f"mkdir -p {folder} && cp {TMP} {MODULE_FILE} && cp {TMP}.prop {MODULE}/module.prop && "
                          f"chmod 644 {MODULE_FILE} {MODULE}/module.prop && "
                          f"chcon -R {SYSTEM_FILE} {MODULE}/system && rm {TMP} {TMP}.prop")
            if _version_of(ctx, MODULE_FILE) != v:
                raise clock.SetupFailed(f"The time zone data didn't land in {MODULE}. Nothing else was changed; "
                                        "please report this, with the log.")
        clock.reboot(ctx, why="the clock to restart with the new time zone data")
        now = in_use(ctx)
        if now >= v:
            ctx.ui.say(f"Time zone data updated to {v}.")
        else:
            # Not a stop: the clock works as before, with its old rules.
            ctx.ui.say(f"The time zone data is installed, but the clock still reads {now or 'its old copy'}. "
                       "Please report this, with the log. Meanwhile a zone that keeps one offset all year "
                       "works: see " + doc("docs/TIMEZONES.md") + ".")


ASK_ZONE = ("The clock's time zone is {current}. Press Enter to keep it, or type another one's name (for "
            "example America/Edmonton): ")


class TimezoneStep(Step):
    name = "timezone"
    title = "Check the clock's time zone"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        names = zone_names()
        current = clock.prop(ctx, ZONE_PROP)
        ctx.ui.say("The clock keeps the time zone it had before. Dashboards in Kiosk Satellite use the "
                   "clock's built-in browser, which has its own, older rules: if your region changed its "
                   "daylight saving rules lately, see " + doc("docs/TIMEZONES.md") + ".")
        while True:
            zone = ctx.ui.ask(ASK_ZONE.format(current=current or "not set")).strip()
            if not zone or zone == current:
                return
            if zone in names:
                break
            ctx.ui.say(f"{zone} isn't a time zone name. Names look like America/Edmonton or Europe/Berlin.")
        clock.su(ctx, f"setprop {ZONE_PROP} {zone}")
        if TzdataStep().done(ctx):
            # The next step restarts the clock when it installs the data; with the data there already, here.
            clock.reboot(ctx, why="the clock to restart in its new time zone")
        ctx.ui.keep(f"Time zone set to {zone}.")
