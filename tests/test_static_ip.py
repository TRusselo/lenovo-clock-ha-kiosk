"""Optional static address for the clock's Wi-Fi: asked after the Wi-Fi password, in setup and lck wifi."""
from __future__ import annotations

import xml.dom.minidom as xml_dom

import pytest

from lck import staticip, system, userdata
from lck.staticip import StaticIp
from tests.fakes import FakeUI
from tests.test_clock_tool import ctx_for

HOME = StaticIp("10.1.1.40", 24, "10.1.1.254", ("10.1.1.254",))


# --- the saved network ------------------------------------------------------------

def test_a_static_address_is_saved_as_android_writes_it():
    xml = userdata.with_network(userdata.EMPTY_STORE, "Home", "a-long-password",
                                static=StaticIp("10.1.1.40", 24, "10.1.1.254", ("1.1.1.1", "8.8.8.8")))
    xml_dom.parseString(xml)                                     # still well-formed
    block = xml[xml.index("<IpConfiguration>"):xml.index("</IpConfiguration>")]
    assert '<string name="IpAssignment">STATIC</string>' in block
    assert '<string name="LinkAddress">10.1.1.40</string>' in block
    assert '<int name="LinkPrefixLength" value="24" />' in block
    assert '<string name="GatewayAddress">10.1.1.254</string>' in block
    assert ('<string-array name="DNSServers" num="2">\n<item value="1.1.1.1" />\n<item value="8.8.8.8" />\n'
            '</string-array>') in block
    assert '<string name="ProxySettings">NONE</string>' in block


def test_without_a_static_address_the_network_stays_automatic():
    xml = userdata.with_network(userdata.EMPTY_STORE, "Home", "a-long-password")
    assert '<string name="IpAssignment">DHCP</string>' in xml
    assert "LinkAddress" not in xml


def test_the_static_address_goes_only_on_the_new_network():
    xml = userdata.with_network(userdata.EMPTY_STORE, "Shed", "shed-password")
    xml = userdata.with_network(xml, "Home", "a-long-password", static=HOME)
    shed = xml[xml.index("Shed"):xml.index("Home")]
    assert "DHCP" in shed and "STATIC" not in shed
    assert xml.count("STATIC") == 1


# --- checking what was typed ------------------------------------------------------

@pytest.mark.parametrize("text, prefix", [("24", 24), ("/24", 24), ("255.255.255.0", 24), ("255.255.0.0", 16),
                                          (" 22 ", 22)])
def test_a_prefix_or_a_mask_is_understood(text, prefix):
    assert staticip.parse_prefix(text) == prefix


@pytest.mark.parametrize("text", ["33", "0", "255.0.255.0", "abc", "255.255.255.256"])
def test_a_wrong_prefix_or_mask_is_refused(text):
    with pytest.raises(ValueError):
        staticip.parse_prefix(text)


def test_a_router_at_254_is_inside_a_24():
    staticip.check(HOME)


@pytest.mark.parametrize("bad, words", [
    (StaticIp("10.1.1.40", 24, "10.1.2.1", ("10.1.1.254",)), "gateway"),
    (StaticIp("10.1.1.0", 24, "10.1.1.254", ("10.1.1.254",)), "network"),
    (StaticIp("10.1.1.255", 24, "10.1.1.254", ("10.1.1.254",)), "broadcast"),
    (StaticIp("10.1.1.254", 24, "10.1.1.254", ("10.1.1.254",)), "gateway"),
    (StaticIp("10.1.1.40", 24, "10.1.1.254", ()), "DNS"),
])
def test_addresses_that_cant_work_are_refused(bad, words):
    with pytest.raises(ValueError, match=words):
        staticip.check(bad)


# --- the default gateway ----------------------------------------------------------

def test_the_default_gateway_is_this_computers_when_on_the_same_network():
    assert staticip.default_gateway("10.1.1.40", 24, pc_gateway="10.1.1.254") == "10.1.1.254"


def test_otherwise_the_default_gateway_is_the_first_address():
    assert staticip.default_gateway("10.0.5.20", 24, pc_gateway="10.1.1.254") == "10.0.5.1"
    assert staticip.default_gateway("10.0.5.20", 24, pc_gateway=None) == "10.0.5.1"


def test_linux_routes_give_the_gateway():
    table = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
             "enp5s0\t0001010A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
             "enp5s0\t00000000\tFE01010A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
    assert staticip.gateway_from_proc_route(table) == "10.1.1.254"


def test_mac_and_windows_routes_give_the_gateway():
    assert staticip.gateway_from_route_get("   route to: default\n    gateway: 10.1.1.254\n") == "10.1.1.254"
    windows = ("Network Destination        Netmask          Gateway       Interface  Metric\n"
               "          0.0.0.0          0.0.0.0    10.1.1.254    10.1.1.10     25\n")
    assert staticip.gateway_from_route_print(windows) == "10.1.1.254"


# --- the questions ----------------------------------------------------------------

def _ask(tmp_path, answers, monkeypatch, pc="10.1.1.254"):
    monkeypatch.setattr(staticip, "pc_gateway", lambda: pc)
    ui = FakeUI(answers)
    return system.ask_static_ip(ctx_for(tmp_path, ui=ui)), ui


def test_enter_keeps_the_address_automatic(tmp_path, monkeypatch):
    got, _ = _ask(tmp_path, [""], monkeypatch)
    assert got is None


def test_enter_takes_each_default(tmp_path, monkeypatch):
    got, _ = _ask(tmp_path, ["10.1.1.40", "", "", ""], monkeypatch)
    assert got == HOME


def test_the_gateway_dns_and_mask_can_each_be_typed(tmp_path, monkeypatch):
    got, _ = _ask(tmp_path, ["10.0.5.20", "255.255.0.0", "10.0.0.1", "1.1.1.1, 8.8.8.8"], monkeypatch)
    assert got == StaticIp("10.0.5.20", 16, "10.0.0.1", ("1.1.1.1", "8.8.8.8"))


def test_a_mistyped_answer_asks_that_question_again(tmp_path, monkeypatch):
    got, ui = _ask(tmp_path, ["10.1.1.400", "10.1.1.40", "abc", "", "10.1.2.1", "", ""], monkeypatch)
    assert got == HOME
    assert sum("isn't" in line or "outside" in line for line in ui.lines[1:]) == 3   # [0]: the intro
