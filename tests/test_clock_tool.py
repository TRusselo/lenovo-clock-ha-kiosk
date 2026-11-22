"""The clock-specific parts of lck: no hardware, no network."""
from __future__ import annotations

import hashlib
import struct
import subprocess
import uuid
from pathlib import Path

import pytest

from lck import backup, clock, flash, gpt, mtk, plan
from lck.modes import Mode, detect, wait_for_mode, UsbNoPermission, current, MultipleDevices
from lck.runner import Result, Tools
from lck.steps import Context, Paths
from tests.fakes import FakeRunner, FakeUI

GUID = uuid.UUID("1238f5a7-c796-490a-93a5-d954b8ff0f38")


def make_gpt(parts, guid=GUID, total=200) -> bytes:
    """A small disk image: protective MBR, GPT header at LBA 1, entries at LBA 2."""
    entries = b""
    for name, first, count in parts:
        e = bytearray(128)
        e[0:16] = uuid.uuid4().bytes
        struct.pack_into("<QQ", e, 32, first, first + count - 1)
        n = name.encode("utf-16-le")
        e[56:56 + len(n)] = n
        entries += bytes(e)
    entries += bytes(128 * (128 - len(parts)))
    head = bytearray(512 * 2)
    h = bytearray(92)
    h[:8] = b"EFI PART"
    h[56:72] = guid.bytes_le
    struct.pack_into("<QII", h, 72, 2, 128, 128)
    head[512:512 + 92] = h
    return bytes(head) + entries


def ctx_for(tmp_path, runner=None, ui=None, **state):
    c = Context(runner=runner or FakeRunner(), ui=ui or FakeUI(), paths=Paths(tmp_path),
                # Linux unless a test says otherwise: on a Windows CI runner the real UsbDk check and the Windows
                # hints would otherwise run inside tests written for neither (the Windows ones pass platform="win32").
                state={"progress_id": "clock", "tools": Tools(adb=Path("adb"), fastboot=Path("fastboot")),
                       "platform": "linux", **state})
    return c


# --- partition table -------------------------------------------------------

def test_reads_the_partition_table_and_disk_guid():
    head = make_gpt([("misc", 40, 8), ("boot_a", 48, 16), ("userdata", 64, 100)])
    names = [p.name for p in gpt.read_gpt(head)]
    assert names == ["misc", "boot_a", "userdata"]
    assert gpt.disk_guid(head) == str(GUID)
    assert gpt.by_name(head)["boot_a"].offset == 48 * 512


def test_signature_tells_two_clocks_apart():
    a = make_gpt([("misc", 40, 8)])
    b = make_gpt([("misc", 40, 8)], guid=uuid.uuid4())
    assert gpt.signature(a) == gpt.signature(make_gpt([("misc", 40, 8)]))
    assert gpt.signature(a) != gpt.signature(b)


def test_not_a_disk_is_refused():
    with pytest.raises(ValueError):
        gpt.read_gpt(bytes(2048))


# --- backup: extract and verify --------------------------------------------

def _zstd_backup(tmp_path: Path) -> Path:
    parts = [("misc", 40, 2), ("boot_a", 42, 4), ("userdata", 46, 10)]
    raw = bytearray(make_gpt(parts))
    raw += bytes(56 * 512 - len(raw))
    raw[42 * 512:46 * 512] = b"B" * (4 * 512)
    raw[46 * 512:56 * 512] = b"U" * (10 * 512)
    folder = tmp_path / "backup" / "clock"
    folder.mkdir(parents=True)
    (folder / backup.RAW).write_bytes(bytes(raw))
    backup.compress(folder / backup.RAW, folder / backup.ZST)     # the library: no zstd program needed
    (folder / backup.GPT_FILE).write_bytes(bytes(raw[:gpt.GPT_BYTES]))
    for n in backup.BOOT_AREAS:
        (folder / n).write_bytes(b"P" * 64)
    sums = {backup.RAW: hashlib.sha256(raw).hexdigest()}
    for n in [backup.ZST, backup.GPT_FILE, *backup.BOOT_AREAS]:
        sums[n] = backup.sha256(folder / n)
    (folder / backup.SUMS).write_text("".join(f"{d}  {n}\n" for n, d in sums.items()))
    (folder / backup.RAW).unlink()
    return folder


def test_a_complete_backup_verifies_and_a_damaged_one_doesnt(tmp_path):
    folder = _zstd_backup(tmp_path)
    assert backup.verify_backup(folder) == []
    (folder / "boot1.bin").write_bytes(b"x")
    assert backup.verify_backup(folder) == ["boot1.bin doesn't match its checksum"]
    assert backup.verify_backup(tmp_path / "nothing")


def test_extract_pulls_partitions_and_checks_the_whole_image(tmp_path):
    folder = _zstd_backup(tmp_path)
    c = ctx_for(tmp_path)
    files = backup.extract(c, folder, ["boot_a", "userdata"], tmp_path / "parts")
    assert files["boot_a"].read_bytes() == b"B" * 2048
    assert files["userdata"].read_bytes() == b"U" * 5120


def test_extract_refuses_a_damaged_image(tmp_path):
    folder = _zstd_backup(tmp_path)
    sums = (folder / backup.SUMS).read_text().replace(backup.read_sums(folder)[backup.RAW], "0" * 64)
    (folder / backup.SUMS).write_text(sums)
    with pytest.raises(backup.BackupMismatch, match="damaged"):
        backup.extract(ctx_for(tmp_path), folder, ["boot_a"], tmp_path / "parts")
    assert not (tmp_path / "parts" / "boot_a.bin").exists()


def test_extract_names_a_partition_the_clock_doesnt_have(tmp_path):
    folder = _zstd_backup(tmp_path)
    with pytest.raises(backup.BackupMismatch, match="system_a"):
        backup.extract(ctx_for(tmp_path), folder, ["system_a"], tmp_path / "parts")


def test_pack_and_unpack_round_trip(tmp_path):
    folder = _zstd_backup(tmp_path)
    c = ctx_for(tmp_path)
    z = backup.pack(c, folder)
    assert z.name == "clock.zip"
    out = backup.unpack(c, z, tmp_path / "restore")
    assert backup.verify_backup(out) == []


def test_restore_refuses_another_clocks_backup(tmp_path, monkeypatch):
    folder = _zstd_backup(tmp_path)
    other = make_gpt([("misc", 40, 2)], guid=uuid.uuid4())
    monkeypatch.setattr(backup, "RESTORE_SET", ["boot_a", "misc"])
    monkeypatch.setattr(backup, "read_clock_gpt", lambda ctx, dest: other)
    ui = FakeUI(["restore"])
    with pytest.raises(backup.BackupMismatch, match="different clock"):
        backup.restore(ctx_for(tmp_path, ui=ui), folder, tmp_path / "work")
    assert not any(l.startswith("[ask]") for l in ui.lines)   # never asked: nothing to confirm


def test_restore_writes_only_after_the_word(tmp_path, monkeypatch):
    folder = _zstd_backup(tmp_path)
    monkeypatch.setattr(backup, "RESTORE_SET", ["boot_a", "misc", "userdata"])
    monkeypatch.setattr(backup, "read_clock_gpt", lambda ctx, dest: backup.head_of(folder))
    ran = []
    monkeypatch.setattr(backup.mtk, "run_in_brom", lambda ctx, cmds, **kw: ran.append(cmds))
    with pytest.raises(backup.BackupMismatch, match="cancelled"):
        backup.restore(ctx_for(tmp_path, ui=FakeUI([""])), folder, tmp_path / "work")
    assert ran == []
    backup.restore(ctx_for(tmp_path, ui=FakeUI(["restore"])), folder, tmp_path / "work")
    assert ran == [["w boot_a parts/boot_a.bin", "w misc parts/misc.bin", "w userdata parts/userdata.bin"]]


