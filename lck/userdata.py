"""Offline-provision a stock Android Things userdata image for adb over Wi-Fi.

Edits a COPY of the clock's stock userdata (plaintext ext4) with debugfs, no mount, no sudo.
Method from joserodpt/lenovo-cd24501f-research section 6. Labels/owners copied from the stock image.

  /data/system/users/0/settings_global.xml  adb_enabled 0 -> 1
  /data/property/persist.adb.tcp.port       "5555"
  /data/property/persist.sys.usb.config     "adb"
  /data/property/persist.sys.disable_rescue "true"  (RescueParty off; mediashell crash loop)
  /data/misc/adb/adb_keys                   host adbkey.pub (public key only)

usage: python -m lck.userdata <userdata copy.img> <adbkey.pub>
The image must already have had `e2fsck -fy` (journal replayed).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile


class UserdataFailed(Exception):
    pass


PROP_CTX = "u:object_r:property_data_file:s0"
SYS_CTX = "u:object_r:system_data_file:s0"
ADBKEY_CTX = "u:object_r:adb_keys_file:s0"


# debugfs and e2fsck: names on Linux, full paths elsewhere (lck/e2fs.py sets these). Every call runs
# in the image's own folder with relative file names: Windows' debugfs (Cygwin) reads its own command
# lines, where a path like C:\Users\Some One\... would break.
DEBUGFS = "debugfs"
E2FSCK = "e2fsck"
ENV = None


def _run(img, args, text=False):
    folder, name = os.path.split(os.path.abspath(img))
    return subprocess.run([DEBUGFS, *args, name], cwd=folder, capture_output=True, text=text, env=ENV)


def dbg(img, cmds):
    folder = os.path.dirname(os.path.abspath(img))
    with tempfile.NamedTemporaryFile("w", suffix=".debugfs", delete=False, dir=folder, newline="\n") as f:
        f.write("\n".join(cmds) + "\n")
        path = f.name
    r = _run(img, ["-w", "-f", os.path.basename(path)], text=True)
    os.unlink(path)
    err = [l for l in r.stderr.splitlines() if l and not l.startswith("debugfs ")]
    if err:
        raise UserdataFailed("debugfs: " + "\n".join(err))
    return r.stdout


def cat(img, path):
    return _run(img, ["-R", f"cat {path}"]).stdout


def exists(img, path) -> bool:
    return _run(img, ["-R", f"stat {path}"], text=True).stdout.startswith("Inode:")


def put(img, tmp, path, data, uid, gid, mode, ctx):
    """tmp: a folder inside the image's own folder (see _scratch)."""
    folder = os.path.dirname(os.path.abspath(img))
    local = os.path.join(tmp, os.path.basename(path))
    open(local, "wb").write(data)
    ctxf = local + ".ctx"
    open(ctxf, "wb").write(ctx.encode() + b"\0")  # stock labels are NUL-terminated
    rel, relctx = (os.path.relpath(x, folder).replace(os.sep, "/") for x in (local, ctxf))
    if exists(img, path):
        dbg(img, [f"rm {path}"])
    parent, name = path.rsplit("/", 1)
    dbg(img, [
        # cd first, then a plain name: debugfs 1.44 (Windows' Cygwin build) makes "write a /x/y" a file
        # literally named "/x/y" in the current folder (e2e CI on Windows); 1.47 resolves the path.
        f"cd {parent or '/'}",
        f"write {rel} {name}",
        "cd /",
        f"set_inode_field {path} uid {uid}",
        f"set_inode_field {path} gid {gid}",
        f"set_inode_field {path} mode 0{0o100000 | mode:o}",
        f"ea_set -f {relctx} {path} security.selinux",
    ])


def _scratch(img):
    """A temporary folder next to the image, so debugfs gets short relative names."""
    return tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(img)), prefix="dbg-")


def inject(img: str, pub: str, runner=None) -> None:
    """Edits img in place. Every debugfs call is a plain subprocess: its output holds
    the Wi-Fi settings file, so none of it goes to the run log."""
    key = open(pub, "rb").read()
    if not key.startswith(b"QAAAA"):
        raise UserdataFailed(f"{pub} isn't an adb public key (adbkey.pub).")
    with _scratch(img) as tmp:
        sg = cat(img, "/system/users/0/settings_global.xml").decode()
        new, n = re.subn(r'(name="adb_enabled" value=")[01](")', r"\g<1>1\2", sg)
        if n != 1:
            raise UserdataFailed("The user data has no adb_enabled setting (or more than one): it isn't "
                                 "from a stock clock that has been set up.")
        put(img, tmp, "/system/users/0/settings_global.xml", new.encode(), 1000, 1000, 0o600, SYS_CTX)
        put(img, tmp, "/property/persist.adb.tcp.port", b"5555", 0, 0, 0o600, PROP_CTX)
        put(img, tmp, "/property/persist.sys.usb.config", b"adb", 0, 0, 0o600, PROP_CTX)
        # mediashell crash-loops after the AVB unlock; RescueParty would then reset settings
        # (adb_enabled back to 0) and ask for a wipe before we can disable it over adb.
        put(img, tmp, "/property/persist.sys.disable_rescue", b"true", 0, 0, 0o600, PROP_CTX)
        put(img, tmp, "/misc/adb/adb_keys", key, 1000, 2000, 0o640, ADBKEY_CTX)
    if runner is None:
        print("injected: adb_enabled=1, persist.adb.tcp.port=5555, persist.sys.usb.config=adb, "
              "persist.sys.disable_rescue=true, adb_keys")


