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
                state={"progress_id": "clock", "tools": Tools(adb=Path("adb"), fastboot=Path("fastboot")),
                       **state})
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
    if subprocess.run(["which", "zstd"], capture_output=True).returncode:
        pytest.skip("zstd isn't installed")
    parts = [("misc", 40, 2), ("boot_a", 42, 4), ("userdata", 46, 10)]
    raw = bytearray(make_gpt(parts))
    raw += bytes(56 * 512 - len(raw))
    raw[42 * 512:46 * 512] = b"B" * (4 * 512)
    raw[46 * 512:56 * 512] = b"U" * (10 * 512)
    folder = tmp_path / "backup" / "clock"
    folder.mkdir(parents=True)
    (folder / backup.RAW).write_bytes(bytes(raw))
    subprocess.run(["zstd", "-q", str(folder / backup.RAW), "-o", str(folder / backup.ZST)], check=True)
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
                     "root", "degoogle", "keyboard", "kiosk-install", "kiosk-grants", "kiosk-settings",
                     "verify"]


def test_missing_linux_tools_are_named_with_their_package():
    missing = plan.missing_tools(which=lambda t: None if t == "img2simg" else "/usr/bin/" + t)
    assert len(missing) == 1 and "img2simg" in missing[0] and "libsparse" in missing[0]


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
    log.write_text("Wrote a.bin to sector 1\nProgress: 50.0% Write\n")
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


def test_degoogle_needs_root_and_hides_cast_with_a_boot_script(tmp_path, monkeypatch):
    from lck import system
    base = ("-s", "10.0.0.7:5555")
    no_root = FakeRunner({base + ("get-state",): Result(0, "device\n"),
                          base + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
                          base + ("shell", "su", "-c", "id"): Result(127, "su: not found\n")})
    with pytest.raises(clock.SetupFailed, match="needs root"):
        system.DegoogleStep().run(ctx_for(tmp_path, runner=no_root, ip="10.0.0.7"))
    lists = [Result(0, f"package:{system.CAST}\npackage:com.google.assistant.core\n"),
             Result(0, "package:com.google.assistant.core\n")]
    runner = FakeRunner({base + ("get-state",): Result(0, "device\n"),
                         base + ("shell", "getprop", "ro.product.device"): Result(0, "mt8167s_som\n"),
                         base + ("shell", "su", "-c", "id"): Result(0, "uid=0(root)\n"),
                         base + ("shell", "pm", "list", "packages", "--user", "0"): lists,
                         base + ("shell", "pm", "list", "packages", "-d", "--user", "0"):
                             Result(0, "package:com.google.assistant.core\n"),
                         base + ("shell", "pidof"): Result(0, "123\n")})
    monkeypatch.setattr(clock, "reboot", lambda ctx, why="": None)
    ui = FakeUI()
    system.DegoogleStep().run(ctx_for(tmp_path, runner=runner, ui=ui, ip="10.0.0.7"))
    su = [c for c in runner.calls if "su" in c and "-c" in c and "id" not in c]
    assert any(system.HIDE_SCRIPT in " ".join(c) for c in su)
    assert not any("uninstall" in c for c in runner.calls)
    assert any("blank" in l for l in ui.lines)
    assert "mount -o bind /data/adb/empty-cast /oem/app/com.google.android.apps.mediashell" in system.HIDE_CAST


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
    assert any("still in fastboot" in l for l in ui.lines)


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