def test_restore_never_writes_the_bootloader_or_the_clocks_own_data():
    for name in backup.RESTORE_SET:
        mtk.assert_safe(f"w {name} parts/{name}.bin")
    for name in ("preloader", "lk_a", "tee_b", "seccfg", "proinfo", "nvram", "oem_bootloader_a"):
        with pytest.raises(mtk.ForbiddenCommand):
            mtk.assert_safe(f"w {name} x.bin")
    for bad in ("wf full_user.bin", "e userdata", "da seccfg unlock", "wl folder", ""):
        with pytest.raises(mtk.ForbiddenCommand):
            mtk.assert_safe(bad)


# --- mtkclient in BROM ------------------------------------------------------

class FakeProc:
    def __init__(self, polls, code, text, log):
        self.polls, self.returncode_final, self.text, self.log = list(polls), code, text, log
        self.returncode = None

    def poll(self):
        if self.polls and self.polls.pop(0):
            return None
        self.returncode = self.returncode_final
        return self.returncode

    def wait(self):
        self.returncode = self.returncode_final

    def kill(self):
        self.returncode = -9


class SpawnRunner(FakeRunner):
    def __init__(self, procs):
        super().__init__()
        self.procs = procs
        self.spawned = []

    def spawn(self, args, *, out_file, cwd=None, env=None):
        self.spawned.append([str(a) for a in args])
        p = self.procs.pop(0)
        Path(out_file).write_text(p.text)
        return p

    def finished(self, proc):
        return proc.text


def test_brom_retries_after_the_preloader_then_follows_the_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    modes = iter([Mode.NONE, Mode.PRELOADER] + [Mode.NONE, Mode.BROM, Mode.DA] + [Mode.DA] * 50)
    runner = SpawnRunner([FakeProc([True] * 5, 1, "", None),
                          FakeProc([True] * 6, 0, "Progress: 50.0% Read\nDumped sector 0", None)])
    ui = FakeUI()
    c = ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: [next(modes)])
    text = mtk.run_in_brom(c, ["rf full_user.bin"], cwd=tmp_path, title="Backing up", sleep=lambda s: None)
    assert "Dumped" in text
    assert len(runner.spawned) == 2
    assert any("preloader" in l for l in ui.lines)
    assert sum(l.startswith("[physical]") for l in ui.lines) == 2
    assert runner.spawned[0][-2:] == ["rf", "full_user.bin"]


def test_brom_runs_several_commands_in_one_session(tmp_path, monkeypatch):
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    runner = SpawnRunner([FakeProc([], 0, "Dumped x\nDumped y", None)])
    c = ctx_for(tmp_path, runner=runner, detect=lambda: [])
    mtk.run_in_brom(c, ["rf a.bin", "r preloader b.bin --parttype boot1"], cwd=tmp_path, title="t",
                    sleep=lambda s: None)
    assert runner.spawned[0][-2:] == ["multi", "rf a.bin;r preloader b.bin --parttype boot1"]


# --- modes -----------------------------------------------------------------

def test_modes_come_from_usb_ids():
    assert detect(scan=lambda: [(Mode.FASTBOOT, 1, 2)], access=lambda b, a: True) == [Mode.FASTBOOT]
    with pytest.raises(UsbNoPermission):
        detect(scan=lambda: [(Mode.BROM, 1, 2)], access=lambda b, a: False)


def test_two_clocks_at_once_stop_the_run(tmp_path):
    c = ctx_for(tmp_path, detect=lambda: [Mode.FASTBOOT, Mode.BROM])
    with pytest.raises(MultipleDevices):
        current(c)


def test_wait_for_fastboot_prompts_then_finds_it(tmp_path):
    modes = iter([Mode.NONE, Mode.NONE, Mode.FASTBOOT])
    ui = FakeUI()
    c = ctx_for(tmp_path, ui=ui, detect=lambda: [m for m in [next(modes)] if m != Mode.NONE])
    wait_for_mode(Mode.FASTBOOT, c, prompt="Hold VOLUME UP", sleep=lambda s: None)
    assert ui.lines[0] == "[physical] Hold VOLUME UP"
    assert "Found it" in ui.lines[-1]


# --- flash -----------------------------------------------------------------

def test_flash_list_puts_misc_last_and_only_slot_a(tmp_path):
    for f in ("boot.img", "vendor.img", "vbmeta.img", "misc.img", "system_chunk.0", "system_chunk.1",
              "system_chunk.10", "oem_chunk.0", "userdata_chunk.0"):
        (tmp_path / f).write_bytes(b"x")
    pairs = flash.flash_list(tmp_path)
    assert [p for _, p in pairs][-1] == "misc"
    assert [f.name for f, p in pairs if p == "system_a"] == ["system_chunk.0", "system_chunk.1", "system_chunk.10"]
    assert {p for _, p in pairs} <= flash.FLASH_SET
    with pytest.raises(flash.ForbiddenCommand):
        flash.assert_safe([(Path("x"), "lk_a")])


def test_flash_list_refuses_when_pieces_are_missing(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"x")
    with pytest.raises(flash.FlashFailed, match="images step"):
        flash.flash_list(tmp_path)


# --- the clock over Wi-Fi ----------------------------------------------------

def test_adb_always_names_the_clock_by_its_address(tmp_path):
    runner = FakeRunner({("-s", "10.0.0.5:5555", "shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n")})
    c = ctx_for(tmp_path, runner=runner, ip="10.0.0.5")
    assert clock.prop(c, "ro.product.device") == "mt8167s_som"
    assert all("-s" in call for call in runner.calls)


def test_a_device_that_isnt_a_clock_is_refused(tmp_path):
    runner = FakeRunner({
        ("-s", "10.0.0.5:5555", "get-state"): Result(0, "device\n"),
        ("-s", "10.0.0.5:5555", "shell", "getprop", "ro.product.device"): Result(0, "msm8x53_som\n"),
    })
    c = ctx_for(tmp_path, runner=runner, ip="10.0.0.5")
    with pytest.raises(clock.NotAClock, match="msm8x53_som"):
        clock.require_clock(c)
    assert not clock.converted(c)


