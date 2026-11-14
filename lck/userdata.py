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

PROP_CTX = "u:object_r:property_data_file:s0"
SYS_CTX = "u:object_r:system_data_file:s0"
ADBKEY_CTX = "u:object_r:adb_keys_file:s0"


def dbg(img, cmds):
    with tempfile.NamedTemporaryFile("w", suffix=".debugfs", delete=False) as f:
        f.write("\n".join(cmds) + "\n")
        path = f.name
    r = subprocess.run(["debugfs", "-w", "-f", path, img], capture_output=True, text=True)
    os.unlink(path)
    err = [l for l in r.stderr.splitlines() if l and not l.startswith("debugfs ")]
    if err:
        raise SystemExit("debugfs: " + "\n".join(err))
    return r.stdout


def cat(img, path):
    return subprocess.run(["debugfs", "-R", f"cat {path}", img],
                          capture_output=True).stdout


def put(img, tmp, path, data, uid, gid, mode, ctx):
    local = os.path.join(tmp, os.path.basename(path))
    open(local, "wb").write(data)
    ctxf = local + ".ctx"
    open(ctxf, "wb").write(ctx.encode() + b"\0")  # stock labels are NUL-terminated
    exists = subprocess.run(["debugfs", "-R", f"stat {path}", img],
                            capture_output=True, text=True).stdout.startswith("Inode:")
    if exists:
        dbg(img, [f"rm {path}"])
    dbg(img, [
        f"write {local} {path}",
        f"set_inode_field {path} uid {uid}",
        f"set_inode_field {path} gid {gid}",
        f"set_inode_field {path} mode 0{0o100000 | mode:o}",
        f"ea_set -f {ctxf} {path} security.selinux",
    ])


def inject(img: str, pub: str) -> None:
    key = open(pub, "rb").read()
    assert key.startswith(b"QAAAA"), "expected an adbkey.pub (public key)"
    with tempfile.TemporaryDirectory() as tmp:
        sg = cat(img, "/system/users/0/settings_global.xml").decode()
        new, n = re.subn(r'(name="adb_enabled" value=")0(")', r"\g<1>1\2", sg)
        if n != 1:
            raise SystemExit("adb_enabled setting not found exactly once")
        put(img, tmp, "/system/users/0/settings_global.xml", new.encode(), 1000, 1000, 0o600, SYS_CTX)
        put(img, tmp, "/property/persist.adb.tcp.port", b"5555", 0, 0, 0o600, PROP_CTX)
        put(img, tmp, "/property/persist.sys.usb.config", b"adb", 0, 0, 0o600, PROP_CTX)
        # mediashell crash-loops after the AVB unlock; RescueParty would then reset settings
        # (adb_enabled back to 0) and ask for a wipe before we can disable it over adb.
        put(img, tmp, "/property/persist.sys.disable_rescue", b"true", 0, 0, 0o600, PROP_CTX)
        put(img, tmp, "/misc/adb/adb_keys", key, 1000, 2000, 0o640, ADBKEY_CTX)
    print("injected: adb_enabled=1, persist.adb.tcp.port=5555, persist.sys.usb.config=adb, persist.sys.disable_rescue=true, adb_keys")


def main() -> None:
    inject(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
