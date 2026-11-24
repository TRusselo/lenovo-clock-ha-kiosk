"""Kiosk Satellite's mic gain: the clock's mic chip runs at 0 dB, so the setup sets Kiosk Satellite's own gain
to +24 dB. At its default (+8) people had to stand a foot away and raise their voice for the wake word."""
from __future__ import annotations

from lck import kiosk
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

BASE = ("-s", "10.0.0.7:5555")
HOME = Result(0, "priority=0 preferredOrder=0\nme.jxl.kiosk_satellite/.MainActivity\n")
NOT_HOME = Result(0, "com.android.iotlauncher/.DefaultIoTLauncher\n")
SECRET = "signing-secret"
PASSWORD = "hunter2-admin"


def _prefs(gain: int | None) -> Result:
    line = f'<long name="flutter.ks.audio.mic_gain_db" value="{gain}" />' if gain is not None else ""
    return Result(0, '<map>\n<string name="flutter.ks.remote.password">' + PASSWORD + "</string>\n"
                     '<string name="flutter.ks.secret.remote_auth">' + SECRET + "</string>\n"
                     '<string name="flutter.ks.ha.token">tok</string>\n' + line + "\n</map>\n")


class FakeHttp:
    def __init__(self, rejected=()):
        self.posts, self.patches, self.rejected = [], [], list(rejected)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(200, {"token": "session"})

    def patch(self, url, json=None, headers=None, timeout=None):
        self.patches.append((url, json, headers))
        return _Resp(200, {"rejected": self.rejected})


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _runner(home, prefs):
    r = FakeRunner()
    r.responses.update({
        BASE + ("get-state",): Result(0, "device\n"),
        BASE + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
        BASE + ("shell", "cmd", "package", "resolve-activity"): home,
        BASE + ("shell", "su", "-c"): prefs})
    return r


def test_the_step_isnt_done_until_the_mic_gain_is_24_db(tmp_path):
    step = kiosk.SettingsStep()
    assert not step.done(ctx_for(tmp_path, runner=_runner(HOME, _prefs(8)), ip="10.0.0.7"))
    assert not step.done(ctx_for(tmp_path, runner=_runner(HOME, _prefs(None)), ip="10.0.0.7"))
    assert step.done(ctx_for(tmp_path, runner=_runner(HOME, _prefs(24)), ip="10.0.0.7"))
    assert not step.done(ctx_for(tmp_path, runner=_runner(NOT_HOME, _prefs(24)), ip="10.0.0.7"))


def test_a_set_up_clock_just_gets_the_mic_gain(tmp_path):
    # A clock converted before this change: Kiosk Satellite is already home, so no setup pages.
    from lck import plugin
    runner = _runner(HOME, [_prefs(8), _prefs(24)])
    http, ui = FakeHttp(), FakeUI()
    kiosk.SettingsStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7", http=http))
    assert http.posts == []                      # no sign-in with the admin password
    (url, body, headers), = http.patches
    assert (url, body) == ("http://10.0.0.7:2324/api/settings", {"audio.mic_gain_db": 24})
    payload, sig = headers["Authorization"].removeprefix("Bearer ").split(".")
    import base64, hashlib, hmac
    assert base64.urlsafe_b64decode(sig) == hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    assert not any(l.startswith("[physical]") for l in ui.lines)


def test_secrets_never_reach_the_log_or_screen(tmp_path):
    runner = _runner(HOME, [_prefs(8), _prefs(24)])
    ui = FakeUI()
    kiosk.SettingsStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7", http=FakeHttp()))
    for secret in (SECRET, PASSWORD):
        assert not any(secret in " ".join(c) for c in runner.calls)   # not on a command line
        assert not any(secret in l for l in ui.lines)
    reads = [i for i, c in enumerate(runner.calls) if "su" in c and any(kiosk.PREFS in a for a in c)]
    assert reads and all(runner.log_flags[i] is False for i in reads)  # its output isn't logged


def test_a_rejected_gain_stops_the_step(tmp_path):
    import pytest
    runner = _runner(HOME, [_prefs(8), _prefs(8)])
    with pytest.raises(kiosk.KioskError, match="audio.mic_gain_db"):
        kiosk.SettingsStep().run(ctx_for(tmp_path, runner=runner, ip="10.0.0.7",
                                         http=FakeHttp(rejected=["audio.mic_gain_db"])))