def test_converted_needs_slot_a(tmp_path):
    answers = {("-s", "10.0.0.5:5555", "get-state"): Result(0, "device\n"),
               ("-s", "10.0.0.5:5555", "shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
               ("-s", "10.0.0.5:5555", "shell", "getprop", "ro.boot.slot_suffix"): Result(0, "_a\n")}
    assert clock.converted(ctx_for(tmp_path, runner=FakeRunner(answers), ip="10.0.0.5"))
    answers[("-s", "10.0.0.5:5555", "shell", "getprop", "ro.boot.slot_suffix")] = Result(0, "_b\n")
    assert not clock.converted(ctx_for(tmp_path, runner=FakeRunner(answers), ip="10.0.0.5"))
    assert not clock.converted(ctx_for(tmp_path))   # no address yet


def test_ip_is_checked():
    assert clock.valid_ip("10.0.0.23")
    assert not clock.valid_ip("10.0.0")
    assert not clock.valid_ip("clock.local")


# --- the plan --------------------------------------------------------------

def test_steps_are_in_the_manuals_order():
    names = [s.name for s in plan.setup_steps()]
    assert names == ["prerequisites", "backup", "unlock", "images", "userdata", "misc", "flash", "connect",
                     "root", "volume-keys", "timezone", "tzdata", "degoogle", "keyboard", "kiosk-install", "kiosk-grants", "kiosk-settings",
                     "verify"]


def test_missing_linux_tools_are_named_with_their_package():
    missing = plan.missing_tools(which=lambda t: None if t == "debugfs" else "/usr/bin/" + t)
    assert len(missing) == 1 and "debugfs" in missing[0] and "e2fsprogs" in missing[0]


def test_flash_asks_first_unless_this_run_unlocked(tmp_path, monkeypatch):
    for f in ("boot.img", "vendor.img", "vbmeta.img", "misc.img", "system_chunk.0", "oem_chunk.0",
              "userdata_chunk.0"):
        (tmp_path / "work" / "clock" / "images").mkdir(parents=True, exist_ok=True)
        (tmp_path / "work" / "clock" / "images" / f).write_bytes(b"x")
    flashed = []
    monkeypatch.setattr(flash, "fbmulti", lambda ctx, pairs, **kw: flashed.append(pairs))
    with pytest.raises(flash.FlashFailed, match="Nothing was changed.*still in fastboot"):
        flash.FlashStep().run(ctx_for(tmp_path, ui=FakeUI([""]), detect=lambda: [Mode.FASTBOOT]))
    assert flashed == []
    monkeypatch.setattr(flash, "restart_from_fastboot", lambda ctx: ctx.ui.physical("replug"))
    flash.FlashStep().run(ctx_for(tmp_path, ui=FakeUI(["flash"])))
    ui = FakeUI()
    flash.FlashStep().run(ctx_for(tmp_path, ui=ui, unlocked_now=True))
    assert "[physical] replug" in ui.lines
    assert len(flashed) == 2 and not any(l.startswith("[ask]") for l in ui.lines)


def test_a_silent_fastboot_never_counts_as_locked(tmp_path):
    runner = FakeRunner({("getvar",): Result(0, "")})
    with pytest.raises(flash.FlashFailed, match="didn't say"):
        flash.unlocked(ctx_for(tmp_path, runner=runner))
    runner = FakeRunner({("getvar", "unlocked"): Result(0, "unlocked: yes\n"),
                         ("getvar", "at-vboot-state"): Result(0, "(bootloader) avb-locked: 0\n")})
    assert flash.unlocked(ctx_for(tmp_path, runner=runner)) == (True, True)


# --- e2e run 2 findings ------------------------------------------------------

def test_one_bar_for_the_whole_restore(tmp_path):
    log = tmp_path / "mtk.txt"
    # Real mtkclient output from run 2's restore (\r between progress redraws).
    log.write_text("Done |\u2588---------| 6.2% Write: (0x200000/0x2000000),0.00 MB/s\r"
                   "Wrote parts/boot_a.bin to sector 134184 with sector count 65536.\n"
                   "Progress: |\u2588\u2588---| 50.0% Write: (0x400000/0x2000000), 20.11 MB/s 01s left \r", encoding="utf-8")
    assert mtk.overall(log, [10, 10, 80]) == pytest.approx(15.0)
    log.write_text("Wrote a\nWrote b\nWrote c\n")
    assert mtk.overall(log, [10, 10, 80]) == pytest.approx(100.0)


def test_a_clock_adb_already_knows_is_found_without_ip(tmp_path):
    runner = FakeRunner({
        ("devices",): Result(0, "List of devices attached\nHUA05TEST\tdevice\n10.0.0.9:5555\tdevice\n"
                                "10.0.0.7:5555\tdevice\n"),
        ("-s", "10.0.0.9:5555", "shell", "getprop", "ro.product.device"): Result(0, "msm8x53_som\n"),
        ("-s", "10.0.0.7:5555", "shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
    })
    assert clock.find_on_wifi(ctx_for(tmp_path, runner=runner)) == "10.0.0.7"
    assert not any("HUA05TEST" in c for call in runner.calls for c in call)   # the USB device is never asked


def _degoogle_runner(lists, root=True):
    from lck import system
    base = ("-s", "10.0.0.7:5555")
    saved = "".join(f'<pkg name="{p}" enabled="3" />' for p in [*system.DISABLE, system.CAST])
    return FakeRunner({base + ("get-state",): Result(0, "device\n"),
                       base + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
                       base + ("shell", "su", "-c", "id"): Result(0, "uid=0(root)\n") if root
                       else Result(127, "su: not found\n"),
                       base + ("shell", "su", "-c", f"'test -x {system.HIDE_SCRIPT} && echo present'"):
                           Result(0, "present\n"),
                       base + ("shell", "pm", "list", "packages", "--user", "0"): lists,
                       base + ("shell", "pm", "list", "packages", "-d", "--user", "0"):
                           Result(0, "package:com.google.assistant.core\n"),
                       base + ("shell", "su", "-c", f"'cat {system.RESTRICTIONS}'"): Result(0, saved),
                       base + ("shell", "pidof"): Result(0, "123\n")})


def test_degoogle_hides_cast_with_the_boot_script_in_one_restart(tmp_path, monkeypatch):
    # e2e runs 2 and 3: uninstalling Cast for user 0 didn't last, with or without disabling first.
    from lck import system
    cast = f"package:{system.CAST}\npackage:com.google.assistant.core\n"
    runner = _degoogle_runner([Result(0, cast), Result(0, "package:com.google.assistant.core\n")])
    reboots = []
    monkeypatch.setattr(clock, "reboot", lambda ctx, why="": reboots.append(why))
    ui = FakeUI()
    system.DegoogleStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"))
    assert len(reboots) == 1
    assert not any("uninstall" in c for c in runner.calls)
    # e2e run 3: the chained command must reach su -c as ONE quoted argument, or only mkdir runs as root.
    su = [c for c in runner.calls if c[-3:-1] == ["su", "-c"] and "cp " in c[-1]]
    assert len(su) == 1 and su[0][-1].startswith("'") and su[0][-1].endswith("'") and "&&" in su[0][-1]
    assert any("blank" in l for l in ui.lines)
    assert "mount -o bind /data/adb/empty-cast /oem/app/com.google.android.apps.mediashell" in system.HIDE_CAST


def test_degoogle_without_root_stops_before_changing_anything(tmp_path, monkeypatch):
    from lck import system
    monkeypatch.setattr(clock, "reboot", lambda ctx, why="": None)
    runner = _degoogle_runner([Result(0, f"package:{system.CAST}\n")], root=False)
    with pytest.raises(clock.SetupFailed, match="needs root"):
        system.DegoogleStep().run(ctx_for(tmp_path, runner=runner, ip="10.0.0.7"))
    assert not any("disable-user" in c for c in runner.calls)


def test_degoogle_says_when_cast_still_comes_back(tmp_path, monkeypatch):
    from lck import system
    cast = f"package:{system.CAST}\n"
    monkeypatch.setattr(clock, "reboot", lambda ctx, why="": None)
    runner = _degoogle_runner([Result(0, cast)])
    with pytest.raises(clock.SetupFailed, match="Cast came back"):
        system.DegoogleStep().run(ctx_for(tmp_path, runner=runner, ip="10.0.0.7"))


def test_root_comes_before_degoogle():
    names = [s.name for s in plan.setup_steps()]
    assert names.index("root") < names.index("degoogle")


def test_flash_says_found_it_only_after_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "uv_path", lambda ctx: Path("uv"))
    monkeypatch.setattr(flash, "fetch", lambda id_, ctx: Path("fbmulti.py"))
    runner = SpawnRunner([FakeProc([True, True], 0, "ALL DONE in 3 s", None)])
    ui = FakeUI()
    flash.fbmulti(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: [Mode.FASTBOOT]),
                  [(tmp_path / "b.img", "boot_a")], title="t", sleep=lambda s: None)
    assert not any("Found it" in l or l.startswith("[physical]") for l in ui.lines)
    assert any("fastboot mode already" in l for l in ui.lines)


