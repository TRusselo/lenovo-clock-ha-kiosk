"""The volume buttons: a Magisk module that runs the VolKeys helper (from the Smart Display tool)."""
from __future__ import annotations

import hashlib
import zipfile

import pytest

from lck import volkeys
from lck.clock import SetupFailed
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

BASE = ("-s", "10.0.0.7:5555", "shell")
MD5 = hashlib.md5(volkeys.bundled().read_bytes()).hexdigest()
PS = BASE + ("ps", "-A", "-o", "ARGS")
RUNNING = Result(0, "ARGS\napp_process /system/bin ks.VolKeys /sys/class/gpio/gpio429/value\n")
VOL = BASE + ("settings", "get", "system", "volume_music_speaker")


def su(command):
    return BASE + ("su", "-c", f"'{command}'")


def _ctx(tmp_path, responses, monkeypatch):
    monkeypatch.setattr(volkeys.clock, "require_clock", lambda ctx: None)
    runner = FakeRunner(responses)
    ui = FakeUI()
    return ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"), runner, ui


def test_the_helper_jar_is_in_the_repo_and_is_dex():
    with zipfile.ZipFile(volkeys.bundled()) as z:
        assert "classes.dex" in z.namelist()


def test_the_boot_script_has_lf_endings_and_both_pins():
    assert "\r" not in volkeys.SERVICE
    assert volkeys.SERVICE.startswith("#!/system/bin/sh\n")
    for pin in volkeys.PINS:
        assert pin in volkeys.SERVICE
    assert "app_process /system/bin ks.VolKeys" in volkeys.SERVICE


def test_done_only_with_this_jar_installed_and_the_helper_running(tmp_path, monkeypatch):
    c, _, _ = _ctx(tmp_path, {su(f"md5sum {volkeys.JAR}"): Result(0, f"{MD5}  {volkeys.JAR}\n"), PS: RUNNING},
                   monkeypatch)
    assert volkeys.VolumeKeysStep().done(c)
    c, _, _ = _ctx(tmp_path, {su(f"md5sum {volkeys.JAR}"): Result(0, f"{'0' * 32}  {volkeys.JAR}\n"), PS: RUNNING},
                   monkeypatch)
    assert not volkeys.VolumeKeysStep().done(c)
    c, _, _ = _ctx(tmp_path, {su(f"md5sum {volkeys.JAR}"): Result(0, f"{MD5}  {volkeys.JAR}\n"),
                              PS: Result(0, "ARGS\n")}, monkeypatch)
    assert not volkeys.VolumeKeysStep().done(c)


def test_installs_a_magisk_module_starts_it_and_checks_both_buttons(tmp_path, monkeypatch):
    c, runner, ui = _ctx(tmp_path, {
        su(f"md5sum {volkeys.JAR}"): [Result(1, ""), Result(0, f"{MD5}  {volkeys.JAR}\n")],
        PS: [Result(0, "ARGS\n"), RUNNING],
        VOL: [Result(0, "50"), Result(0, "50"), Result(0, "55"), Result(0, "55"), Result(0, "50")],
    }, monkeypatch)
    volkeys.VolumeKeysStep().run(c, sleep=lambda s: None)
    pushes = [x[4:] for x in runner.calls if x[3] == "push"]
    assert [str(volkeys.bundled()), volkeys.TMP + ".jar"] in pushes
    sent = (tmp_path / "work" / "lck-volkeys" / "service.sh").read_bytes()
    assert b"\r" not in sent and sent.decode() == volkeys.SERVICE
    root = [x[-1] for x in runner.calls if x[-3:-1] == ["su", "-c"]]
    install = [r for r in root if "cp " in r]
    assert len(install) == 1 and volkeys.MODULE in install[0] and " /system" not in install[0]
    assert any("setsid" in r and "service.sh" in r for r in root)        # started now, no restart
    assert not [x for x in runner.calls if "reboot" in x]
    assert any("Press VOLUME UP" in l for l in ui.lines)
    assert any("Press VOLUME DOWN" in l for l in ui.lines)
    assert "Volume buttons work." in ui.lines


def test_a_helper_that_never_starts_stops_the_step(tmp_path, monkeypatch):
    c, _, _ = _ctx(tmp_path, {su(f"md5sum {volkeys.JAR}"): Result(0, f"{MD5}  {volkeys.JAR}\n"),
                              PS: Result(0, "ARGS\n")}, monkeypatch)
    with pytest.raises(SetupFailed, match="volume button helper"):
        volkeys.VolumeKeysStep().run(c, sleep=lambda s: None)


def test_it_comes_after_root_and_before_the_restarts(tmp_path):
    from lck.plan import setup_steps
    names = [s.name for s in setup_steps()]
    assert names.index("root") < names.index("volume-keys") < names.index("tzdata") < names.index("degoogle")
