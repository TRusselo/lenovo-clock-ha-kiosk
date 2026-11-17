"""Steps 11 to 13: Kiosk Satellite. Install the newest release (verified), grant
what it needs, make it device owner, then walk the person through its setup
on the clock.

The app's settings file is never edited or read: rewriting it drops its
SELinux categories and the app falls back to its setup wizard, and it holds
the Home Assistant token and the remote admin password.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import time
from pathlib import Path

import requests

from lck import clock, doc
from lck.apkcert import check as check_cert
from lck.clock import adb, shell
from lck.steps import Step
from lck.ui import choose

PKG = "me.jxl.kiosk_satellite"
ADMIN = f"{PKG}/.KioskAdminReceiver"
# Signing certificate of jxlarrea's releases (also pinned by echo-dot-assist).
KS_CERT_SHA256 = "2a77134ce11f65ba9d08bed669fdcb742a25633c5665a6135aeca30040b068fc"
RELEASES = "https://api.github.com/repos/jxlarrea/kiosk-satellite/releases/latest"
GRANTS = [
    ["appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow"],
    ["appops", "set", PKG, "WRITE_SETTINGS", "allow"],
    ["appops", "set", PKG, "SYSTEM_ALERT_WINDOW", "allow"],
    ["appops", "set", PKG, "GET_USAGE_STATS", "allow"],
    ["dumpsys", "deviceidle", "whitelist", f"+{PKG}"],
    ["cmd", "notification", "allow_listener", f"{PKG}/.MediaSessionListener"],
    ["dpm", "set-active-admin", ADMIN],
]


class KioskError(Exception):
    pass


def _rate_limited(r) -> None:
    headers = getattr(r, "headers", None) or {}
    if getattr(r, "status_code", 200) in (403, 429) and headers.get("X-RateLimit-Remaining") == "0":
        when = ""
        if str(headers.get("X-RateLimit-Reset", "")).isdigit():
            when = " after " + datetime.datetime.fromtimestamp(int(headers["X-RateLimit-Reset"])).strftime("%H:%M")
        raise KioskError("GitHub is limiting lookups from your internet connection right now. It allows "
                         "60 an hour, shared by every device on your network. Run the same command "
                         f"again{when}. Or reach GitHub from another address: turn a VPN on or off, or "
                         "change its location (a shared VPN address often has the 60 used up already). Or set "
                         "GITHUB_TOKEN to a GitHub token: " + doc("docs/LINUX.md", "If GitHub limits lookups") + ".")


def _headers() -> dict:
    """GitHub allows 60 anonymous lookups an hour, shared by everyone on the network (e2e run 2
    hit it). A token in GITHUB_TOKEN lifts that; it's only sent to GitHub's API."""
    import os
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def latest_release(http) -> tuple[str, str, str]:
    r = http.get(RELEASES, timeout=30, headers=_headers())
    _rate_limited(r)
    r.raise_for_status()
    for a in r.json().get("assets", []):
        if "armeabi-v7a" in a["name"] and a["name"].endswith(".apk"):
            digest = a.get("digest") or ""
            if not digest.startswith("sha256:"):
                raise KioskError("The Kiosk Satellite release has no published checksum.")
            return a["browser_download_url"], digest.split(":", 1)[1], a["name"]
    raise KioskError("The newest Kiosk Satellite release has no armeabi-v7a APK.")


def verify_apk(path: Path, sha256: str, cert: str) -> None:
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha256:
        raise KioskError("The Kiosk Satellite download doesn't match its published checksum.")
    check_cert(str(path), cert)


def _http(ctx):
    return ctx.state.get("http") or requests


def fetch_apk(ctx) -> Path:
    """The newest Kiosk Satellite APK, checked. The release lookup is saved in
    the workdir, so a rerun doesn't ask GitHub again."""
    folder = ctx.paths.cache / "kiosk"
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / "release.json"
    cert = ctx.state.get("ks_cert", KS_CERT_SHA256)
    if saved.exists():
        rel = json.loads(saved.read_text(encoding="utf-8"))
        dest = folder / rel["name"]
        if dest.exists():
            try:
                verify_apk(dest, rel["sha256"], cert)
                return dest
            except Exception:
                dest.unlink(missing_ok=True)
    http = _http(ctx)
    url, sha, name = latest_release(http)
    saved.write_text(json.dumps({"url": url, "sha256": sha, "name": name}), encoding="utf-8")
    dest = folder / name
    with http.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    try:
        verify_apk(dest, sha, cert)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


REMOTE_APK = "/data/local/tmp/kiosk-satellite.apk"