def test_power_cycle_box_waits_for_the_clock_to_leave_fastboot(tmp_path):
    modes = iter([[Mode.FASTBOOT], [Mode.FASTBOOT], []])
    ui = FakeUI()
    c = ctx_for(tmp_path, ui=ui, detect=lambda: next(modes))
    flash.restart_from_fastboot(c)     # two half-second polls
    assert ui.lines[0].startswith("[physical] To start the clock normally")
    assert any("left fastboot" in l for l in ui.lines)


def test_the_stop_reason_is_the_last_line_of_the_log(tmp_path):
    from lck import cli
    import argparse
    args = argparse.Namespace(name="clock", workdir=tmp_path, command="setup")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "clock.log").write_text("old mtkclient lines\n")
    cli._log_error(args, "GitHub is limiting lookups, token=abc")
    last = (tmp_path / "logs" / "clock.log").read_text().splitlines()[-1]
    assert last.startswith("STOPPED GitHub") and "abc" not in last


def test_intro_leaves_out_a_backup_thats_already_there(tmp_path):
    from lck import cli
    folder = _zstd_backup(tmp_path)
    c = ctx_for(tmp_path)
    assert "35 minutes" not in cli._intro(c) and "already here" in cli._intro(c)
    c2 = ctx_for(tmp_path / "other")
    assert "35 minutes" in cli._intro(c2)


def test_the_github_token_is_sent_only_when_set(monkeypatch):
    from lck import kiosk
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert kiosk._headers() == {}
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    assert kiosk._headers() == {"Authorization": "Bearer t0k"}


def test_an_old_address_that_no_longer_answers_is_dropped(tmp_path, monkeypatch):
    import itertools
    import json
    monkeypatch.setattr(clock.time, "sleep", lambda s: None)
    ticks = itertools.count(0, 30)          # the one-minute grace passes in two looks
    monkeypatch.setattr(clock.time, "monotonic", lambda: next(ticks))
    from lck import system
    from lck.steps import _load
    c = ctx_for(tmp_path, ui=FakeUI([""]), runner=FakeRunner(default=Result(1, "")))
    c.paths.progress.mkdir(parents=True)
    (c.paths.progress / "clock.json").write_text(json.dumps({"done": ["prerequisites"], "ip": "10.0.0.5"}))
    system.check_saved_ip(c)
    assert c.state["ip"] is None
    _load(c)
    assert c.state["ip"] is None            # the saved one isn't put back
    c2 = ctx_for(tmp_path, ui=FakeUI(["10.0.0.8"]), runner=FakeRunner(default=Result(1, "")))
    system.check_saved_ip(c2)
    assert c2.state["ip"] == "10.0.0.8"


def test_a_new_address_after_a_restart_is_asked_for_and_used(tmp_path, monkeypatch):
    # The address can change at any restart, not just between runs.
    clocks = iter([0, 0, 300, 300, 300, 300, 300, 300])
    monkeypatch.setattr(clock.time, "monotonic", lambda: next(clocks, 300))
    seen = []

    def booted(ctx):
        seen.append(ctx.state["ip"])
        return ctx.state["ip"] == "10.0.0.8"
    monkeypatch.setattr(clock, "booted", booted)
    ui = FakeUI(["10.0.0.8"])
    c = ctx_for(tmp_path, ui=ui, ip="10.0.0.5")
    clock.wait_booted(c, sleep=lambda s: None)
    assert c.state["ip"] == "10.0.0.8" and "10.0.0.5" in seen
    assert any("may have changed" in l for l in ui.lines)


def test_a_clock_leaving_a_mode_isnt_a_permission_problem():
    # e2e run 3: the device file was gone (clock unplugged, or past BROM), read as "not allowed".
    from lck.modes import detect
    scans = iter([[(Mode.FASTBOOT, 1, 2)], []])
    assert detect(scan=lambda: next(scans), access=lambda b, a: False, sleep=lambda s: None) == []
    with pytest.raises(UsbNoPermission):
        detect(scan=lambda: [(Mode.BROM, 1, 2)], access=lambda b, a: False, sleep=lambda s: None)


def _fastboot_wait(tmp_path, seq):
    """seq: (seconds, modes) pairs, one per poll."""
    import itertools
    polls = iter(seq)
    now = [0]

    def detect():
        secs, modes = next(polls)
        now[0] = secs
        return modes
    ui = FakeUI()
    c = ctx_for(tmp_path, ui=ui, detect=detect)
    wait_for_mode(Mode.FASTBOOT, c, prompt="Hold VOLUME UP", sleep=lambda s: None, clock=lambda: now[0])
    return ui


def test_the_normal_gap_between_brom_and_fastboot_isnt_called_a_mistake(tmp_path):
    # e2e run 3's good holds: BROM, a few seconds of nothing while LK starts, then fastboot.
    ui = _fastboot_wait(tmp_path, [(0, []), (0, []), (3, [Mode.BROM]), (5, []), (8, []), (10, [Mode.FASTBOOT])])
    assert not any("started normally" in l for l in ui.lines)
    assert "Found it" in ui.lines[-1]


def test_brom_then_nothing_for_long_says_it_started_normally(tmp_path):
    ui = _fastboot_wait(tmp_path, [(0, []), (0, []), (3, [Mode.BROM]), (6, []), (12, []), (19, []), (25, []),
                                   (40, [Mode.FASTBOOT])])
    assert sum("started normally" in l for l in ui.lines) == 1


def test_a_resumed_root_step_doesnt_patch_or_flash_again(tmp_path, monkeypatch):
    from lck import root
    base = ("-s", "10.0.0.7:5555")
    runner = FakeRunner({base + ("get-state",): Result(0, "device\n"),
                         base + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
                         base + ("shell", "su", "-c", "id"): [Result(13, "Permission denied\n"),
                                                              Result(13, "Permission denied\n"),
                                                              Result(0, "uid=0(root)\n")],
                         base + ("shell", "pm", "path"): Result(0, "package:/data/app/magisk.apk\n")})
    monkeypatch.setattr(root, "stock_boot", lambda ctx: Path("boot-stock.img"))
    monkeypatch.setattr(root, "patch", lambda ctx, b: pytest.fail("patched again"))
    monkeypatch.setattr(root, "fbmulti", lambda *a, **k: pytest.fail("flashed again"))
    monkeypatch.setattr(root.RootStep, "_first_run", lambda self, ctx: None)
    ui = FakeUI()
    root.RootStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"))
    assert any("already running" in l for l in ui.lines)


def test_marked_words_are_bold_on_a_terminal_and_plain_elsewhere(monkeypatch):
    import io
    import lck.ui as ui_mod
    from lck.ui import UI

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(ui_mod, "_windows_vt_on", lambda: True)
    monkeypatch.setenv("WT_SESSION", "1")   # Windows CI: the redrawn screen, as in Windows Terminal
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = Tty()
    UI(stdout=out).physical("For **fastboot mode**: unplug it, then plug it back in. Hold no buttons.")
    text = out.getvalue()
    assert "**" not in text and "\x1b[1mfastboot mode" in text and "\x1b[1munplug" in text
    assert "\x1b[1mplug it back in" in text and "\x1b[1mHold no buttons" in text
    plain_out = io.StringIO()
    UI(stdout=plain_out).physical("For **fastboot mode**: hold")
    assert "**" not in plain_out.getvalue() and "fastboot mode" in plain_out.getvalue()


def test_the_log_tail_is_only_this_run(tmp_path, capsys):
    from lck import cli
    import argparse
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "clock.log").write_text(
        f"line from the restore\n{cli.RUN_MARK} restore\nline from the run before\n{cli.RUN_MARK} setup\n"
        "this run's line\nSTOPPED x\n")
    cli._print_log_tail(argparse.Namespace(name="clock", workdir=tmp_path))
    err = capsys.readouterr().err
    assert "this run's line" in err and "line from the run before" not in err and "line from the restore" not in err


