"""Casting Helper, a Kiosk Satellite plugin: cast video and music to the clock from phones and computers.

Downloaded with the other tools, newest release each run. A download that fails, or isn't a Kiosk Satellite
plugin, is silent: nothing is said, and nothing is installed. Not pinned to a checksum like the manifest's
downloads: the address always serves the newest release, and Kiosk Satellite checks the package itself.

Kiosk Satellite installs a plugin ZIP only through its remote API, signed in. Its tokens are HMAC-SHA256 over
a base64url JSON payload with the expiry ({"exp": ms}), keyed with a secret it keeps in its preferences
(lib/managers/remote/auth.dart, settings_manager.dart secret()). With root the tool reads that secret and signs
a 10-minute token, so no admin password is asked, and the install runs through Kiosk Satellite's own checks.
The secret and token are never logged, and never on a command line. Tried on a clock (2026-11-22): installed,
trusted, switched on and running.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import re
import shlex
import time
import zipfile
from pathlib import Path

from lck import clock
from lck.steps import Step

URL = "https://gitlab.tiered.top/api/v4/projects/3/packages/generic/casting-helper/latest/casting-helper.zip"
ID = "casting-helper"
NAME = "Casting Helper"
PREFS = "/data/data/me.jxl.kiosk_satellite/shared_prefs/FlutterSharedPreferences.xml"
SECRET_KEY = "flutter.ks.secret.remote_auth"
PORT = 2324
MAX_BYTES = 4 * 1024 * 1024        # Kiosk Satellite's own limit for a plugin ZIP
TTL_MS = 10 * 60 * 1000

log = logging.getLogger("lck")


def _file(ctx) -> Path:
    return ctx.paths.cache / "downloads" / "casting-helper.zip"


def _http(ctx):
    import requests
    return ctx.state.get("http") or requests


def manifest_of(data: bytes) -> dict | None:
    """The plugin's manifest, or None when `data` isn't a Casting Helper package."""
    try:
        if not 0 < len(data) <= MAX_BYTES:
            return None
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if not {"kiosk-satellite-plugin.json", "plugin.jar"} <= names:
                return None
            m = json.loads(z.read("kiosk-satellite-plugin.json"))
        return m if isinstance(m, dict) and m.get("id") == ID else None
    except Exception:
        return None


def cached(ctx) -> Path | None:
    """This run's download, if there is a good one."""
    f = _file(ctx)
    return f if f.is_file() and manifest_of(f.read_bytes()) else None


def fetch(ctx) -> Path | None:
    """The newest Casting Helper, or None, silently. An older copy is dropped first: a failed download
    leaves nothing, so nothing is installed."""
    f = _file(ctx)
    f.unlink(missing_ok=True)
    try:
        r = _http(ctx).get(URL, timeout=60)
        r.raise_for_status()
        if manifest_of(r.content) is None:
            log.info("Casting Helper: the download isn't a Kiosk Satellite plugin; skipped")
            return None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
        return f
    except Exception as e:
        log.info("Casting Helper: download failed (%s); skipped", type(e).__name__)
        return None


def token(secret: str, now_ms: int | None = None, ttl_ms: int = TTL_MS) -> str:
    """A token Kiosk Satellite accepts, as AuthStore.issueToken makes them."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    payload = base64.urlsafe_b64encode(json.dumps({"exp": now_ms + ttl_ms}).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(sig).decode()}"


def _secret(ctx) -> str | None:
    out = clock.adb(ctx, "shell", "su", "-c", shlex.quote(f"cat {PREFS}"), check=False, timeout=30,
                    log_output=False).out
    m = re.search(rf'name="{re.escape(SECRET_KEY)}">([^<]+)<', out)
    return m.group(1) if m else None


class _Api:
    def __init__(self, ctx, secret: str):
        self.http = _http(ctx)
        self.base = f"http://{ctx.state['ip']}:{PORT}/api/commands/"
        self.headers = {"Authorization": "Bearer " + token(secret), "Content-Type": "application/json"}

    def __call__(self, name: str, params: dict | None = None, timeout=95):
        r = self.http.post(self.base + name, json=params or {}, headers=self.headers, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("error") or f"{name} failed")
        return body.get("data")


class PluginStep(Step):
    name = "casting-helper"
    title = "Add Casting Helper to Kiosk Satellite"
    recover = staticmethod(clock.recover_if_lost)   # lost the clock: wait, then start again

    def run(self, ctx) -> None:
        zip_path = cached(ctx) or fetch(ctx)
        if zip_path is None:
            return                     # no download: nothing to install, nothing to say
        data = zip_path.read_bytes()
        version = manifest_of(data).get("version", "")
        try:
            clock.require_clock(ctx)
            secret = _secret(ctx)
            if not secret:
                raise RuntimeError("Kiosk Satellite's remote admin isn't set up")
            api = _Api(ctx, secret)
            mine = [p for p in api("listPlugins") or [] if p.get("id") == ID]
            if mine and mine[0].get("version") == version and mine[0].get("enabled"):
                ctx.ui.say(f"{NAME} {version} is already in Kiosk Satellite, and on.")
                return
            with ctx.ui.busy(f"Adding {NAME} {version} to Kiosk Satellite"):
                api("setPluginsEnabled", {"enabled": True})
                api("installPlugin", {"data": base64.b64encode(data).decode(), "trusted": True})
                api("enablePlugin", {"id": ID})
        except Exception as e:
            log.info("Casting Helper: install failed (%s: %s)", type(e).__name__, e)
            ctx.ui.say(f"{NAME} wasn't added ({_why(e)}). Setup goes on without it. To add it yourself: in "
                       f"Kiosk Satellite, Plugin Manager, Install from ZIP, with {zip_path}")
            return
        ctx.ui.ok(f"{NAME} {version} is in Kiosk Satellite, and on: cast to the clock from a phone or computer "
                  "on the same network.")


def _why(e: Exception) -> str:
    if isinstance(e, RuntimeError):
        return str(e)
    return "Kiosk Satellite's remote admin didn't answer"
