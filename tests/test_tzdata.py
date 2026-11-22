"""The time zone data and the time zone question (ported from the Smart Display tool)."""
from __future__ import annotations

import struct
from pathlib import Path

from lck import tzdata
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

ASSET = Path(__file__).parent.parent / "assets" / "tzdata" / "tzdata"
BASE = ("-s", "10.0.0.7:5555", "shell")
V = tzdata.version(ASSET.read_bytes())


def su(command):
    return BASE + ("su", "-c", f"'{command}'")


def test_bundled_tzdata_layout():
    b = ASSET.read_bytes()
    assert b[:6] == b"tzdata"
    index, data, zonetab = struct.unpack(">iii", b[12:24])
    assert index == 24 and (data - index) % 52 == 0
    names = [b[index + i * 52:index + i * 52 + 40].rstrip(b"\0").decode() for i in range((data - index) // 52)]
    assert "America/Edmonton" in names and "America/Regina" in names
    assert names == sorted(names)
    assert not b[zonetab:].startswith(b"#")
    assert V >= "2026f"


def test_zone_names_come_from_the_bundled_file():
    names = tzdata.zone_names()
    assert {"America/Edmonton", "Europe/Berlin", "America/Regina"} <= names and len(names) > 300


def _ctx(tmp_path, responses, answers=(), monkeypatch=None):
    runner = FakeRunner(responses)
    ui = FakeUI(list(answers))
    c = ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7")
    return c, runner, ui


def test_done_only_when_the_module_has_it_and_the_clock_reads_it(tmp_path):
    c, _, _ = _ctx(tmp_path, {su(f"head -c 12 {tzdata.MODULE_FILE}"): Result(0, f"tzdata{V}"),
                              su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, "tzdata2021a")})
    assert not tzdata.TzdataStep().done(c)
    c, _, _ = _ctx(tmp_path, {su(f"head -c 12 {tzdata.MODULE_FILE}"): Result(0, f"tzdata{V}"),
                              su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, f"tzdata{V}")})
    assert tzdata.TzdataStep().done(c)


def test_installs_a_magisk_module_and_never_writes_system(tmp_path, monkeypatch):
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    rebooted = []
    monkeypatch.setattr(tzdata.clock, "reboot", lambda ctx, why="": rebooted.append(why))
    c, runner, ui = _ctx(tmp_path, {su(f"head -c 12 {tzdata.MODULE_FILE}"): [Result(1, ""), Result(0, f"tzdata{V}")],
                                    su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, f"tzdata{V}")})
    tzdata.TzdataStep().run(c)
    pushes = [x[4:] for x in runner.calls if x[3] == "push"]
    assert [str(tzdata.bundled()), tzdata.TMP] in pushes
    install = [x[-1] for x in runner.calls if x[-3:-1] == ["su", "-c"] and "cp " in x[-1]]
    assert len(install) == 1
    assert f"chcon -R {tzdata.SYSTEM_FILE} {tzdata.MODULE}/system" in install[0]
    assert "mount" not in install[0] and " /system" not in install[0]
    assert (tmp_path / "work" / "lck-tzdata.prop").read_text().startswith("id=lck-tzdata\n")
    assert rebooted and any(f"updated to {V}" in l for l in ui.lines)


def test_old_data_still_in_use_after_the_restart_is_a_note_not_a_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    monkeypatch.setattr(tzdata.clock, "reboot", lambda ctx, why="": None)
    c, runner, ui = _ctx(tmp_path, {su(f"head -c 12 {tzdata.MODULE_FILE}"): Result(0, f"tzdata{V}"),
                                    su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, "tzdata2021a")})
    tzdata.TzdataStep().run(c)
    assert not [x for x in runner.calls if x[3] == "push"]
    assert any("still reads 2021a" in l for l in ui.lines)


def test_enter_keeps_the_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    c, runner, ui = _ctx(tmp_path, {BASE + ("getprop", tzdata.ZONE_PROP): Result(0, "America/Edmonton\n")}, [""])
    tzdata.TimezoneStep().run(c)
    assert any("is America/Edmonton" in l for l in ui.lines if l.startswith("[ask]"))
    assert not [x for x in runner.calls if "setprop" in x[-1]]


def test_a_typed_zone_is_checked_then_set(tmp_path, monkeypatch):
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    rebooted = []
    monkeypatch.setattr(tzdata.clock, "reboot", lambda ctx, why="": rebooted.append(why))
    c, runner, ui = _ctx(tmp_path, {BASE + ("getprop", tzdata.ZONE_PROP): Result(0, "America/Denver\n"),
                                    su(f"head -c 12 {tzdata.MODULE_FILE}"): Result(1, "")},
                         ["Alberta; reboot", "America/Regina"])
    tzdata.TimezoneStep().run(c)
    assert any("isn't a time zone name" in l for l in ui.lines)
    assert [x[-1] for x in runner.calls if "setprop" in x[-1]] == ["'setprop persist.sys.timezone America/Regina'"]
    assert not rebooted       # the tzdata step restarts the clock next


def test_with_the_data_already_there_the_zone_step_restarts_the_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    rebooted = []
    monkeypatch.setattr(tzdata.clock, "reboot", lambda ctx, why="": rebooted.append(why))
    c, runner, ui = _ctx(tmp_path, {BASE + ("getprop", tzdata.ZONE_PROP): Result(0, "America/Denver\n"),
                                    su(f"head -c 12 {tzdata.MODULE_FILE}"): Result(0, f"tzdata{V}"),
                                    su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, f"tzdata{V}")},
                         ["America/Edmonton"])
    tzdata.TimezoneStep().run(c)
    assert rebooted


def test_the_nul_after_the_version_isnt_part_of_it(tmp_path, monkeypatch):
    # head -c 12 of the real file is "tzdata2026f\0": the 12th byte is a NUL, and adb passes it through.
    monkeypatch.setattr(tzdata.clock, "require_clock", lambda ctx: None)
    monkeypatch.setattr(tzdata.clock, "reboot", lambda ctx, why="": None)
    head = ASSET.read_bytes()[:12].decode("ascii")
    assert head.endswith("\0")
    c, _, ui = _ctx(tmp_path, {su(f"head -c 12 {tzdata.MODULE_FILE}"): [Result(1, ""), Result(0, head)],
                               su(f"head -c 12 {tzdata.DEVICE_PATH}"): Result(0, head)})
    tzdata.TzdataStep().run(c)
    assert any(f"updated to {V}" in s for s in ui.lines)
    assert tzdata.TzdataStep().done(c)