def test_su_gets_the_whole_command_as_one_argument(tmp_path):
    runner = FakeRunner()
    clock.su(ctx_for(tmp_path, runner=runner, ip="10.0.0.7"), "mkdir -p /a && cp b /a/b")
    assert runner.calls[-1][-3:] == ["su", "-c", "'mkdir -p /a && cp b /a/b'"]


def test_bold_survives_a_phrase_wrapped_across_lines(monkeypatch):
    import io
    import lck.ui as ui_mod
    from lck.ui import UI

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(ui_mod, "_windows_vt_on", lambda: True)
    monkeypatch.setenv("WT_SESSION", "1")   # Windows CI: the redrawn screen, as in Windows Terminal
    monkeypatch.setattr(ui_mod.shutil, "get_terminal_size", lambda fallback=None: __import__("os").terminal_size((40, 24)))
    out = Tty()
    UI(stdout=out).say("Then wait three seconds and then plug the power back in with no buttons held, please.")
    text = out.getvalue()
    assert "\x1b[1mplug the power" in text and "\x1b[1mwith no" in text


def test_degoogle_waits_until_android_has_saved_the_disables(tmp_path):
    # e2e run 4: the file is written 5 to 10 s after pm disable-user; a restart before that lost them.
    from lck import system
    base = ("-s", "10.0.0.7:5555")
    pkgs = ["com.a", "com.b"]
    runner = FakeRunner({base + ("shell", "su", "-c", f"'cat {system.RESTRICTIONS}'"): [
        Result(0, '<pkg name="com.a" />'),
        Result(0, '<pkg name="com.a" enabled="3" /><pkg name="com.b" />'),
        Result(0, '<pkg name="com.a" enabled="3" /><pkg name="com.b" enabled="3" />')]})
    c = ctx_for(tmp_path, runner=runner, ip="10.0.0.7")
    assert system.wait_saved(c, pkgs, sleep=lambda s: None) is True
    assert len([x for x in runner.calls if "cat" in " ".join(x)]) == 3


def test_the_restore_bar_doesnt_flash_full_between_partitions(tmp_path):
    # e2e run 4: the finished partition's 100% was read as the next one's.
    log = tmp_path / "mtk.txt"
    log.write_text("Progress: |##########| 100.0% Write: (0x1/0x1)\rWrote parts/a.bin to sector 1 with sector count 2.\n")
    assert mtk.overall(log, [1, 9]) == pytest.approx(10.0)


def test_kiosk_satellite_is_copied_with_a_bar_then_installed(tmp_path, monkeypatch):
    from lck import kiosk
    apk = tmp_path / "ks.apk"
    apk.write_bytes(b"x" * (3 << 20))
    monkeypatch.setattr(kiosk, "fetch_apk", lambda ctx: apk)
    base = ("-s", "10.0.0.7:5555")
    runner = SpawnRunner([FakeProc([True, True], 0, "1 file pushed", None)])
    runner.responses.update({
        base + ("get-state",): Result(0, "device\n"),
        base + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
        base + ("shell", "stat"): Result(0, str(1 << 20) + "\n"),
        base + ("shell", "pm", "install"): Result(0, "Success\n"),
        base + ("shell", "pm", "path"): Result(0, "package:/data/app/ks.apk\n")})
    monkeypatch.setattr(kiosk.time, "sleep", lambda s: None)
    ui = FakeUI()
    kiosk.InstallStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"))
    assert runner.spawned[0][-3:] == ["push", str(apk), kiosk.REMOTE_APK]
    assert any(l == "[note] 1 of 3 MB" for l in ui.lines)


def test_only_the_zip_is_kept_and_the_folder_comes_back_from_it(tmp_path):
    from lck import plan
    folder = _zstd_backup(tmp_path)
    c = ctx_for(tmp_path)
    z = backup.pack(c, folder)
    (folder / "extra-not-in-zip").write_text("x")       # the zip still holds the same SHA256SUMS
    assert backup.drop_folder(c, folder) is True and not folder.exists() and z.exists()
    ui = FakeUI()
    plan.BackupStep().run(ctx_for(tmp_path, ui=ui))      # no BROM: unpacks the zip instead
    assert backup.verify_backup(folder) == [] and any("already saved" in l for l in ui.lines)


def test_a_folder_isnt_dropped_when_the_zip_differs(tmp_path):
    folder = _zstd_backup(tmp_path)
    c = ctx_for(tmp_path)
    backup.pack(c, folder)
    (folder / backup.SUMS).write_text((folder / backup.SUMS).read_text() + "0" * 64 + "  other\n")
    assert backup.drop_folder(c, folder) is False and folder.exists()


def _tty_ui(monkeypatch):
    import io
    import lck.ui as ui_mod
    from lck.ui import UI

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(ui_mod, "_windows_vt_on", lambda: True)
    monkeypatch.setenv("WT_SESSION", "1")   # Windows CI: the redrawn screen, as in Windows Terminal
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = Tty()
    return UI(stdout=out), out


def test_one_box_on_screen_at_a_time_and_the_reason_carries_over(monkeypatch):
    # e2e run 5: the start-normally box and the Magisk box were on screen together.
    ui, out = _tty_ui(monkeypatch)
    ui.screen(["Root"], 1, set())
    ui.physical("First box")
    ui.say("Why the next box")
    ui.physical("Second box")
    last_screen = out.getvalue().rsplit("\x1b[H\x1b[2J", 1)[-1]
    assert "Second box" in last_screen and "First box" not in last_screen and "Why the next box" in last_screen


def test_links_stand_out_on_their_own_line(monkeypatch):
    from lck import doc
    ui, out = _tty_ui(monkeypatch)
    ui.section("What's next", ["Time zones: " + doc("docs/TIMEZONES.md")])
    text = out.getvalue()
    assert "\n" in text.split("docs/TIMEZONES.md", 1)[1].split("https://", 1)[0]
    assert "\x1b[4;38;5;80mhttps://" in text


def test_wifi_entry_is_well_formed_and_escaped():
    import xml.dom.minidom
    from lck import userdata
    from xml.sax.saxutils import escape
    ents = {'"': "&quot;", "'": "&apos;"}
    entry = userdata.NETWORK.format(key=escape('"A & B"WPA_PSK', ents), ssid=escape('"A & B"', ents),
                                    psk=escape('"p<ss>w\'rd"', ents))
    xml.dom.minidom.parseString(userdata.EMPTY_STORE.replace("</NetworkList>", entry + "</NetworkList>").encode())
    with pytest.raises(userdata.UserdataFailed):
        userdata.check_wifi("Home", "short")


def test_no_saved_wifi_asks_before_anything_is_written(tmp_path, monkeypatch):
    from lck import images, userdata
    folder = _zstd_backup(tmp_path)
    monkeypatch.setattr(images, "backup_dir", lambda ctx: folder)
    monkeypatch.setattr(userdata, "wifi_networks", lambda img: 0)
    ui = FakeUI(["Home", "secret-pass"])
    c = ctx_for(tmp_path, ui=ui, runner=FakeRunner(), e2fs=True)   # no real e2fsprogs needed
    images.ensure_wifi(c)
    assert c.state["wifi_new"] == ("Home", "secret-pass")
    assert not any("secret-pass" in l for l in ui.lines)        # the password is never echoed back


def test_step_one_asks_yes_or_no_and_no_means_typed_later(tmp_path, monkeypatch):
    # The person's design (after e2e run 5): a saved network isn't a working one; ask first.
    from lck import images, system
    monkeypatch.setattr(images, "backup_dir", lambda ctx: tmp_path / "none")
    ui = FakeUI(["maybe", "no"])
    c = ctx_for(tmp_path, ui=ui)
    assert system.ask_wifi_ready(c) is False
    assert any("Google Home app before typing **YES**" in l for l in ui.lines)
    c.state["wifi_ready"] = False
    c.ui = FakeUI(["Home", "secret-pass"])
    images.ensure_wifi(c)                                      # no backup look: asked straight away
    assert c.state["wifi_new"] == ("Home", "secret-pass")
    assert system.ask_wifi_ready(ctx_for(tmp_path, ui=FakeUI(["YES"]))) is True


