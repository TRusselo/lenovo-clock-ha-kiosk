"""lck wifi: a new network or password for a converted clock, over Wi-Fi or over USB."""
from __future__ import annotations

import shlex
import xml.dom.minidom as xml_dom
from pathlib import Path

import pytest

from lck import cli, clock, userdata, wifi
from lck.runner import Result
from tests.fakes import FakeRunner, FakeUI
from tests.test_clock_tool import ctx_for

IP = "10.0.0.9"
TARGET = f"{IP}:5555"


def _store(*networks: tuple[str, str]) -> str:
    xml = userdata.EMPTY_STORE
    for ssid, psk in networks:
        xml = userdata.with_network(xml, ssid, psk)
    return xml


# --- the saved network list ----------------------------------------------------

def test_a_network_of_the_same_name_is_replaced_and_the_others_stay():
    xml = _store(("Home", "old-password"), ("Shed", "shed-password"))
    text = userdata.with_network(xml, "Home", "new-password")
    assert text.count("<Network>") == 2
    assert "new-password" in text and "old-password" not in text and "shed-password" in text
    assert xml_dom.parseString(text.encode()).getElementsByTagName("NetworkList")


def test_the_new_network_can_replace_every_saved_one():
    # Live, 2026-11-22: with two saved networks in range, the clock booted onto the old one, even marked as not
    # the user's choice. Over USB the clock is off Wi-Fi anyway, so only the new one is kept.
    xml = _store(("Old", "old-password"), ("Shed", "shed-password"))
    text = userdata.with_network(xml, "New", "new-password", keep_others=False)
    assert text.count("<Network>") == 1 and "new-password" in text
    xml_dom.parseString(text.encode())


def test_names_with_symbols_are_matched_as_android_writes_them():
    xml = _store(('Bob\'s "Net" & Co', "old-password"))
    xml = userdata.with_network(xml, 'Bob\'s "Net" & Co', "new-password")
    assert xml.count("<Network>") == 1 and "new-password" in xml


# --- over Wi-Fi ------------------------------------------------------------------

def test_the_script_hands_the_name_and_password_over_exactly():
    script = wifi.connect_script("My Net", "pa'ss wo$rd")
    line = next(l for l in script.splitlines() if l.startswith("am "))
    words = shlex.split(line)
    assert words[words.index("ssid") + 1] == "My Net"
    assert words[words.index("passphrase") + 1] == "pa'ss wo$rd"
    assert f"rm -f {wifi.REMOTE}" in script        # the copy on the clock goes as soon as it's read


LOGCAT = """--------- beginning of main
  1763800000.100  1  1 I WifiSetupService: connecting to Home succeeded
  1763800100.500  1  1 I WifiSetupService: connecting to Shed failed
  1763800200.900  1  1 I WifiSetupService: connecting to Home failed
"""


def test_only_this_run_s_answer_for_this_network_counts():
    assert wifi.result(LOGCAT, since=1763800150.0, ssid="Home") == "failed"
    assert wifi.result(LOGCAT, since=1763800150.0, ssid="Shed") is None      # before this run
    assert wifi.result(LOGCAT, since=1763800000.0, ssid="Shed") == "failed"
    assert wifi.result(LOGCAT, since=1763800300.0, ssid="Home") is None


def _wifi_ctx(tmp_path, logcat: str, ui=None):
    runner = FakeRunner({
        ("connect", TARGET): Result(0, f"connected to {TARGET}"),
        ("-s", TARGET, "get-state"): Result(0, "device"),
        ("-s", TARGET, "shell", "date", "+%s"): Result(0, "1763800150\n"),
        ("-s", TARGET, "logcat"): Result(0, logcat),
        ("-s", TARGET, "shell", "dumpsys"): Result(0, "mWifiInfo SSID: Old, BSSID: x, Supplicant state: COMPLETED"),
    })
    return ctx_for(tmp_path, runner=runner, ui=ui or FakeUI(), ip=IP)


def test_a_wrong_password_says_so_and_that_the_clock_went_back(tmp_path):
    c = _wifi_ctx(tmp_path, LOGCAT)
    assert wifi.over_wifi(c, "Home", "wrong-password", sleep=lambda s: None) is False
    text = " ".join(c.ui.lines)
    assert "couldn't join Home" in text and "back on" in text
    # The name stays out of the log too, as setup's does: logs get pasted into issues.
    assert all(l in c.ui.unlogged for l in c.ui.lines if "Home" in l and not l.startswith("["))
    # The password reaches the clock only inside the pushed file: never on a command line, so never logged.
    assert not any("wrong-password" in " ".join(call) for call in c.runner.calls)
    assert any(call[1:4] == ["-s", TARGET, "push"] for call in c.runner.calls)
    assert not list((tmp_path / "work").rglob("*.sh"))               # the local copy is gone too


