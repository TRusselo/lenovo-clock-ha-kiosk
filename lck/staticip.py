"""An optional static address for the clock's Wi-Fi, saved in Android's WifiConfigStore.xml as Android 8.1
writes one (IpConfigurationXmlUtil): IpAssignment STATIC, LinkAddress, LinkPrefixLength, GatewayAddress and a
DNSServers string array. Asked after the Wi-Fi password, in setup and lck wifi; Enter keeps it automatic."""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StaticIp:
    address: str
    prefix: int
    gateway: str
    dns: tuple[str, ...]


def ip(text: str) -> str:
    """A plain IPv4 address, or ValueError."""
    text = text.strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text):
        raise ValueError(f'"{text}" isn\'t an address. It looks like four numbers with dots between them.')
    try:
        return str(ipaddress.IPv4Address(text))
    except ipaddress.AddressValueError:
        raise ValueError(f'"{text}" isn\'t an address: each number is 0 to 255.') from None


def parse_prefix(text: str) -> int:
    """24, /24 or 255.255.255.0."""
    text = text.strip().lstrip("/")
    if text.isdigit():
        n = int(text)
        if 1 <= n <= 30:
            return n
    elif re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text):
        try:
            return ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen
        except ValueError:
            pass
    raise ValueError(f'"{text}" isn\'t a subnet mask. It\'s usually 255.255.255.0 (or 24).')


def network(address: str, prefix: int) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)


def check(s: StaticIp) -> None:
    """ValueError, in plain words, for an address the clock couldn't use."""
    net = network(s.address, s.prefix)
    addr = ipaddress.IPv4Address(s.address)
    if addr == net.network_address:
        raise ValueError(f"{s.address} is the network's own address, not one a device can have.")
    if addr == net.broadcast_address:
        raise ValueError(f"{s.address} is the network's broadcast address, not one a device can have.")
    if ipaddress.IPv4Address(s.gateway) not in net:
        raise ValueError(f"The gateway {s.gateway} is outside {net}, the clock's network.")
    if s.gateway == s.address:
        raise ValueError(f"{s.address} is the gateway's (your router's) own address.")
    if not s.dns:
        raise ValueError("At least one DNS server is needed.")


def default_gateway(address: str, prefix: int, pc_gateway: str | None) -> str:
    """This computer's own gateway when it's on the clock's network, else the network's first address."""
    net = network(address, prefix)
    if pc_gateway and ipaddress.IPv4Address(pc_gateway) in net and pc_gateway != address:
        return pc_gateway
    return str(net.network_address + 1)


def gateway_from_proc_route(text: str) -> str | None:
    """Linux /proc/net/route: the default route's gateway (little-endian hex)."""
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) > 7 and f[1] == "00000000" and f[7] == "00000000" and f[2] != "00000000":
            return str(ipaddress.IPv4Address(bytes.fromhex(f[2])[::-1]))
    return None


def gateway_from_route_get(text: str) -> str | None:
    """macOS `route -n get default`."""
    m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", text)
    return m.group(1) if m else None


def gateway_from_route_print(text: str) -> str | None:
    """Windows `route print -4 0.0.0.0`."""
    m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s", text, re.M)
    return m.group(1) if m else None


def pc_gateway() -> str | None:
    """This computer's default gateway, or None. Only a default answer: never needed."""
    try:
        if sys.platform.startswith("linux"):
            return gateway_from_proc_route(Path("/proc/net/route").read_text())
        if sys.platform == "darwin":
            return gateway_from_route_get(subprocess.run(["route", "-n", "get", "default"], capture_output=True,
                                                         text=True, timeout=10).stdout)
        if sys.platform == "win32":
            return gateway_from_route_print(subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True,
                                                           text=True, timeout=10).stdout)
    except Exception:
        pass
    return None


def ip_block(s: StaticIp | None) -> str:
    """The <IpConfiguration> block of a saved network."""
    if s is None:
        return ('<IpConfiguration>\n<string name="IpAssignment">DHCP</string>\n'
                '<string name="ProxySettings">NONE</string>\n</IpConfiguration>\n')
    dns = "".join(f'<item value="{d}" />\n' for d in s.dns)
    return ('<IpConfiguration>\n<string name="IpAssignment">STATIC</string>\n'
            f'<string name="LinkAddress">{s.address}</string>\n'
            f'<int name="LinkPrefixLength" value="{s.prefix}" />\n'
            f'<string name="GatewayAddress">{s.gateway}</string>\n'
            f'<string-array name="DNSServers" num="{len(s.dns)}">\n{dns}</string-array>\n'
            '<string name="ProxySettings">NONE</string>\n</IpConfiguration>\n')