def test_the_wifi_answer_is_saved_but_never_the_password(tmp_path):
    import json
    from lck.steps import _save, _load
    c = ctx_for(tmp_path, wifi_ready=False, wifi_new=("Home", "secret-pass"))
    _save(c, ["prerequisites"])
    text = (c.paths.progress / "clock.json").read_text()
    assert "secret-pass" not in text and json.loads(text)["wifi_ready"] is False
    c2 = ctx_for(tmp_path)
    _load(c2)
    assert c2.state["wifi_ready"] is False


def test_mistyped_wifi_can_be_entered_again_and_only_userdata_is_rewritten(tmp_path, monkeypatch):
    # A mistyped name or password leaves the clock off Wi-Fi, with no adb: loop until it joins.
    from lck import images, system
    import lck.flash as flash_mod
    rebuilt, flashed = [], []
    monkeypatch.setattr(images, "rebuild_userdata", lambda ctx: rebuilt.append(ctx.state["wifi_new"]))
    monkeypatch.setattr(flash_mod, "flash_userdata", lambda ctx: flashed.append(1))
    monkeypatch.setattr(clock, "connect", lambda ctx, tries=3: True)
    monkeypatch.setattr(clock, "require_clock", lambda ctx: None)
    monkeypatch.setattr(clock, "wait_booted", lambda ctx, **kw: None)
    monkeypatch.setattr(clock, "prop", lambda ctx, name: "_a")
    monkeypatch.setattr(system, "channel", lambda ctx: "none-channel")
    ui = FakeUI(["wifi", "Home", "right-password", "10.0.0.9"])
    c = ctx_for(tmp_path, ui=ui, wifi_ready=False, wifi_new=("Hmoe", "wrong-password"))
    system.ConnectStep().run(c)
    assert rebuilt == [("Home", "right-password")] and flashed == [1]
    assert c.state["ip"] == "10.0.0.9"
    assert any("mistyped" in l for l in ui.lines)


def test_no_wifi_retry_offered_when_the_backup_brought_the_wifi(tmp_path, monkeypatch):
    from lck import system
    ui = FakeUI(["wifi", "10.0.0.9"])
    c = ctx_for(tmp_path, ui=ui, wifi_ready=True)
    assert system.ask_ip(c) is None and c.state["ip"] == "10.0.0.9"
    assert not any("mistyped" in l for l in ui.lines)


def test_a_step_that_loses_the_clock_waits_and_starts_again(tmp_path, monkeypatch):
    # e2e run 6: a power cut during the install's copy stopped setup; the clock was back in a minute.
    import subprocess
    from lck.steps import Step, run_steps
    waited = []
    monkeypatch.setattr(clock, "wait_booted", lambda ctx, **kw: waited.append(1))

    class Flaky(Step):
        name, title = "flaky", "Flaky"
        recover = staticmethod(clock.recover_if_lost)
        runs = 0

        def run(self, ctx):
            Flaky.runs += 1
            if Flaky.runs == 1:
                raise subprocess.TimeoutExpired(["adb", "shell", "stat"], 15)
    ui = FakeUI()
    c = ctx_for(tmp_path, ui=ui, ip="10.0.0.7")
    run_steps([Flaky()], c)
    assert Flaky.runs == 2 and waited == [1] and any("Lost the clock" in l for l in ui.lines)


def test_errors_read_as_words_not_command_lists():
    import subprocess
    from lck.cli import plain_error
    from lck.runner import CommandFailed
    msg = plain_error(subprocess.TimeoutExpired(["/x/adb", "-s", "1.2.3.4:5555", "shell", "stat"], 15))
    assert msg.startswith("adb got no answer within 15 seconds") and "[" not in msg
    msg = plain_error(CommandFailed(["/x/adb", "shell", "cp"], Result(1, "line one\ncp: denied\n")))
    assert msg == "adb failed: cp: denied"


def test_a_clock_still_held_from_the_last_session_is_unplugged_first(tmp_path):
    # e2e run 6: mtkclient attached to the leftover download agent and read 0 bytes, 3 times.
    seq = iter([[Mode.DA], [Mode.DA], []])
    ui = FakeUI()
    mtk.release_stale(ctx_for(tmp_path, ui=ui, detect=lambda: next(seq)), sleep=lambda s: None)
    assert any("still held" in l for l in ui.lines) and any("unplugged" in l for l in ui.lines)
    assert not mtk._ok("Dumped sector 0/addr 0x0 with flash size 0x0 as full_user.bin.", ["rf full_user.bin"])
    assert "still held" in mtk.why_failed("flash size 0x0")
    assert "USB connection dropped" in mtk.why_failed("usb.core.USBError: [Errno 19] No such device")


def test_one_ctrl_c_during_brom_only_asks(tmp_path):
    ui = FakeUI()
    c = ctx_for(tmp_path, ui=ui)
    state = {}

    def interrupted(s):
        raise KeyboardInterrupt
    mtk._guarded(c, interrupted, 1, state)               # the first: a question, no stop
    assert any("Ctrl+Shift+C" in l for l in ui.lines)
    with pytest.raises(KeyboardInterrupt):
        mtk._guarded(c, interrupted, 1, state)           # the second, within 5 s: stops


def test_only_the_innermost_spinner_draws(monkeypatch):
    import time as _time
    ui, out = _tty_ui(monkeypatch)
    with ui.busy("Outer"):
        _time.sleep(0.25)
        mark = len(out.getvalue())
        with ui.busy("Inner"):
            _time.sleep(0.35)
        inner_part = out.getvalue()[mark:].split("Inner", 1)[1].split("\n", 1)[0]
    assert "Outer" not in inner_part


def test_what_the_person_is_told_and_answers_is_logged_but_not_secrets():
    import io, logging
    from lck.ui import UI
    stream = io.StringIO()
    logger = logging.getLogger("lck.test-ui-log")
    logger.handlers[:] = [logging.StreamHandler(stream)]
    logger.setLevel(logging.INFO)
    ui = UI(stdin=io.StringIO("10.0.0.7\nhunter22\n"), stdout=io.StringIO(), logger=logger)
    ui.say("That didn't take: the USB connection dropped.")
    ui.ask("Type the clock's address: ")
    ui.ask_secret("Wi-Fi password: ")
    log = stream.getvalue()
    assert "USB connection dropped" in log and "ANSWER 10.0.0.7" in log and "hunter22" not in log


def test_flash_waits_for_fastboot_before_starting_and_never_times_out(tmp_path, monkeypatch):
    # e2e run 7: fbmulti.py gave up after 300 s with the person away; the tool must wait first.
    monkeypatch.setattr(flash, "uv_path", lambda ctx: Path("uv"))
    monkeypatch.setattr(flash, "fetch", lambda id_, ctx: Path("fbmulti.py"))
    waits = []
    monkeypatch.setattr(flash, "wait_for_mode", lambda mode, ctx, prompt: waits.append(mode))
    runner = SpawnRunner([FakeProc([], 1, "no fastboot device after 300 s", None),
                          FakeProc([True], 0, "ALL DONE in 3 s", None)])
    ui = FakeUI()
    flash.fbmulti(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: []),
                  [(tmp_path / "b.img", "boot_a")], title="t", sleep=lambda s: None)
    assert waits == [Mode.FASTBOOT, Mode.FASTBOOT] and len(runner.spawned) == 2