def test_a_switch_that_worked_says_where_the_clock_is(tmp_path):
    c = _wifi_ctx(tmp_path, LOGCAT.replace("Home failed", "Home succeeded"))
    c.runner.responses[("-s", TARGET, "shell", "dumpsys")] = Result(0, "mWifiInfo SSID: Home, BSSID: x")
    assert wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None) is True
    assert any("on Home" in l and IP in l for l in c.ui.lines)


def test_a_clock_that_left_this_network_is_told_apart_from_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(clock.time, "sleep", lambda s: None)     # adb connect's own pause between tries
    c = _wifi_ctx(tmp_path, "")
    c.runner.responses[("-s", TARGET, "get-state")] = [Result(0, "device"), Result(1, "error: device offline")]
    assert wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None) is True
    assert any("left this network" in l for l in c.ui.lines)


# --- over USB ----------------------------------------------------------------------

class _Usb:
    """BROM read, e2fsck and fastboot write, faked: records the order."""

    def __init__(self, monkeypatch, converted=True, fsck=0):
        self.order: list[str] = []
        from lck import flash, images, mtk

        def brom(ctx, commands, *, cwd, title, **kw):
            self.order.append("read")
            assert commands == ["ro 0 %d gpt.bin" % wifi.gpt.GPT_BYTES, "r userdata userdata.img"]
            (Path(cwd) / "gpt.bin").write_bytes(b"gpt")
            (Path(cwd) / "userdata.img").write_bytes(b"\0" * 8192)
        monkeypatch.setattr(mtk, "run_in_brom", brom)
        monkeypatch.setattr(wifi.gpt, "by_name", lambda head: {"userdata": type("P", (), {"size": 8192})()})
        monkeypatch.setattr(images, "e2fsck", lambda ctx, img, flag: (self.order.append("fsck" + flag),
                                                                      Result(fsck if flag == "-fy" else 0, ""))[1])
        monkeypatch.setattr(userdata, "exists", lambda img, path: converted)
        monkeypatch.setattr(userdata, "set_wifi", lambda img, ssid, psk, keep_others=True, static=None: self.order.append(
            f"set {ssid}" + ("" if keep_others else " only")))
        monkeypatch.setattr(images, "_sparse", lambda ctx, img, name, out, limit: [Path(out) / "userdata_chunk.0"])
        monkeypatch.setattr(flash, "fbmulti", lambda ctx, pairs, title: self.order.append(
            "write " + ",".join(p for _, p in pairs)))
        monkeypatch.setattr(flash, "restart_from_fastboot", lambda ctx: self.order.append("restart"))


def test_usb_reads_edits_checks_then_writes_only_the_user_data(tmp_path, monkeypatch):
    usb = _Usb(monkeypatch)
    c = ctx_for(tmp_path, ui=FakeUI(["write"]), e2fs=True)
    wifi.over_usb(c, "Home", "right-password")
    assert usb.order == ["read", "fsck-fy", "set Home only", "fsck-fn", "write userdata", "restart"]
    assert not (tmp_path / "work" / "wifi-clock").exists()          # the copy with the password is gone


def test_usb_writes_nothing_without_the_go_ahead(tmp_path, monkeypatch):
    usb = _Usb(monkeypatch)
    c = ctx_for(tmp_path, ui=FakeUI([""]), e2fs=True)
    with pytest.raises(wifi.WifiFailed, match="Nothing was written"):
        wifi.over_usb(c, "Home", "right-password")
    assert not any(o.startswith("write") for o in usb.order)


def test_usb_refuses_a_clock_that_was_never_converted(tmp_path, monkeypatch):
    usb = _Usb(monkeypatch, converted=False)
    c = ctx_for(tmp_path, ui=FakeUI(["write"]), e2fs=True)
    with pytest.raises(wifi.WifiFailed, match="Google Home app"):
        wifi.over_usb(c, "Home", "right-password")
    assert usb.order == ["read", "fsck-fy"]


def test_usb_stops_on_a_damaged_file_system(tmp_path, monkeypatch):
    usb = _Usb(monkeypatch, fsck=4)
    c = ctx_for(tmp_path, ui=FakeUI(["write"]), e2fs=True)
    with pytest.raises(wifi.WifiFailed, match="Nothing was written"):
        wifi.over_usb(c, "Home", "right-password")
    assert not any(o.startswith("write") for o in usb.order)


# --- the command ---------------------------------------------------------------------

def test_lck_wifi_is_a_command_and_needs_sudo_on_a_mac(monkeypatch):
    args = cli._parser().parse_args(["wifi", "--usb"])
    assert args.command == "wifi" and args.usb
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 501, raising=False)
    assert cli.needs_sudo(args)
