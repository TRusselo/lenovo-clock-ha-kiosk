"""A fixed address with lck wifi and setup. Over Wi-Fi: Google's Wi-Fi service joins first (it only does DHCP,
and a wrong password falls back by itself), then root writes the address into the saved network and the clock
restarts on it. Over USB, and in setup, it goes into the user data with the network."""
from __future__ import annotations

from pathlib import Path

import pytest

from lck import clock, userdata, wifi
from lck.runner import Result
from lck.staticip import StaticIp
from tests.fakes import FakeUI
from tests.test_wifi import IP, LOGCAT, TARGET, _Usb, _wifi_ctx

FIXED = StaticIp("10.0.0.50", 24, "10.0.0.254", ("10.0.0.254",))
STORE = userdata.with_network(userdata.EMPTY_STORE, "Home", "right-password")


def _switched(tmp_path, monkeypatch, restarted=True):
    c = _wifi_ctx(tmp_path, LOGCAT.replace("Home failed", "Home succeeded"))
    c.runner.responses[("-s", TARGET, "shell", "dumpsys")] = Result(0, "mWifiInfo SSID: Home, BSSID: x")
    c.runner.responses[("-s", TARGET, "shell", "su", "-c", f"'cat {wifi.STORE}'")] = Result(0, STORE)
    pushed = {}
    real = c.runner.run

    def run(args, **kw):
        args = [str(a) for a in args]
        if "push" in args and args[args.index("push") + 1].endswith(".xml"):
            pushed["xml"] = Path(args[args.index("push") + 1]).read_text(encoding="utf-8")
        return real(args, **kw)
    c.runner.run = run
    waits = []
    monkeypatch.setattr(clock, "boot_id", lambda ctx: "old")
    monkeypatch.setattr(clock, "wait_restarted", lambda ctx, old, **kw: (waits.append(ctx.state["ip"]), restarted)[1])
    monkeypatch.setattr(clock, "wifi_ip", lambda ctx: ctx.state["ip"])
    return c, pushed, waits


def test_over_wifi_a_fixed_address_is_written_with_root_then_the_clock_restarts_on_it(tmp_path, monkeypatch):
    c, pushed, waits = _switched(tmp_path, monkeypatch)
    assert wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None, static=FIXED) is True
    assert '<string name="LinkAddress">10.0.0.50</string>' in pushed["xml"]
    assert pushed["xml"].count("<Network>") == 1                       # the same network, now fixed
    root = [" ".join(call) for call in c.runner.calls if "su" in call and "cp " in " ".join(call)]
    assert root and "WifiConfigStore.xml" in root[0] and "restorecon" in root[0]
    # The restart is its own adb reboot: inside su, adb shell never returned (live, 2026-11-23).
    assert "reboot" not in root[0]
    assert any(call[1:] == ["-s", TARGET, "reboot"] for call in c.runner.calls)
    assert waits == ["10.0.0.50"] and c.state["ip"] == "10.0.0.50"    # waited for it at the new address
    assert any("10.0.0.50" in l and l.startswith("[ok]") for l in c.ui.lines)
    assert not any("right-password" in " ".join(call) for call in c.runner.calls)
    assert not list((tmp_path / "work").rglob("*.xml"))                 # the local copy is gone


def test_an_adb_reboot_that_hangs_still_waits_for_the_clock(tmp_path, monkeypatch):
    import subprocess
    c, pushed, waits = _switched(tmp_path, monkeypatch)
    c.runner.responses[("-s", TARGET, "reboot")] = subprocess.TimeoutExpired(["adb", "reboot"], 30)
    assert wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None, static=FIXED) is True
    assert waits == ["10.0.0.50"]


def test_a_wrong_password_writes_no_fixed_address(tmp_path, monkeypatch):
    c, pushed, waits = _switched(tmp_path, monkeypatch)
    c.runner.responses[("-s", TARGET, "logcat")] = Result(0, LOGCAT)    # "connecting to Home failed"
    assert wifi.over_wifi(c, "Home", "wrong-password", sleep=lambda s: None, static=FIXED) is False
    assert not pushed and not waits


def test_a_clock_that_doesnt_come_back_at_the_fixed_address_says_how_to_fix_it(tmp_path, monkeypatch):
    c, _, _ = _switched(tmp_path, monkeypatch, restarted=False)
    with pytest.raises(wifi.WifiFailed, match="USB"):
        wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None, static=FIXED)


def test_a_clock_that_left_for_another_network_cant_get_its_fixed_address_from_here(tmp_path, monkeypatch):
    monkeypatch.setattr(clock.time, "sleep", lambda s: None)
    c = _wifi_ctx(tmp_path, "")
    c.runner.responses[("-s", TARGET, "get-state")] = [Result(0, "device"), Result(1, "error: device offline")]
    assert wifi.over_wifi(c, "Home", "right-password", sleep=lambda s: None, static=FIXED) is True
    text = " ".join(c.ui.lines)
    assert "left this network" in text and "fixed address" in text


def test_over_usb_the_fixed_address_goes_in_with_the_network(tmp_path, monkeypatch):
    usb = _Usb(monkeypatch)
    got = {}
    monkeypatch.setattr(userdata, "set_wifi", lambda img, ssid, psk, keep_others=True, static=None: (
        usb.order.append("set"), got.update(static=static)))
    from tests.test_clock_tool import ctx_for
    c = ctx_for(tmp_path, ui=FakeUI(["write"]), e2fs=True)
    wifi.over_usb(c, "Home", "right-password", static=FIXED)
    assert got["static"] == FIXED
    assert any("10.0.0.50" in l for l in c.ui.lines)


def test_setup_saves_the_fixed_address_and_looks_for_the_clock_there(tmp_path, monkeypatch):
    from lck import images
    from tests.test_clock_tool import ctx_for
    got = {}
    monkeypatch.setattr(userdata, "add_wifi", lambda img, ssid, psk, keep_others=True, static=None:
                        got.update(static=static))
    c = ctx_for(tmp_path, ui=FakeUI(), e2fs=True)
    c.state.update(wifi_new=("Home", "right-password"), wifi_static=FIXED)
    images.save_typed_wifi(c, "userdata.img")
    assert got["static"] == FIXED and c.state["ip"] == "10.0.0.50"