def test_a_failed_flash_doesnt_end_with_a_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(flash, "uv_path", lambda ctx: Path("uv"))
    monkeypatch.setattr(flash, "fetch", lambda id_, ctx: Path("fbmulti.py"))
    runner = SpawnRunner([FakeProc([], 1, "flash failed: boot_a", None)])
    ui = FakeUI()
    with pytest.raises(flash.FlashFailed, match="flash failed"):
        flash.fbmulti(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: [Mode.FASTBOOT]),
                      [(tmp_path / "b.img", "boot_a")], title="Flashing", sleep=lambda s: None)


def test_a_short_password_asks_only_the_password_again(tmp_path):
    from lck import system
    ui = FakeUI(["Home", "short", "long-enough"])
    assert system.ask_wifi(ctx_for(tmp_path, ui=ui)) == ("Home", "long-enough")
    assert sum(l.startswith("[ask] Wi-Fi name") for l in ui.lines) == 1


def test_the_secret_filter_leaves_prose_alone():
    from lck.log import redact
    assert redact("Easier in a browser, for typing your address and token: http://x:2324") \
        == "Easier in a browser, for typing your address and token: http://x:2324"
    assert redact("password=hunter22 token:abc") == "password=*** token:***"


def test_a_usb_pull_is_reported_as_a_usb_pull(tmp_path, monkeypatch):
    # e2e run 7: the reason came from an empty text, "didn't reach BROM", though it had been reading.
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))

    def pulled(ctx, proc, log, *a, **k):
        log.write_text("Dumped sector 1...\nusb.core.USBError: [Errno 19] No such device\n")
        raise mtk.MtkFailed("x")

    def fine(ctx, proc, log, *a, **k):
        proc.wait()
        return "Dumped x"
    seq = [pulled, fine]
    monkeypatch.setattr(mtk, "_follow", lambda *a, **k: seq.pop(0)(*a, **k))
    runner = SpawnRunner([FakeProc([], 1, "", None), FakeProc([], 0, "Dumped x", None)])
    ui = FakeUI()
    mtk.run_in_brom(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: []), ["rf a.bin"], cwd=tmp_path,
                    title="t", sleep=lambda s: None)
    assert any("USB connection dropped" in l for l in ui.lines)


def test_a_rerun_reuses_magisks_patch(tmp_path, monkeypatch):
    from lck import root
    boot = tmp_path / "boot-stock.img"
    boot.write_bytes(b"ANDROID!stock")
    out = tmp_path / "work" / "clock" / "images" / "boot-magisk.img"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"ANDROID!patched")
    apk = tmp_path / "magisk.apk"
    apk.write_bytes(b"magisk")
    monkeypatch.setattr(root, "fetch", lambda id_, ctx: apk)
    out.with_name(out.name + ".from").write_text(root.made_from(boot, apk))
    monkeypatch.setattr(root, "patch_files", lambda ctx: pytest.fail("patched again"))
    assert root.patch(ctx_for(tmp_path), boot) == out


# --- review of a0b8517 (e2e run 7 report) ----------------------------------

def test_leaving_fastboot_during_the_flash_waits_again_at_once(tmp_path, monkeypatch):
    # Review 1: nothing flashed and the clock gone: don't sit 300 s in "Flashing… 0 of 1".
    monkeypatch.setattr(flash, "uv_path", lambda ctx: Path("uv"))
    monkeypatch.setattr(flash, "fetch", lambda id_, ctx: Path("fbmulti.py"))
    waits = []
    monkeypatch.setattr(flash, "wait_for_mode", lambda mode, ctx, prompt: waits.append(mode))
    first = FakeProc([True] * 50, 1, "", None)
    runner = SpawnRunner([first, FakeProc([True], 0, "ALL DONE in 3 s", None)])
    flash.fbmulti(ctx_for(tmp_path, runner=runner, detect=lambda: []), [(tmp_path / "b.img", "boot_a")],
                  title="t", sleep=lambda s: None)
    assert first.returncode == -9 and len(runner.spawned) == 2 and len(waits) == 2


def test_the_typed_wifi_name_stays_out_of_the_log():
    # Review 2: the answer was hidden, but "Added the Wi-Fi network <name>" logged it.
    import inspect
    from lck import images
    src = inspect.getsource(images.UserdataStep.run)
    line = next(l for l in src.splitlines() if "Added the Wi-Fi network" in l)
    assert "log=False" in line


def test_the_secret_filter_still_masks_a_value_after_colon_space():
    # Review 3: "password: hunter2" was masked before the prose fix, and must still be.
    from lck.log import redact
    assert redact("password: hunter22") == "password: ***"
    assert redact("api_token : abc123") == "api_token : ***"
    assert redact("your address and token: http://x:2324") == "your address and token: http://x:2324"


def _follow_with(tmp_path, monkeypatch, timeline):
    """Run mtk._follow against (seconds, mode) steps, on a fake clock."""
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
    ui = FakeUI()
    proc = FakeProc([True] * int(timeline[-1][0] * 4 + 4), 1, "", None)
    c = ctx_for(tmp_path, runner=SpawnRunner([]), ui=ui, detect=detect)
    try:
        mtk._follow(c, proc, tmp_path / "out.txt", "t", sleep, 4 * 3600, ["rf a.bin"], "Hold", None)
    except mtk.MtkFailed:
        pass
    return ui


def test_brom_then_nothing_for_long_still_gets_the_hint(tmp_path, monkeypatch):
    # Review 4: let go as BROM showed, the clock started Android: the hint is for exactly this.
    ui = _follow_with(tmp_path, monkeypatch, [(0, Mode.NONE), (27, Mode.BROM), (28, Mode.NONE), (60, Mode.NONE)])
    assert any("Nothing yet" in l for l in ui.lines)


def test_the_short_gap_before_the_download_agent_gets_no_hint(tmp_path, monkeypatch):
    ui = _follow_with(tmp_path, monkeypatch, [(0, Mode.NONE), (27, Mode.BROM), (28, Mode.NONE), (31, Mode.DA),
                                               (32, Mode.NONE)])
    assert not any("Nothing yet" in l for l in ui.lines)


def test_cast_is_watched_while_waiting_for_magisks_ok(tmp_path, monkeypatch):
    # Review 5: "Link Account" covered "Requires Additional Setup" with nothing to bring Magisk back.
    import contextlib
    from lck import root
    watching = []

    @contextlib.contextmanager
    def watch(ctx, every=4.0):
        watching.append(True)
        yield
        watching.pop()
    seen = []
    monkeypatch.setattr(root, "keep_magisk_in_front", watch)
    monkeypatch.setattr(root, "open_magisk", lambda ctx: None)
    monkeypatch.setattr(root, "settle", lambda ctx: None)
    monkeypatch.setattr(root.clock, "boot_id", lambda ctx: "a")
    monkeypatch.setattr(root.clock, "wait_restarted", lambda *a, **k: seen.append(bool(watching)) or True)
    monkeypatch.setattr(root.clock, "wait_booted", lambda *a, **k: None)
    monkeypatch.setattr(root.clock, "has_root", lambda ctx: True)
    monkeypatch.setattr(root, "shell", lambda *a, **k: "")
    root.RootStep()._first_run(ctx_for(tmp_path))
    assert seen == [True]


def test_passing_through_brom_on_the_way_to_fastboot_isnt_told_to_unplug(tmp_path):
    # Review 6: only the download agent left over from a BROM job needs the unplug first.
    modes = iter([[Mode.BROM], [Mode.BROM], [Mode.FASTBOOT]])
    ui = FakeUI()
    wait_for_mode(Mode.FASTBOOT, ctx_for(tmp_path, ui=ui, detect=lambda: next(modes)), prompt="Hold",
                  sleep=lambda s: None)
    assert not any("Unplug its round power plug first" in l for l in ui.lines)
    modes = iter([[Mode.DA], [Mode.DA], [], [], [Mode.FASTBOOT]])
    ui = FakeUI()
    wait_for_mode(Mode.FASTBOOT, ctx_for(tmp_path, ui=ui, detect=lambda: next(modes)), prompt="Hold",
                  sleep=lambda s: None)
    assert any("Unplug its round power plug first" in l for l in ui.lines)


