"""Casting Helper: downloaded with the other tools, installed into Kiosk Satellite after its setup pages. A
download that fails is silent and nothing is installed. Kiosk Satellite installs plugins only through its own
remote API: root reads the token secret it signs logins with, so no admin password is asked."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import zipfile

import pytest

from lck import plugin
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

IP = "10.0.0.9"
TARGET = f"{IP}:5555"
MANIFEST = {"id": "casting-helper", "name": "Casting Helper", "version": "0.3.1", "entryClass": "x.Y"}
PREFS = ('<?xml version=\'1.0\' encoding=\'utf-8\' standalone=\'yes\' ?>\n<map>\n'
         '<string name="flutter.ks.secret.remote_auth">c2VjcmV0LXNlY3JldA==</string>\n'
         '<boolean name="flutter.ks.remote.enabled" value="true" />\n</map>\n')


@pytest.fixture(autouse=True)
def _clock_answers(monkeypatch):
    monkeypatch.setattr(plugin.clock, "require_clock", lambda ctx: None)


def _zip(manifest=MANIFEST, jar=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("kiosk-satellite-plugin.json", json.dumps(manifest))
        if jar:
            z.writestr("plugin.jar", b"PK-jar")
    return buf.getvalue()


class _Http:
    """requests, faked: the download, and Kiosk Satellite's remote API."""

    def __init__(self, zip_bytes=None, fail=False, installed=(), api_down=False):
        self.zip_bytes, self.fail, self.installed, self.api_down = zip_bytes, fail, list(installed), api_down
        self.calls = []

    def get(self, url, timeout=None, **kw):
        self.calls.append(("GET", url, None, kw.get("headers")))
        if self.fail:
            raise OSError("no route to host")
        return _Resp(200, content=self.zip_bytes)

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json, headers))
        if self.api_down:
            raise OSError("connection refused")
        name = url.rsplit("/", 1)[-1]
        if name == "listPlugins":
            return _Resp(200, {"ok": True, "data": self.installed})
        if name == "installPlugin":
            self.installed = [dict(MANIFEST, enabled=False)]
        if name == "enablePlugin":
            self.installed = [dict(MANIFEST, enabled=True, running=True)]
        return _Resp(200, {"ok": True, "data": self.installed})


class _Resp:
    def __init__(self, status, body=None, content=None):
        self.status_code, self.body, self.content = status, body, content

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


def _ctx(tmp_path, http, prefs=PREFS):
    runner = FakeRunner({
        ("-s", TARGET, "shell", "su", "-c", f"'cat {plugin.PREFS}'"): Result(0, prefs),
    })
    return ctx_for(tmp_path, runner=runner, ui=FakeUI(), ip=IP, http=http)


# --- the download ----------------------------------------------------------------

def test_the_helper_downloads_with_the_tools(tmp_path):
    http = _Http(_zip())
    c = _ctx(tmp_path, http)
    got = plugin.fetch(c)
    assert got and got.read_bytes() == _zip()
    assert http.calls[0][1] == plugin.URL


@pytest.mark.parametrize("http", [_Http(fail=True), _Http(b"<html>not found</html>"),
                                  _Http(_zip(dict(MANIFEST, id="something-else"))), _Http(_zip(jar=False))])
def test_a_download_that_fails_is_silent(tmp_path, http):
    c = _ctx(tmp_path, http)
    assert plugin.fetch(c) is None
    assert not c.ui.lines                                              # nothing said, nothing stopped
    assert not plugin.cached(c)


def test_a_failed_download_keeps_no_older_copy_either(tmp_path):
    c = _ctx(tmp_path, _Http(_zip()))
    plugin.fetch(c)
    c.state["http"] = _Http(fail=True)
    assert plugin.fetch(c) is None and not plugin.cached(c)


# --- the token -------------------------------------------------------------------

def test_the_token_is_signed_as_kiosk_satellite_signs_its_logins():
    token = plugin.token("c2VjcmV0", now_ms=1_000, ttl_ms=600_000)
    payload, sig = token.split(".")
    assert json.loads(base64.urlsafe_b64decode(payload)) == {"exp": 601_000}
    want = base64.urlsafe_b64encode(hmac.new(b"c2VjcmV0", payload.encode(), hashlib.sha256).digest()).decode()
    assert sig == want


# --- the install -----------------------------------------------------------------

def test_the_helper_is_installed_trusted_and_switched_on(tmp_path):
    http = _Http(_zip())
    c = _ctx(tmp_path, http)
    plugin.fetch(c)
    plugin.PluginStep().run(c)
    posts = [(u.rsplit("/", 1)[-1], j) for m, u, j, h in http.calls if m == "POST"]
    names = [n for n, _ in posts]
    assert names == ["listPlugins", "setPluginsEnabled", "installPlugin", "enablePlugin"]
    install = dict(posts)["installPlugin"]
    assert install["trusted"] is True and base64.b64decode(install["data"]) == _zip()
    assert dict(posts)["enablePlugin"] == {"id": "casting-helper"}
    assert all(h["Authorization"].startswith("Bearer ") for m, u, j, h in http.calls if m == "POST")
    assert all(u.startswith(f"http://{IP}:2324/api/commands/") for m, u, j, h in http.calls if m == "POST")
    assert any(l.startswith("[ok]") and "Casting Helper 0.3.1" in l for l in c.ui.lines)
    # The secret and the token stay out of every command line, so out of the log.
    assert not any("c2VjcmV0" in " ".join(call) or "Bearer" in " ".join(call) for call in c.runner.calls)


def test_the_same_version_already_on_is_left_alone(tmp_path):
    http = _Http(_zip(), installed=[dict(MANIFEST, enabled=True, running=True)])
    c = _ctx(tmp_path, http)
    plugin.fetch(c)
    plugin.PluginStep().run(c)
    assert [u.rsplit("/", 1)[-1] for m, u, j, h in http.calls if m == "POST"] == ["listPlugins"]


def test_no_download_means_no_install_and_no_word(tmp_path):
    http = _Http(fail=True)
    c = _ctx(tmp_path, http)
    plugin.PluginStep().run(c)                    # tries the download once more, silently
    assert not [1 for m, *_ in http.calls if m == "POST"]
    assert not c.ui.lines


@pytest.mark.parametrize("http, prefs", [(_Http(_zip(), api_down=True), PREFS),
                                         (_Http(_zip()), "<map></map>")])
def test_an_install_that_fails_says_so_in_a_line_and_setup_goes_on(tmp_path, http, prefs):
    c = _ctx(tmp_path, http, prefs)
    plugin.fetch(c)
    plugin.PluginStep().run(c)                    # no exception: setup carries on
    said = [l for l in c.ui.lines if "Casting Helper" in l]
    assert len(said) == 1 and "Install from ZIP" in said[0] and str(plugin.cached(c)) in said[0]


def test_the_step_comes_after_kiosk_satellite_is_set_up():
    from lck import plan
    names = [s.name for s in plan.setup_steps()]
    assert names.index("casting-helper") == names.index("kiosk-settings") + 1
    assert names[-1] == "verify"
