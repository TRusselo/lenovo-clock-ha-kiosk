"""The setup, in order, numbered like the manual's headings. Each step skips itself
when its work is already done."""
from __future__ import annotations

import shutil
import sys

from lck import backup, clock, doc, e2fs, mtk
from lck.downloads import fetch, load_manifest, platform_entry, require_free_space
from lck.flash import FlashStep, UnlockStep
from lck.images import ImagesStep, MiscStep, UserdataStep, backup_dir, work
from lck.kiosk import GrantsStep, InstallStep, SettingsStep, fetch_apk, is_device_owner
from lck.modes import detect
from lck.root import RootStep
from lck.runner import Tools, platform_arch, platform_id
from lck.steps import Step
from lck.tzdata import TimezoneStep, TzdataStep
from lck import volkeys
from lck.volkeys import VolumeKeysStep
from lck.system import CAST, THINGS_APP, ConnectStep, DegoogleStep, KeyboardStep, channel
from lck.uvrun import uv_path

NEEDED_FREE = 20 * 1024 ** 3
# Linux tools the setup runs, and the package that has each one (Debian/Ubuntu, Arch/Fedora).
# Only debugfs and e2fsck come from the system now (zstd and the sparse images are Python): the e2fsprogs
# package on Linux, Homebrew's on macOS; on Windows the tool downloads them (lck/e2fs.py).
SYSTEM_TOOLS = {
    "debugfs": "e2fsprogs",
    "e2fsck": "e2fsprogs",
}
GUIDE_NAMES = {"linux": "LINUX", "darwin": "MACOS", "windows": "WINDOWS"}


class SetupFailed(Exception):
    pass


def missing_tools(which=shutil.which) -> list[str]:
    return [f"{t} (package: {p})" for t, p in SYSTEM_TOOLS.items() if not which(t)]


def guide() -> str:
    """This OS's guide, as a path for doc()."""
    return f"docs/{GUIDE_NAMES.get(platform_id(), 'LINUX')}.md"


class PrerequisitesStep(Step):
    name = "prerequisites"
    title = "Check this computer, and download the tools"

    def run(self, ctx) -> None:
        require_free_space(ctx.paths.root, NEEDED_FREE)
        detect()                       # libusb present, before the clock is touched
        if backup.verify_backup(backup_dir(ctx)):
            # Windows: the backup needs UsbDk for BROM mode. Before 200 MB of downloads, not after (issue 1).
            from lck.windrivers import check_brom_driver
            check_brom_driver(ctx, ctx.state.get("platform"))
        manifest = ctx.state.get("manifest") or load_manifest()
        for id_ in (f"platform-tools-{platform_id()}", platform_entry("uv", manifest, platform_arch()),
                    "mtkclient", "cube-avb-unlock", "at_auth_unlock.py", "fbmulti.py",
                    "simple-keyboard", "magisk"):
            fetch(id_, ctx)
        e2fs.locate(ctx)              # Linux and macOS: installed; Windows: downloaded now
        ctx.state["tools"] = Tools.locate(ctx.paths.cache)
        uv_path(ctx)
        # Before the clock is touched, so a GitHub limit or outage can't stop the run halfway.
        mtk.prepare(ctx)
        with ctx.ui.busy("Downloading Kiosk Satellite"):
            fetch_apk(ctx)
        # A full stop before the clock is touched: is it on a Wi-Fi that works now?
        if ctx.state.get("wifi_ready") is None and not clock.converted(ctx):
            from lck.system import ask_wifi_ready
            ctx.state["wifi_ready"] = ask_wifi_ready(ctx)