def test_a_patch_from_another_magisk_isnt_reused(tmp_path, monkeypatch):
    # Review 7: the stamp follows the Magisk file used, not a typed-in version.
    from lck import root
    boot = tmp_path / "boot-stock.img"
    boot.write_bytes(b"ANDROID!stock")
    apk = tmp_path / "magisk.apk"
    apk.write_bytes(b"magisk one")
    monkeypatch.setattr(root, "fetch", lambda id_, ctx: apk)
    out = tmp_path / "work" / "clock" / "images" / "boot-magisk.img"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"ANDROID!patched")
    out.with_name(out.name + ".from").write_text(root.made_from(boot, apk))
    assert root.patch(ctx_for(tmp_path), boot) == out           # same Magisk: reused
    apk.write_bytes(b"magisk two")
    monkeypatch.setattr(root, "patch_files", lambda ctx: (_ for _ in ()).throw(RuntimeError("patching")))
    with pytest.raises(RuntimeError, match="patching"):
        root.patch(ctx_for(tmp_path), boot)                      # another Magisk: patched again


def test_the_cast_watcher_does_nothing_once_stopped(tmp_path, monkeypatch):
    # Review 8: a slow adb call must not stop Cast or relaunch Magisk after the prompt ended.
    import threading
    from lck import root
    calls, release = [], threading.Event()

    def front(ctx, timeout=None):
        release.wait(2)
        return root.CAST_PKG + "/.Setup"
    monkeypatch.setattr(root, "front_app", front)
    monkeypatch.setattr(root, "shell", lambda ctx, *a, **k: calls.append(a) or "")
    with root.keep_magisk_in_front(ctx_for(tmp_path), every=0.01):
        import time as _t
        _t.sleep(0.05)
    release.set()
    _t.sleep(0.1)
    assert calls == []


def test_the_wifi_name_and_password_are_checked_apart():
    # Review 9: no fake password to check a name.
    from lck import userdata
    userdata.check_ssid("Home")
    with pytest.raises(userdata.UserdataFailed):
        userdata.check_ssid("")
    with pytest.raises(userdata.UserdataFailed):
        userdata.check_psk("short")


def test_restores_second_session_shows_one_unplug_box(tmp_path, monkeypatch):
    from lck.modes import BROM_AGAIN
    # Review 10: NEXT_SESSION, then the plain BROM steps, not "BROM mode again, this time to write" again.
    monkeypatch.setattr(mtk, "_python", lambda ctx: ["python"])
    monkeypatch.setattr(mtk, "script", lambda ctx: Path("mtk.py"))
    seq = iter([[Mode.DA], []] + [[]] * 20)
    runner = SpawnRunner([FakeProc([], 0, "Wrote x.bin to sector 0", None)])
    ui = FakeUI()
    mtk.run_in_brom(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: next(seq)), ["w boot_a x.bin"],
                    cwd=tmp_path, title="t", prompt=BROM_AGAIN, held_message=mtk.NEXT_SESSION,
                    sleep=lambda s: None)
    boxes = [l for l in ui.lines if l.startswith("[physical]")]
    assert sum("this time to write" in b for b in boxes) == 1


def test_power_pulled_while_writing_is_flashed_again_and_said_so(tmp_path, monkeypatch):
    # Writing starts at "-> flash:": a pull after it isn't "before anything was written", and the flash
    # starts again once the clock is back in fastboot, rather than stopping setup.
    monkeypatch.setattr(flash, "uv_path", lambda ctx: Path("uv"))
    monkeypatch.setattr(flash, "fetch", lambda id_, ctx: Path("fbmulti.py"))
    waits = []
    monkeypatch.setattr(flash, "wait_for_mode", lambda mode, ctx, prompt: waits.append(mode))
    pulled = "-> download:02000000\n  <- DATA02000000\n  <- OKAY\n-> flash:boot_a\nusb.core.USBError: [Errno 19]\n"
    runner = SpawnRunner([FakeProc([True], 1, pulled, None), FakeProc([True], 0, "ALL DONE in 3 s", None)])
    ui = FakeUI()
    flash.fbmulti(ctx_for(tmp_path, runner=runner, ui=ui, detect=lambda: []), [(tmp_path / "b.img", "boot_a")],
                  title="t", sleep=lambda s: None)
    assert len(runner.spawned) == 2 and len(waits) == 2
    assert any("while writing" in l for l in ui.lines)
    assert not any("before anything was written" in l for l in ui.lines)


# --- e2e run 8 ----------------------------------------------------------------

def test_a_usb_drop_while_writing_doesnt_say_nothing_was_written():
    # e2e run 8: restore lost USB 7 minutes into its writes and said "Nothing was written".
    text = ("Write: (0x6D600000/0x13FE56E00), 10.22 MB/s\nDeviceClass - USBError(5, 'Input/Output Error')\n")
    reason = mtk.why_failed(text)
    assert "Nothing was written" not in reason and "partly written" in reason
    assert "Nothing was written" in mtk.why_failed("Dumped sector 1\nusb.core.USBError: [Errno 19]")


def test_an_adb_copy_cut_by_a_power_pull_counts_as_losing_the_clock():
    # e2e run 8: a power pull during the Magisk patch's copy stopped setup instead of waiting.
    from lck.runner import CommandFailed
    e = CommandFailed(["adb", "push"], Result(1, "adb: error: failed to read copy response: EOF\n"))
    assert clock.lost(e)


def test_the_nothing_yet_hint_comes_once_per_wait(tmp_path):
    # e2e run 8: it came again 30 s after every "Still waiting" reminder.
    ui = _fastboot_wait(tmp_path, [(t, []) for t in range(0, 700, 10)] + [(700, [Mode.FASTBOOT])])
    assert sum("Nothing yet" in l for l in ui.lines) == 1
    assert sum("Still waiting" in l for l in ui.lines) >= 2


# --- macOS: administrator rights -------------------------------------------

def test_on_a_mac_without_sudo_it_says_to_use_sudo_before_anything(tmp_path, monkeypatch, capsys):
    # A tester's Mac needed sudo: macOS's own driver holds the clock in BROM mode, and only root takes it over.
    from lck import cli
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["./lck/lck", "setup"])
    assert cli.main(["setup", "--workdir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "sudo ./lck/lck setup" in err
    assert not (tmp_path / "logs").exists()          # nothing made, so nothing owned by the wrong account


def test_on_a_mac_dry_run_and_status_need_no_sudo(tmp_path, monkeypatch):
    from lck import cli
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 501, raising=False)
    assert cli.needs_sudo(cli._parser().parse_args(["setup", "--dry-run"])) is False
    assert cli.needs_sudo(cli._parser().parse_args(["status"])) is False
    assert cli.needs_sudo(cli._parser().parse_args(["backup"])) is True
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0, raising=False)
    assert cli.needs_sudo(cli._parser().parse_args(["backup"])) is False


def test_from_source_on_a_mac_the_sudo_command_is_python_m_lck(tmp_path, monkeypatch, capsys):
    from lck import cli
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["/x/lck/__main__.py", "backup", "--name", "c1"])
    monkeypatch.setattr(cli.sys, "executable", "/x/.venv/bin/python")
    assert cli.main(["backup", "--name", "c1", "--workdir", str(tmp_path)]) == 2
    assert "sudo /x/.venv/bin/python -m lck backup --name c1" in capsys.readouterr().err
