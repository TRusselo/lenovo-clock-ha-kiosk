"""Full backup over BROM, and restoring it.

The backup is the clock's whole eMMC user area (every partition, userdata
included: the setup rebuilds the clock from it), plus the two boot areas
(the preloader). It's compressed with zstd (the library, not the program), about 0.7 GB, and packed with its
checksums into one zip. It holds data unique to that clock, and the Wi-Fi
password saved on it: it must never be shared.

A backup restores only to the clock it came from: restore first reads the
clock's partition table and compares its disk GUID and layout. Restore
writes back what a conversion changes, never the
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
from lck.modes import BROM_AGAIN
from lck.ui import choose

RAW = "full_user.bin"
ZST = "full_user.bin.zst"
GPT_FILE = "gpt.bin"
BOOT_AREAS = ["boot1.bin", "boot2.bin"]
BOOT_AREA_SIZE = 0x400000
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


# zstd through the Python library, the same on every OS (no zstd program needed). The window
# matches `zstd --long=27`, which made the backups taken before this.
WINDOW_LOG = 27


def zst_reader(path: Path):
    """A stream of the decompressed image."""
    import zstandard
    return zstandard.ZstdDecompressor(max_window_size=1 << WINDOW_LOG).stream_reader(open(path, "rb"),
                                                                                   closefd=True)


def compress(src: Path, dest: Path, job=None) -> None:
    """`zstd -10 --long=27`, as a library call, with progress."""
    import zstandard
    params = zstandard.ZstdCompressionParameters.from_level(10, window_log=WINDOW_LOG, enable_ldm=True,
                                                            threads=-1)
    total = Path(src).stat().st_size
    part = Path(dest).with_name(Path(dest).name + ".part")
    with open(src, "rb") as fin, open(part, "wb") as fout:
        with zstandard.ZstdCompressor(compression_params=params).stream_writer(fout, size=total,
                                                                             closefd=False) as w:
            done = 0
            for chunk in iter(lambda: fin.read(CHUNK), b""):
                w.write(chunk)
                done += len(chunk)
                if job is not None:
                    job.note(f"{done >> 20} of {total >> 20} MB", done, total)
    os.replace(part, dest)


def zst_sha256(path: Path) -> str:
    """The SHA-256 of the decompressed image: checks the compressed copy reads back whole."""
    h = hashlib.sha256()
    with zst_reader(path) as r:
        for chunk in iter(lambda: r.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def head_of(folder: Path) -> bytes:
    """The backup's partition table (gpt.bin, or the start of the compressed image)."""
    f = Path(folder) / GPT_FILE
    if f.exists():
        return f.read_bytes()
    with zst_reader(Path(folder) / ZST) as r:
        return r.read(gpt.GPT_BYTES)


def backup(ctx, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if not verify_backup(out):
        ctx.ui.keep(f"There's already a complete backup in {out}.")
        return
    raw = out / RAW
    if not _complete_dump(out):
        # A dump that came out whole is kept if a later step fails: redoing it costs 40
        # minutes at the clock.
        raw.unlink(missing_ok=True)
        mtk.run_in_brom(ctx, [f"rf {RAW}", "r preloader boot1.bin --parttype boot1",
                              "r preloader boot2.bin --parttype boot2"],
                        cwd=out, title="Backing up the clock: the whole disk, about 35 minutes",
                        weights=[300, 1, 1])
        if not _complete_dump(out):
            raise BackupMismatch("The backup came out incomplete (the disk image is short, or a boot area "
                                 "is missing). Run the same command again to redo it.")
    head = open(raw, "rb").read(gpt.GPT_BYTES)
    (out / GPT_FILE).write_bytes(head)
    with ctx.ui.busy("Checking the backup"):
        sums = {RAW: sha256(raw)}
    with ctx.ui.busy(f"Compressing the backup ({raw.stat().st_size >> 30} GB to about 0.7 GB)") as job:
        compress(raw, out / ZST, job)
    with ctx.ui.busy("Checking the compressed backup reads back"):
        if zst_sha256(out / ZST) != sums[RAW]:
            raise BackupMismatch("The compressed backup doesn't read back the same. Run the same command again.")
    for name in [ZST, GPT_FILE, *BOOT_AREAS]:
        sums[name] = sha256(out / name)
    (out / SUMS).write_text("".join(f"{d}  {n}\n" for n, d in sums.items()), encoding="utf-8")
    raw.unlink()
    (out / "mtk-output.txt").unlink(missing_ok=True)
    ctx.ui.keep(f"Backup complete: {out}. Keep it private: it holds data unique to this clock, and its "
                "Wi-Fi details, if it has any.")


def _complete_dump(out: Path) -> bool:
    raw = Path(out) / RAW
    try:
        parts = gpt.read_gpt(open(raw, "rb").read(gpt.GPT_BYTES))
    except (OSError, ValueError):
        return False
    if raw.stat().st_size < parts[-1].offset + parts[-1].size:
        return False
    return all((Path(out) / n).exists() and (Path(out) / n).stat().st_size == BOOT_AREA_SIZE
               for n in BOOT_AREAS)


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
    reader = zst_reader(folder / ZST)
    pos = 0
    with ctx.ui.busy("Unpacking the backup") as job:
        outs = {p.name: open(files[p.name], "wb") for p in wanted}
        try:
            while True:
                chunk = reader.read(CHUNK)
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
            reader.close()
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
    # `ro` (bytes), not `rs`: at the pinned commit, rs has no file argument.
    mtk.run_in_brom(ctx, [f"ro 0 {gpt.GPT_BYTES} clock-gpt.bin"], cwd=dest,
                    title="Reading the clock's partition table")
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
               "backup: anything installed or set up since goes. The bootloader, "
               "its keys and its calibration are never written. It takes about 15 minutes, in BROM mode "
               "again.")
    if not choose(ctx.ui, "Type restore to continue, or press Enter to stop: ", "restore"):
        raise BackupMismatch("Restore cancelled. Nothing was changed.")
    # Forward slashes on every system: mtkclient's multi splits on spaces, and the tests expect one form.
    commands = [f"w {n} {files[n].relative_to(work).as_posix()}" for n in RESTORE_SET]
    ctx.ui.redraw()
    mtk.run_in_brom(ctx, commands, cwd=work, title="Writing the backup to the clock, about 15 minutes",
                    prompt=BROM_AGAIN, held_message=mtk.NEXT_SESSION,
                    weights=[files[n].stat().st_size for n in RESTORE_SET])
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


def drop_folder(ctx, out: Path) -> bool:
    """After a successful setup, keep only the zip: the folder is the same backup twice. Removed
    only when the zip next to it holds this same backup (same checksum list) and reads back whole.
    Returns whether it was removed."""
    out = Path(out)
    z = out.with_name(out.name + ".zip")
    if not (out.is_dir() and z.is_file()):
        return False
    try:
        with zipfile.ZipFile(z) as zf:
            if zf.read(SUMS) != (out / SUMS).read_bytes():
                return False
            with ctx.ui.busy(f"Checking {z.name}"):
                if zf.testzip() is not None:
                    return False
    except (OSError, KeyError, zipfile.BadZipFile, EOFError):
        return False
    shutil.rmtree(out)
    return True


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
