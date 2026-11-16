"""lck: turn a stock Lenovo Smart Clock (gen 1) into a degoogled Home Assistant kiosk."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lck import __version__, backup, clock, log, plan
from lck.downloads import fetch
from lck.runner import RealRunner, Tools, platform_id
from lck.steps import Context, Paths, run_steps


def _ip(text: str) -> str:
    if not clock.valid_ip(text):
        raise argparse.ArgumentTypeError(f"{text!r} isn't an address like 10.0.0.23")
    return text.strip()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lck", description=__doc__)
    p.add_argument("--version", action="version", version=f"lck {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--workdir", type=Path, default=Path.cwd(),
                        help="folder for downloads, backups, logs and progress (default: here)")
        sp.add_argument("--name", default="clock",
                        help="a name for this clock, so several can be done one after another")

    s = sub.add_parser("setup", help="convert a clock, from stock to working kiosk")
    common(s)
    s.add_argument("--ip", type=_ip, help="the clock's address on your Wi-Fi, once it has one")
    s.add_argument("--dry-run", action="store_true", help="list the steps, change nothing")
    s.add_argument("--from", dest="start_from", metavar="STEP", help="start at STEP")
    s.add_argument("--restart", action="store_true", help="forget saved progress")
    s.add_argument("--skip-backup", action="store_true",
                   help="skip the backup (asks first; only for a clock that's already converted)")

    b = sub.add_parser("backup", help="back up the whole clock, at any time (BROM)")
    common(b)

    r = sub.add_parser("restore", help="put a backup back on the clock it came from (BROM)")
    common(r)
    r.add_argument("backup_dir", type=Path)

    st = sub.add_parser("status", help="show which setup steps are done")
    common(st)
    return p


def _context(args) -> Context:
    from lck.ui import UI
    paths = Paths(args.workdir)
    logger = log.setup(paths.logs / f"{args.name}.log")
    ctx = Context(runner=RealRunner(logger=logger), ui=UI(), paths=paths,
                  state={"progress_id": args.name, "log": logger})
    if getattr(args, "ip", None):
        ctx.state["ip"] = args.ip
    ctx.state["tools"] = Tools.locate(paths.cache)
    return ctx


def _status(args) -> int:
    f = Paths(args.workdir).progress / f"{args.name}.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    done = data.get("done", [])
    for s in plan.setup_steps():
        print(f"  [{'x' if s.name in done else ' '}] {s.name:16} {s.title}")
    if data.get("ip"):
        print(f"\n  The clock's address: {data['ip']}")
    return 0


def _tools(ctx) -> None:
    """Every tool first, so nothing downloads while the clock waits in BROM."""
    from lck import mtk
    from lck.downloads import load_manifest, platform_entry
    from lck.runner import platform_arch
    manifest = ctx.state.get("manifest") or load_manifest()
    fetch(f"platform-tools-{platform_id()}", ctx)
    fetch(platform_entry("uv", manifest, platform_arch()), ctx)
    ctx.state["tools"] = Tools.locate(ctx.paths.cache)
    mtk.prepare(ctx)


def _backup(args) -> int:
    ctx = _context(args)
    ctx.ui.title("Back up the clock")
    out = ctx.paths.backup / args.name
    if not backup.verify_backup(out):
        ctx.ui.say(f"There's already a complete backup for --name {args.name} in {out}. Give this one "
                   "another --name, or move that one somewhere safe first.")
        return 1
    ctx.ui.say("This copies the clock's whole disk in BROM mode: about 40 minutes, and 9 GB free while it "
               "works (about 0.7 GB when done). Nothing on the clock is changed.")
    _tools(ctx)
    backup.backup(ctx, out)
    z = backup.pack(ctx, out)
    ctx.ui.ok("Backed up.")
    ctx.ui.alert(f"SAVE YOUR BACKUP: copy {z} somewhere safe, off this computer if you can, and keep it "
                 "private. It holds this clock's own keys and your Wi-Fi password.")
    ctx.ui.section("What's next", ["Unplug the clock's power and plug it back in, with no buttons held: it "
                                   "starts normally.",
                                   f"To put this backup back: lck restore {z}"])
    return 0


def _restore(args) -> int:
    folder = args.backup_dir
    if not folder.exists():
        print(f"Can't restore this backup: no backup at {folder}", file=sys.stderr)
        return 2
    ctx = _context(args)
    ctx.ui.title("Restore a backup")
    ctx.ui.say("This puts a backup back on the clock it came from: its system on both slots and its user "
               "data. It takes about 45 minutes, in BROM mode twice: once to check it's the right clock, "
               "once to write. You'll type restore to confirm, after the check.")
    if folder.is_file() and folder.suffix.lower() == ".zip":
        try:
            folder = backup.unpack(ctx, folder, ctx.paths.root / "restore")
        except Exception as e:
            print(f"Can't restore this backup: {e}", file=sys.stderr)
            return 2
    with ctx.ui.busy("Checking the backup"):
        problems = backup.verify_backup(folder)
    if problems:
        print("Can't restore this backup: " + "; ".join(problems), file=sys.stderr)
        return 2
    _tools(ctx)
    backup.restore(ctx, folder, ctx.paths.root / "work" / f"restore-{args.name}")
    ctx.ui.ok("Restored.")
    ctx.ui.section("What's next", [
        "Unplug the clock's power and plug it back in, with no buttons held. It starts the system that "
        "was on it when the backup was made: the first start can take a few minutes.",
        "The bootloader stays unlocked. Stock software starts fine that way."])
    return 0


def _setup(args) -> int:
    ctx = _context(args)
    ctx.dry_run = args.dry_run
    ctx.state["skip_backup"] = args.skip_backup
    fresh = args.restart or not (ctx.paths.progress / f"{ctx.state.get('progress_id')}.json").exists()
    if fresh and not args.dry_run and args.start_from is None:
        ctx.ui.intro("Setup takes about 1 hour 30 minutes and needs 20 GB free: about 200 MB of downloads, "
                     "a backup of the whole clock (40 minutes, 0.7 GB kept), and the images built from it. "
                     "The tool tells you each time it needs you at the clock.")
    run_steps(plan.setup_steps(), ctx, start_from=args.start_from, restart=args.restart)
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        return {"setup": _setup, "backup": _backup, "restore": _restore, "status": _status}[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue where you left off.", file=sys.stderr)
        return 130
    except Exception as e:  # every failure ends in plain words, not a traceback
        print(f"\nStopped: {e}", file=sys.stderr)
        _print_log_tail(args)
        print("Run the same command again to retry this step.", file=sys.stderr)
        return 1


def _print_log_tail(args, lines: int = 12) -> None:
    """The end of the run log, so the reason is on screen. The log is already redacted;
    the home folder becomes ~ so a pasted copy doesn't carry the user's name."""
    name, workdir = getattr(args, "name", None), getattr(args, "workdir", None)
    if not name or workdir is None:
        return
    f = Paths(workdir).logs / f"{name}.log"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    home = str(Path.home())
    tail = [l for l in text.splitlines() if l.strip()][-lines:]
    if not tail:
        return
    print("\nThe last lines of the log:", file=sys.stderr)
    for line in tail:
        print("  " + line.replace(home, "~"), file=sys.stderr)
    print("Full log: " + str(f).replace(home, "~"), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