class BackupStep(Step):
    name = "backup"
    title = "Back up the whole clock (BROM)"

    def done(self, ctx) -> bool:
        return not backup.verify_backup(backup_dir(ctx))

    def run(self, ctx) -> None:
        if ctx.state.get("skip_backup"):
            if not clock.converted(ctx):
                ctx.ui.say("This clock isn't converted yet, so the setup needs its backup: the new system "
                           "is built from it.")
            elif backup.confirm_skip(ctx.ui):
                ctx.ui.say("Continuing without a backup.")
                return
            ctx.ui.say("OK, making the backup.")
        out = backup_dir(ctx)
        z = out.with_name(out.name + ".zip")
        if not out.exists() and z.is_file():
            # A finished setup keeps only the zip. Backing up again now would copy the converted clock
            # and pack it over the stock backup the setup is built from: unpack the zip instead.
            folder = backup.unpack(ctx, z, out.parent)
            problems = backup.verify_backup(folder)
            if problems:
                raise backup.BackupMismatch(f"{z} can't be used: " + "; ".join(problems))
            ctx.ui.keep(f"Using the backup already saved in {z}.")
            return
        backup.backup(ctx, out)
        z = backup.pack(ctx, out)
        ctx.ui.keep(f"The backup is also saved as one file: {z}. It's named after --name (clock, if you "
                    "didn't give one). Copy it somewhere safe and keep it private. Once setup finishes, only "
                    "this file is kept: lck restore takes it.")


class VerifyStep(Step):
    name = "verify"
    title = "Final check"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        problems = []
        if clock.prop(ctx, "ro.boot.slot_suffix") != "_a":
            problems.append("the clock isn't running slot A")
        if "none-channel" not in channel(ctx):
            problems.append("Google's updates aren't blocked")
        if not is_device_owner(ctx):
            problems.append("Kiosk Satellite isn't device owner. Run: lck setup --from kiosk-grants")
        if clock.shell(ctx, "pidof", CAST, check=False).strip():
            problems.append("Cast is running. Run: lck setup --from degoogle")
        if not clock.shell(ctx, "pidof", THINGS_APP, check=False).strip():
            problems.append("Lenovo's Things app isn't running, so the light sensor won't work")
        if not volkeys.running(ctx):
            problems.append("the volume button helper isn't running. Run: lck setup --from volume-keys")
        if problems:
            raise SetupFailed("Final check failed: " + "; ".join(problems) + ".")
        z = ctx.paths.backup / f"{ctx.state.get('progress_id', 'clock')}.zip"
        folder = z.with_suffix("")
        if folder.is_dir() and not z.exists() and not backup.verify_backup(folder):
            backup.pack(ctx, folder)      # a backup made before zips, or seeded: zip it first (e2e run 5)
        backup.drop_folder(ctx, folder)
        ip = ctx.state.get("ip")
        nxt = [f"Change Kiosk Satellite's settings from a browser: http://{ip}:2324",
               "If Home Assistant doesn't show the clock, add it: Settings, Devices & services, Kiosk Satellite "
               "(ESPHome). If an old copy of the clock stays unavailable there, remove it.",
               "A dashboard made for the clock's small screen, installed with HACS:\n  https://github.com/TRusselo/lenovo-clock-dashboard",
               "If a dashboard shows times an hour off: " + doc("docs/TIMEZONES.md"),
               "Questions: see " + doc("README.md", "Questions, problems, or it worked?")]

        def closing():
            ctx.ui.ok("All done. The clock is a Home Assistant kiosk.")
            if z.exists():
                ctx.ui.alert(f"SAVE YOUR BACKUP: copy {z} somewhere safe, off this computer if you can, and "
                             "keep it private. It's the only way back to this clock's stock software.")
            ctx.ui.section("What's next", nxt)
        ctx.after.append(closing)


def setup_steps() -> list[Step]:
    return [
        PrerequisitesStep(), BackupStep(), UnlockStep(), ImagesStep(), UserdataStep(), MiscStep(),
        FlashStep(), ConnectStep(), RootStep(), VolumeKeysStep(), TimezoneStep(), TzdataStep(), DegoogleStep(), KeyboardStep(), InstallStep(), GrantsStep(),
        SettingsStep(), VerifyStep(),
    ]