def push_with_progress(ctx, local: Path, remote: str, text: str, sleep=time.sleep) -> None:
    """adb push in the background, with a bar from the file's size on the clock (e2e run 4: the
    ~100 MB copy over Wi-Fi is the slow part, and showed only a time)."""
    total = Path(local).stat().st_size
    log = ctx.paths.root / "work" / "push-output.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = ctx.runner.spawn([ctx.state["tools"].adb, "-s", clock.address(ctx), "push", local, remote],
                            out_file=log)
    try:
        with ctx.ui.busy(text) as job:
            while proc.poll() is None:
                got = shell(ctx, "stat", "-c", "%s", remote, check=False, timeout=15).strip()
                if got.isdigit():
                    done = min(int(got), total)
                    job.note(f"{done >> 20} of {total >> 20} MB", done, total)
                sleep(2)
    except BaseException:
        proc.kill()
        ctx.runner.finished(proc)
        raise
    out = ctx.runner.finished(proc)
    if proc.returncode != 0:
        raise KioskError("Copying Kiosk Satellite to the clock stopped: " + (out.strip().splitlines() or ["?"])[-1])


class InstallStep(Step):
    name = "kiosk-install"
    title = "Install Kiosk Satellite"

    def done(self, ctx) -> bool:
        return "package:" in shell(ctx, "pm", "path", PKG, check=False)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        dest = fetch_apk(ctx)
        push_with_progress(ctx, dest, REMOTE_APK, "Copying Kiosk Satellite to the clock over Wi-Fi")
        with ctx.ui.busy("Installing Kiosk Satellite"):
            r = shell(ctx, "pm", "install", "-r", "-g", REMOTE_APK, check=False, timeout=600)
            shell(ctx, "rm", "-f", REMOTE_APK, check=False)
        if "Success" not in r and not self.done(ctx):
            raise KioskError("Kiosk Satellite didn't install. The log has Android's reason.")
        if not self.done(ctx):
            raise KioskError("Kiosk Satellite didn't install. The log has Android's reason.")


def is_device_owner(ctx) -> bool:
    """Device owner implies an active device admin: it's what lets Kiosk
    Satellite update itself silently and keep the home screen."""
    out = shell(ctx, "dumpsys", "device_policy", check=False)
    return "Device Owner" in out and PKG in out.split("Device Owner", 1)[1][:400]


class GrantsStep(Step):
    name = "kiosk-grants"
    title = "Give Kiosk Satellite its permissions and make it device owner"

    def done(self, ctx) -> bool:
        return is_device_owner(ctx) and "allow" in shell(
            ctx, "appops", "get", PKG, "REQUEST_INSTALL_PACKAGES", check=False)

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        for g in GRANTS:
            shell(ctx, *g)
        if not is_device_owner(ctx):
            shell(ctx, "dpm", "set-device-owner", ADMIN)
        if not self.done(ctx):
            raise KioskError("Kiosk Satellite isn't device owner. That needs a clock with no Google account "
                             "on it; please report this, with the log.")


def home_app(ctx) -> str:
    out = shell(ctx, "cmd", "package", "resolve-activity", "--brief", "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.HOME", check=False)
    lines = [l.strip() for l in out.splitlines() if "/" in l]
    return lines[-1] if lines else ""


class SettingsStep(Step):
    name = "kiosk-settings"
    title = "Set up Kiosk Satellite on the clock"

    def done(self, ctx) -> bool:
        return home_app(ctx).startswith(PKG + "/")

    def run(self, ctx) -> None:
        clock.require_clock(ctx)
        shell(ctx, "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        ctx.ui.physical("Kiosk Satellite is open on the clock. Go through its setup pages: a name and an "
                        "admin password, your Home Assistant address and sign-in, the dashboard to show, "
                        "and the voice options. If you have a Kiosk Satellite settings export from another "
                        "device, you can import it instead of filling these in. Permissions are already done by this tool, so on the last "
                        "page just press Finish. Then, in its settings:\n"
                        "  1. Turn on \"Act as the home screen\"\n"
                        "  2. Turn on forced landscape, if the screen is sideways\n"
                        "  3. Leave the mic gain at its default")
        ip = clock.wifi_ip(ctx) or ctx.state.get("ip")
        if ip:
            # The address on a line of its own, so terminals make it clickable.
            ctx.ui.say("Easier in a browser, for typing your Home Assistant address and token:\n"
                       f"  http://{ip}:2324")
        ctx.ui.alert("NOT DONE YET! After you press FINISH and turn on \"Act as the home screen\", come back "
                     "here and press Enter.")
        while True:
            if choose(ctx.ui, "Press Enter here once that's done, or type stop: ", "stop"):
                raise KioskError("Stopped before Kiosk Satellite's setup was finished. Finish it on the "
                                 "clock, then run the same command again.")
            if self.done(ctx):
                break
            ctx.ui.say("Kiosk Satellite isn't the home screen yet. In its settings, turn on \"Act as the home "
                       "screen\", then press Enter here.")
        ctx.ui.say("Kiosk Satellite is the clock's home screen.")
