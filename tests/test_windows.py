"""Windows' USB driver for BROM mode (issue 1), without Windows: the platform is passed in."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lck import mtk, windrivers
from lck.modes import Mode
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import FakeProc, SpawnRunner, ctx_for


class Logger:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(fmt % args if args else fmt)


# --- the UsbDk check ---------------------------------------------------------

def test_no_check_off_windows(tmp_path):
    runner = FakeRunner()
    windrivers.check_brom_driver(ctx_for(tmp_path, runner=runner), "linux", dll=tmp_path / "none.dll")
    assert runner.calls == []


def test_usbdk_found_by_its_helper_library(tmp_path):
    dll = tmp_path / "UsbDkHelper.dll"
    dll.write_bytes(b"x")
    log = Logger()
    windrivers.check_brom_driver(ctx_for(tmp_path, log=log), "win32", dll=dll)
    assert "UsbDk: found" in log.lines


def test_usbdk_found_by_its_service(tmp_path):
    runner = FakeRunner({("query", "UsbDk"): Result(0, "SERVICE_NAME: UsbDk\n  STATE : 4  RUNNING\n")})
    windrivers.check_brom_driver(ctx_for(tmp_path, runner=runner), "win32", dll=tmp_path / "none.dll")
    assert runner.calls == [["sc", "query", "UsbDk"]]


def test_no_usbdk_stops_before_the_clock_is_asked_for(tmp_path):
    runner = FakeRunner({("query", "UsbDk"): Result(1060, "The specified service does not exist")})
    log = Logger()
    with pytest.raises(windrivers.UsbDkMissing) as e:
        windrivers.check_brom_driver(ctx_for(tmp_path, runner=runner, log=log), "win32", dll=tmp_path / "none.dll")
    assert "docs/drivers.md" in str(e.value) and "Nothing was done to the clock" in str(e.value)
    assert "UsbDk: NOT found" in log.lines


def test_the_check_can_be_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv(windrivers.SKIP_ENV, "1")
    runner = FakeRunner({("query", "UsbDk"): Result(1060, "")})
    windrivers.check_brom_driver(ctx_for(tmp_path, runner=runner), "win32", dll=tmp_path / "none.dll")
    assert runner.calls == []


def test_brom_jobs_check_for_usbdk_first(tmp_path, monkeypatch):
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    monkeypatch.setattr(windrivers, "_helper_dll", lambda: tmp_path / "none.dll")
    runner = SpawnRunner([])
    c = ctx_for(tmp_path, runner=runner, platform="win32", detect=lambda: [])
    with pytest.raises(windrivers.UsbDkMissing):
        mtk.run_in_brom(c, ["rf a.bin"], cwd=tmp_path, title="t", sleep=lambda s: None)
    assert runner.spawned == []


def test_mediatek_devices_from_pnputil():
    out = textwrap.dedent("""\
        Instance ID:                USB\\VID_0E8D&PID_0003\\5&1A2B
        Device Description:         USB Serial Device
        Driver Name:                usbser.inf
        Status:                     Disconnected

        Instance ID:                USB\\VID_046D&PID_C52B\\6&2
        Device Description:         USB Receiver
        """).replace("\n", "\r\n")
    blocks = windrivers.mediatek_devices(out)
    assert len(blocks) == 1 and "usbser.inf" in blocks[0]


# --- mtkclient's own word ----------------------------------------------------

def test_mtkclient_runs_through_the_shim(tmp_path, monkeypatch):
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    runner = SpawnRunner([FakeProc([], 0, "Dumped x", None)])
    mtk.run_in_brom(ctx_for(tmp_path, runner=runner, detect=lambda: []), ["rf a.bin"], cwd=tmp_path, title="t",
                    sleep=lambda s: None)
    assert runner.spawned[0][:4] == ["python", "-c", mtk.MTK_SHIM, "mtk.py"]


def _fake_mtkclient(tmp_path: Path, connect_body: str, handshake_ok: bool) -> Path:
    lib = tmp_path / "mtkclient" / "Library"
    (lib / "Connection").mkdir(parents=True)
    for d in (tmp_path / "mtkclient", lib, lib / "Connection"):
        (d / "__init__.py").write_text("")
    (lib / "Connection" / "usblib.py").write_text(textwrap.dedent(f"""\
        class UsbClass:
            def __init__(self):
                self.backend = None
            def connect(self):
                {connect_body}
        """))
    (lib / "Port.py").write_text(textwrap.dedent(f"""\
        from mtkclient.Library.Connection.usblib import UsbClass
        class Port:
            def handshake(self):
                u = UsbClass()
                for _ in range(3):
                    try:
                        u.connect()
                    except Exception:
                        pass        # as mtkclient's handshake: at debug level only
                return {handshake_ok}
        """))
    script = tmp_path / "mtk.py"
    script.write_text("import sys\nfrom mtkclient.Library.Port import Port\nprint('args', sys.argv[1:])\n"
                      "print('handshake', Port().handshake())\n")
    return script


def _run_shim(script: Path, *args) -> str:
    r = subprocess.run([sys.executable, "-c", mtk.MTK_SHIM, str(script), *args], capture_output=True, text=True,
                       cwd=script.parent.parent, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_shim_says_why_usb_wouldnt_open_once(tmp_path):
    script = _fake_mtkclient(tmp_path / "m", 'raise NotImplementedError("Operation not supported or unimplemented '
                                             'on this platform")', False)
    out = _run_shim(script, "rf", "a.bin")
    assert "args ['rf', 'a.bin']" in out
    assert out.count(mtk.OPEN_FAILED) == 1
    assert mtk.no_driver(out) and not mtk.connected(out)


def test_the_shim_says_when_the_handshake_worked(tmp_path):
    script = _fake_mtkclient(tmp_path / "m", "return True", True)
    out = _run_shim(script)
    assert mtk.connected(out) and not mtk.open_failures(out)


def _follow(tmp_path, monkeypatch, timeline, output, platform="win32", code=1):
    now = [0.0]
    monkeypatch.setattr(mtk.time, "monotonic", lambda: now[0])

    def sleep(s):
        now[0] += s
    seq = list(timeline)

    def detect():
        while len(seq) > 1 and seq[1][0] <= now[0]:
            seq.pop(0)
        m = seq[0][1]
        return [] if m == Mode.NONE else [m]
    log = Logger()
    runner = SpawnRunner([])
    runner.responses = {("/enum-devices", "/ids"): Result(0, "Instance ID: USB\\VID_0E8D&PID_0003\\1\n"
                                                             "Driver Name: usbser.inf\n")}
    ui = FakeUI()
    out = tmp_path / "out.txt"
    out.write_text(output)
    proc = FakeProc([True] * int(timeline[-1][0] * 4 + 4), code, output, None)
    c = ctx_for(tmp_path, runner=runner, ui=ui, detect=detect, platform=platform, log=log)
    return c, ui, log, proc, out, sleep


def test_no_driver_on_windows_stops_with_the_usbdk_steps(tmp_path, monkeypatch):
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch, [(0, Mode.NONE), (5, Mode.BROM), (60, Mode.NONE)],
                                           mtk.OPEN_FAILED + "NotImplementedError: Operation not supported\n")
    with pytest.raises(windrivers.UsbDkMissing) as e:
        mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    assert "UsbDk driver isn't working" in str(e.value)
    assert any("usbser.inf" in l for l in log.lines)


def test_brom_without_a_handshake_on_windows_blames_the_driver_not_the_buttons(tmp_path, monkeypatch):
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch,
                                           [(0, Mode.NONE), (5, Mode.BROM), (8, Mode.NONE), (60, Mode.NONE)], "")
    try:
        mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    except mtk.MtkFailed:
        pass
    assert any("hasn't connected" in l for l in ui.lines)
    assert not any("Nothing yet" in l for l in ui.lines)


def test_the_same_off_windows_still_gets_the_nothing_hint(tmp_path, monkeypatch):
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch,
                                           [(0, Mode.NONE), (5, Mode.BROM), (8, Mode.NONE), (60, Mode.NONE)], "",
                                           platform="linux")
    try:
        mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    except mtk.MtkFailed:
        pass
    assert any("Nothing yet" in l for l in ui.lines)
    assert not any("hasn't connected" in l for l in ui.lines)


def test_mtkclients_handshake_counts_as_connected_when_usb_shows_nothing(tmp_path, monkeypatch):
    # UsbDk can hide the clock from the watcher while mtkclient holds it: no unplug advice mid-backup.
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch, [(0, Mode.NONE), (5, Mode.BROM), (6, Mode.NONE),
                                                                    (120, Mode.NONE)],
                                           mtk.CONNECTED + "\nProgress: 50.0% Read\nDumped x\n", code=0)
    text = mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    assert "Dumped" in text
    assert any("Connected." in l for l in ui.lines)
    assert not any("Nothing yet" in l or "hasn't connected" in l for l in ui.lines)


# --- the log ---------------------------------------------------------------

def test_the_log_keeps_every_mtkclient_line_but_one_progress_bar():
    from lck.runner import condensed
    text = ("Preloader - Status: Waiting for PreLoader VCOM\n" + "".join(f"Progress: {i}.0% Read\r" for i in range(500))
            + "\nDumped partition boot_a\n[lck] USB open failed: NotImplementedError\n")
    out = condensed(text)
    assert "Waiting for PreLoader VCOM" in out and "USB open failed" in out and "Dumped" in out
    assert out.count("Progress:") == 1 and "499.0%" in out


def test_a_very_long_output_keeps_its_start_and_end():
    from lck.runner import condensed
    out = condensed("first line\n" + "x" * 100 + "\n" * 2000 + "last line\n", limit=1000)
    assert out.startswith("first line") and out.endswith("last line") and "[... cut ...]" in out


def test_every_run_logs_the_version_and_os():
    from lck import __version__
    from lck.cli import about_this_computer
    line = about_this_computer()
    assert f"lck {__version__}" in line and "Python" in line


# --- the high-speed reconnect (issue 5 on Windows, issue 4 on Linux) ---------

STALLED = (mtk.CONNECTED + "\nDAXFlash - EMMC USER Size:  0x1d2000000\n"
           "DAXFlash - Reconnecting to stage2 with higher speed\n")


def test_a_stalled_reconnect_on_windows_stops_with_the_zadig_steps(tmp_path, monkeypatch):
    # Issue 5: the clock came back as 0E8D:2001 with no Windows driver, and the backup sat at 0% for 7 minutes.
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch, [(0, Mode.BROM), (400, Mode.NONE)], STALLED)
    with pytest.raises(mtk.ReconnectStalled) as e:
        mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    msg = str(e.value)
    assert "Zadig" in msg and "0E8D 2001" in msg and "WinUSB" in msg
    assert msg.index("Zadig") < msg.index("unplug")       # the driver first: the device only exists now
    assert any("usbser.inf" in l for l in log.lines)      # what Windows knows goes in the log


def test_a_stalled_reconnect_off_windows_suggests_a_usb_2_hub(tmp_path, monkeypatch):
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch, [(0, Mode.BROM), (400, Mode.NONE)], STALLED,
                                           platform="linux")
    with pytest.raises(mtk.ReconnectStalled) as e:
        mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    assert "USB 2.0 hub" in str(e.value)
    assert "Zadig" not in str(e.value)


def test_a_reconnect_that_comes_back_carries_on(tmp_path, monkeypatch):
    c, ui, log, proc, out, sleep = _follow(tmp_path, monkeypatch, [(0, Mode.BROM), (400, Mode.NONE)],
                                           STALLED + "DAXFlash - Connected to stage2 with higher speed\n"
                                           "Progress: 50.0% Read\nDumped x\n", code=0)
    assert "Dumped" in mtk._follow(c, proc, out, "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)


def test_the_stall_isnt_retried(tmp_path, monkeypatch):
    # Another try can't help: the clock is held at 0E8D:2001 until the driver is there and it's unplugged.
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    monkeypatch.setattr(mtk, "release_stale", lambda *a, **k: False)
    monkeypatch.setattr(windrivers, "check_brom_driver", lambda *a, **k: None)
    now = [0.0]
    monkeypatch.setattr(mtk.time, "monotonic", lambda: now[0])
    runner = SpawnRunner([FakeProc([True] * 2000, 1, STALLED, None), FakeProc([], 0, "Dumped x", None)])
    c = ctx_for(tmp_path, runner=runner, ui=FakeUI(), detect=lambda: [Mode.BROM], platform="win32", log=Logger())
    with pytest.raises(mtk.ReconnectStalled):
        mtk.run_in_brom(c, ["rf a.bin"], cwd=tmp_path, title="t", sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert len(runner.spawned) == 1
