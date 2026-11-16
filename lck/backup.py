"""Full backup over BROM, and restoring it.

The backup is the clock's whole eMMC user area (every partition, userdata
included: the setup rebuilds the clock from it), plus the two boot areas
(the preloader). It's compressed with zstd, about 0.7 GB, and packed with its
checksums into one zip. It holds data unique to that clock, and the Wi-Fi
password saved on it: it must never be shared.

A backup restores only to the clock it came from: restore first reads the
clock's partition table and compares its disk GUID and layout. Restore
writes back what a conversion (or Alpine on slot B) changes, never the
bootloader chain or the clock's own calibration and keys (lck/mtk.py
NEVER_WRITE), which nothing here ever writes.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from lck import gpt, mtk
from lck.ui import choose

RAW = "full_user.bin"
ZST = "full_user.bin.zst"
GPT_FILE = "gpt.bin"
BOOT_AREAS = ["boot1.bin", "boot2.bin"]
SUMS = "SHA256SUMS"
CHUNK = 4 << 20
# What restore writes. The rest of the disk (bootloader chain, keys, calibration,
# factory data, logo) is never changed by the conversion, so it's left alone.
RESTORE_SET = ["boot_a", "boot_b", "vbmeta_a", "vbmeta_b", "vendor_a", "vendor_b", "system_a",
               "system_b", "oem_a", "oem_b", "misc", "frp", "metadata", "userdata"]
SKIP_WARNING = ("The setup rebuilds the clock from this backup's user data, so without one it can only "
                "go on if the clock is already converted.")


class BackupMismatch(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sums(folder: Path) -> dict[str, str]:
    f = Path(folder) / SUMS
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            out[name.strip().lstrip("*")] = digest
    return out


def verify_backup(folder: Path) -> list[str]:
    folder = Path(folder)
    if not (folder / ZST).exists():
        return [f"no {ZST} in {folder}"]
    sums = read_sums(folder)
    if not sums:
        return ["no SHA256SUMS: the backup never finished"]
    if RAW not in sums:
        return [f"SHA256SUMS has no line for {RAW}"]
    problems = []
    for name, digest in sums.items():
        f = folder / name
        if name == RAW:
            continue      # only the compressed copy is kept; restore checks it as it unpacks
        if not f.exists():
            problems.append(f"{name} is missing")
        elif sha256(f) != digest:
            problems.append(f"{name} doesn't match its checksum")
    return problems


def head_of(folder: Path) -> bytes:
    """The backup's partition table (gpt.bin, or the start of the compressed image)."""
    f = Path(folder) / GPT_FILE
    if f.exists():
        return f.read_bytes()
    p = subprocess.Popen(["zstd", "-dc", "--long=27", str(Path(folder) / ZST)], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    data = p.stdout.read(gpt.GPT_BYTES)
    p.kill()
    p.wait()
    return data


def backup(ctx, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if not verify_backup(out):
        ctx.ui.keep(f"There's already a complete backup in {out}.")
        return
    (out / RAW).unlink(missing_ok=True)
    mtk.run_in_brom(ctx, [f"rf {RAW}", "r preloader boot1.bin --parttype boot1",
                          "r preloader boot2.bin --parttype boot2"],
                    cwd=out, title="Backing up the clock: the whole disk, about 40 minutes")
    raw = out / RAW
    head = open(raw, "rb").read(gpt.GPT_BYTES)
    parts = gpt.read_gpt(head)
    if raw.stat().st_size < parts[-1].offset + parts[-1].size:
        raise BackupMismatch("The backup came out shorter than the clock's disk. Run again to redo it.")
    (out / GPT_FILE).write_bytes(head)
    with ctx.ui.busy("Checking the backup"):
        sums = {RAW: sha256(raw)}
    with ctx.ui.busy(f"Compressing the backup ({raw.stat().st_size >> 30} GB to about 0.7 GB)"):
        ctx.runner.run(["zstd", "-q", "-f", "-T0", "-10", "--long=27", raw, "-o", out / ZST], timeout=3 * 3600)
        ctx.runner.run(["zstd", "-q", "-t", "--long=27", out / ZST], timeout=3600)
    for name in [ZST, GPT_FILE, *BOOT_AREAS]:
        sums[name] = sha256(out / name)
    (out / SUMS).write_text("".join(f"{d}  {n}\n" for n, d in sums.items()), encoding="utf-8")
    raw.unlink()
    (out / "mtk-output.txt").unlink(missing_ok=True)
    ctx.ui.keep(f"Backup complete: {out}. Keep it private: it holds data unique to this clock, and its "
                "Wi-Fi password.")


def extract(ctx, folder: Path, names: list[str], dest: Path) -> dict[str, Path]:
    """Pull partitions out of the compressed image, checking the whole image against
    its checksum on the way. Reuses files already pulled from this same backup."""
    folder, dest = Path(folder), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    sums = read_sums(folder)
    marker = dest / ".from"
    table = gpt.by_name(head_of(folder))
    missing = [n for n in names if n not in table]
    if missing:
        raise BackupMismatch(f"This backup has no {', '.join(missing)} partition: it isn't from this clock "
                             "model.")
    files = {n: dest / f"{n}.bin" for n in names}
    if (marker.exists() and marker.read_text() == sums.get(RAW)
            and all(f.exists() and f.stat().st_size == table[n].size for n, f in files.items())):
        return files
    marker.unlink(missing_ok=True)
    wanted = sorted((table[n] for n in names), key=lambda p: p.first_lba)
    end = wanted[-1].offset + wanted[-1].size
    total = max(p.offset + p.size for p in table.values())
    h = hashlib.sha256()
    proc = subprocess.Popen(["zstd", "-dc", "--long=27", str(folder / ZST)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    pos = 0
    with ctx.ui.busy("Unpacking the backup") as job:
        outs = {p.name: open(files[p.name], "wb") for p in wanted}
        try:
            while True:
                chunk = proc.stdout.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                for p in wanted:
                    a, b = max(pos, p.offset), min(pos + len(chunk), p.offset + p.size)
                    if a < b:
                        outs[p.name].write(chunk[a - pos:b - pos])
                pos += len(chunk)
                job.note(f"{min(pos, total) >> 20} of {total >> 20} MB", min(pos, total), total)
        finally:
            for f in outs.values():
                f.close()
            proc.wait()
    if pos < end:
        raise BackupMismatch("The backup image is cut short.")
    if sums.get(RAW) and h.hexdigest() != sums[RAW]:
        for f in files.values():
            f.unlink(missing_ok=True)
        raise BackupMismatch("The backup doesn't match its checksum: it's damaged. Nothing was written.")
    marker.write_text(sums.get(RAW, ""))
    return files


def read_clock_gpt(ctx, dest: Path) -> bytes:
    """The clock's own partition table, read in BROM (one power-cycle)."""
    dest = Path(dest)
    (dest / "clock-gpt.bin").unlink(missing_ok=True)
    mtk.run_in_brom(ctx, ["rs 0 34 clock-gpt.bin"], cwd=dest, title="Reading the clock's partition table")
    return (dest / "clock-gpt.bin").read_bytes()


def restore(ctx, folder: Path, work: Path) -> None:
    folder, work = Path(folder), Path(work)
    problems = verify_backup(folder)
    if problems:
        raise BackupMismatch("This backup can't be restored: " + "; ".join(problems))
    files = extract(ctx, folder, RESTORE_SET, work / "parts")
    ctx.ui.say("First the tool reads the clock's partition table, to make sure this backup is from this "
               "clock. Nothing is written yet.")
    clock = read_clock_gpt(ctx, work)
    if gpt.signature(clock) != gpt.signature(head_of(folder)):
        raise BackupMismatch("This backup is from a different clock. Each backup only restores to its own "
                             "clock. Nothing was written.")
    ctx.ui.redraw()
    ctx.ui.say("It's this clock's backup.")
    ctx.ui.say("WARNING: this overwrites the clock's system, both slots, and its user data with the "
               "backup: Kiosk Satellite, root, Alpine on slot B and everything since go. The bootloader, "
               "its keys and its calibration are never written. It takes about 40 minutes, in BROM mode "
               "again.")
    if not choose(ctx.ui, "Type restore to continue, or press Enter to stop: ", "restore"):
        raise BackupMismatch("Restore cancelled. Nothing was changed.")
    commands = [f"w {n} {files[n].relative_to(work)}" for n in RESTORE_SET]
    mtk.run_in_brom(ctx, commands, cwd=work, title="Writing the backup to the clock, about 40 minutes")
    ctx.ui.say("Every partition is written.")


def pack(ctx, out: Path) -> Path:
    """The whole backup as one zip next to its folder: one file to copy somewhere safe.
    Stored, not compressed again: the image is already zstd."""
    out = Path(out)
    dest = out.with_name(out.name + ".zip")
    part = dest.with_name(dest.name + ".part")
    with ctx.ui.busy(f"Packing the backup into one file, {dest.name}"):
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
            for f in sorted(out.iterdir()):
                if f.is_file():
                    z.write(f, f.name)
        os.replace(part, dest)
    return dest


def unpack(ctx, archive: Path, dest_root: Path) -> Path:
    """Unpack a packed backup into dest_root/<name>, unless it's already there."""
    archive = Path(archive)
    dest = Path(dest_root) / archive.stem
    with zipfile.ZipFile(archive) as z:
        try:
            same = (dest / SUMS).read_bytes() == z.read(SUMS)
        except (OSError, KeyError):
            same = False
        if same and not verify_backup(dest):
            return dest
        for i in z.infolist():
            p = Path(i.filename)
            if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
                raise BackupMismatch(f"This backup file holds a path outside its folder ({i.filename}).")
        need = sum(i.file_size for i in z.infolist())
        Path(dest_root).mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest_root).free
        if free < need + (1 << 30):
            raise BackupMismatch(f"Not enough free space to unpack the backup: it needs {(need >> 30) + 2} "
                                 f"GB, and {free >> 30} GB is free.")
        if dest.exists():
            shutil.rmtree(dest)
        with ctx.ui.busy(f"Unpacking {archive.name}"):
            z.extractall(dest)
    return dest


def confirm_skip(ui) -> bool:
    ui.say("WARNING: skipping the backup. " + SKIP_WARNING)
    return choose(ui, "Type skip to continue without a backup, or press Enter to make one: ", "skip")