WIFI_STORE = "/misc/wifi/WifiConfigStore.xml"
WIFI_CTX = "u:object_r:wifi_data_file:s0"
EMPTY_STORE = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<WifiConfigStoreData>
<int name="Version" value="1" />
<NetworkList>
</NetworkList>
<PasspointConfigData>
<long name="ProviderIndex" value="0" />
</PasspointConfigData>
</WifiConfigStoreData>
"""
# One WPA2-PSK network, in the shape Android Things 8.1 wrote for the network the first clock was
# set up on (its WifiConfigStore.xml, read from the backup; only the values differ). Untested as
# written by the tool: e2e run 5 found the need, the next run tests it.
NETWORK = """<Network>
<WifiConfiguration>
<string name="ConfigKey">{key}</string>
<string name="SSID">{ssid}</string>
<null name="BSSID" />
<string name="PreSharedKey">{psk}</string>
<null name="WEPKeys" />
<int name="WEPTxKeyIndex" value="0" />
<boolean name="HiddenSSID" value="true" />
<boolean name="RequirePMF" value="false" />
<byte-array name="AllowedKeyMgmt" num="1">02</byte-array>
<byte-array name="AllowedProtocols" num="1">02</byte-array>
<byte-array name="AllowedAuthAlgos" num="1">01</byte-array>
<byte-array name="AllowedGroupCiphers" num="1">0f</byte-array>
<byte-array name="AllowedPairwiseCiphers" num="1">04</byte-array>
<boolean name="Shared" value="true" />
<int name="Status" value="2" />
<null name="FQDN" />
<null name="ProviderFriendlyName" />
<null name="LinkedNetworksList" />
<null name="DefaultGwMacAddress" />
<boolean name="ValidatedInternetAccess" value="false" />
<boolean name="NoInternetAccessExpected" value="false" />
<int name="UserApproved" value="0" />
<boolean name="MeteredHint" value="false" />
<int name="MeteredOverride" value="0" />
<boolean name="UseExternalScores" value="false" />
<int name="NumAssociation" value="0" />
<int name="CreatorUid" value="1000" />
<string name="CreatorName">android.uid.system:1000</string>
<string name="CreationTime">time=01-01 00:00:00.000</string>
<int name="LastUpdateUid" value="1000" />
<string name="LastUpdateName">android.uid.system:1000</string>
<int name="LastConnectUid" value="0" />
<boolean name="IsLegacyPasspointConfig" value="false" />
<long-array name="RoamingConsortiumOIs" num="0" />
</WifiConfiguration>
<NetworkStatus>
<string name="SelectionStatus">NETWORK_SELECTION_ENABLED</string>
<string name="DisableReason">NETWORK_SELECTION_ENABLE</string>
<null name="ConnectChoice" />
<long name="ConnectChoiceTimeStamp" value="-1" />
<boolean name="HasEverConnected" value="false" />
</NetworkStatus>
<IpConfiguration>
<string name="IpAssignment">DHCP</string>
<string name="ProxySettings">NONE</string>
</IpConfiguration>
</Network>
"""


def wifi_networks(img: str) -> int:
    """How many Wi-Fi networks the user data has saved. Needs e2fsck -fy first. The file's
    contents (with the password) are never logged or printed."""
    return cat(img, WIFI_STORE).decode("utf-8", "replace").count("<Network>")


def check_wifi(ssid: str, psk: str) -> None:
    check_ssid(ssid)
    check_psk(psk)


def check_ssid(ssid: str) -> None:
    if not 1 <= len(ssid.encode()) <= 32:
        raise UserdataFailed("A Wi-Fi name is 1 to 32 characters.")


def check_psk(psk: str) -> None:
    if not 8 <= len(psk) <= 63:
        raise UserdataFailed("A WPA2 Wi-Fi password is 8 to 63 characters. Networks with no password, or WEP, "
                             "aren't supported by the tool: set the clock up with the Google Home app instead.")


def add_wifi(img: str, ssid: str, psk: str) -> None:
    """Add one WPA2-PSK network to the copy of the user data, owners and SELinux label as stock."""
    from xml.sax.saxutils import escape
    check_wifi(ssid, psk)
    ents = {'"': "&quot;", "'": "&apos;"}
    entry = NETWORK.format(key=escape(f'"{ssid}"WPA_PSK', ents), ssid=escape(f'"{ssid}"', ents),
                           psk=escape(f'"{psk}"', ents))
    if not exists(img, "/misc/wifi"):
        raise UserdataFailed("This user data has no /misc/wifi folder, so the tool can't add a network to it. "
                             "Set the clock up on your Wi-Fi with the Google Home app instead.")
    xml = cat(img, WIFI_STORE).decode("utf-8", "replace") or EMPTY_STORE
    if "</NetworkList>" not in xml:
        xml = EMPTY_STORE
    xml = xml.replace("</NetworkList>", entry + "</NetworkList>", 1)
    with _scratch(img) as tmp:
        put(img, tmp, WIFI_STORE, xml.encode(), 1000, 1000, 0o600, WIFI_CTX)


def main() -> None:
    inject(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
