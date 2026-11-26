"""lck: turn a stock Lenovo Smart Clock (gen 1) into a degoogled Home Assistant kiosk."""
from __future__ import annotations

import argparse
import json
import os
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

    w = sub.add_parser("wifi", help="put a converted clock on another Wi-Fi network, or give it a new password")
    common(w)
    w.add_argument("--ip", type=_ip, help="the clock's address, if it's on Wi-Fi now")
    w.add_argument("--usb", action="store_true",
                   help="use USB (BROM and fastboot mode) even if the clock answers over Wi-Fi")

    st = sub.add_parser("status", help="show which setup steps are done")
    common(st)

    # For the release build's smoke test: the libraries --version and --dry-run never load.
    sub.add_parser("selfcheck", help=argparse.SUPPRESS)
    return p


def _context(args) -> Context:
    from lck.ui import UI
    paths = Paths(args.workdir)
    logger = log.setup(paths.logs / f"{args.name}.log")
    logger.info("%s %s", RUN_MARK, " ".join(sys.argv[1:]) or args.command)
    logger.info("%s", about_this_computer())
    ctx = Context(runner=RealRunner(logger=logger), ui=UI(logger=logger), paths=paths,
                  state={"progress_id": args.name, "log": logger})
    if getattr(args, "ip", None):
        ctx.state["ip"] = args.ip
    ctx.state["tools"] = Tools.locate(paths.cache)
    return ctx


RUN_MARK = "=== lck run:"


def about_this_computer() -> str:
    """The first line of every run in the log, for a report: lck's version, the OS and its version."""
    import platform
    build = "program" if getattr(sys, "frozen", False) else "from source"
    return (f"lck {__version__} ({build}) on {platform.platform()} {platform.machine()}, "
            f"Python {platform.python_version()}")


def _selfcheck(args) -> int:
    """zstd both ways, one sparse piece, and the libusb backend: what a frozen build could miss."""
    import tempfile
    from lck import sparse
    from lck.modes import _usb_backend
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.img"
        raw.write_bytes(b"clock" * 3000 + bytes(8192))
        backup.compress(raw, Path(tmp) / "raw.zst")
        assert backup.zst_sha256(Path(tmp) / "raw.zst") == backup.sha256(raw), "zstd round trip"
        pieces = sparse.write_pieces(str(raw), str(Path(tmp) / "piece"), 1 << 20)
        assert pieces and sparse.declared_size(open(pieces[0], "rb").read(28)) == raw.stat().st_size // 4096 * 4096
    assert _usb_backend() is not None, "no libusb backend"
    if getattr(sys, "frozen", False):
        from lck import e2fs, userdata
        import subprocess
        assert e2fs.bundled() is not None, "no bundled e2fsprogs"
        e2fs.locate(None)
        r = subprocess.run([userdata.DEBUGFS, "-V"], capture_output=True, text=True, env=userdata.ENV)
        assert "debugfs" in (r.stdout + r.stderr), "bundled debugfs doesn't run: " + r.stdout + r.stderr
        # -R starts debugfs's command library, which -V doesn't: where it loaded the host's libreadline, the
        # static Linux build crashed here (e2e run 9). The release job installs libreadline to catch that.
        r = subprocess.run([userdata.DEBUGFS, "-R", "help"], capture_output=True, text=True, env=userdata.ENV)
        assert r.returncode == 0, f"bundled debugfs -R fails (exit {r.returncode}): " + r.stdout + r.stderr
    print("selfcheck ok")
    return 0


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
    ctx.ui.say("This copies the clock's whole disk in BROM mode: about 35 minutes, and 9 GB free while it "
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
    if not folder.exists() and folder.with_name(folder.name + ".zip").is_file():
        folder = folder.with_name(folder.name + ".zip")    # setup keeps only the zip
    if not folder.exists():
        print(f"Can't restore this backup: no backup at {folder}", file=sys.stderr)
        return 2
    ctx = _context(args)
    ctx.ui.title("Restore a backup")
    ctx.ui.say("This puts a backup back on the clock it came from: its system on both slots and its user "
               "data. It takes about 20 minutes, in BROM mode twice: once to check it's the right clock, "
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


WIFI_WHY = "The tool saves this network on the clock. The password is never logged."


def _wifi(args) -> int:
    from lck import wifi
    from lck.system import ask_wifi
    ctx = _context(args)
    ctx.ui.title("Change the clock's Wi-Fi")
    fetch(f"platform-tools-{platform_id()}", ctx)
    ctx.state["tools"] = Tools.locate(ctx.paths.cache)
    on_wifi = False
    if not args.usb:
        if not ctx.state.get("ip"):
            ctx.state["ip"] = clock.find_on_wifi(ctx) or _saved_ip(args)
        if ctx.state.get("ip"):
            with ctx.ui.busy(f"Looking for the clock at {ctx.state['ip']}"):
                on_wifi = clock.is_clock(ctx)
        if not on_wifi:
            from lck.system import ask_ip_or_usb
            on_wifi = ask_ip_or_usb(ctx)
    if on_wifi:
        ctx.ui.say(f"The clock answers at {ctx.state['ip']}, so the tool tells it over Wi-Fi: no USB, and no restart unless you give it a fixed address. "
                   "If the new network doesn't work, the clock goes back to this one by itself.")
        ssid, psk = ask_wifi(ctx, WIFI_WHY)
        static = ctx.state.get("wifi_static")
        if static is not None:
            ctx.ui.say("For the fixed address, the clock restarts once it has joined the network.")
        return 0 if wifi.over_wifi(ctx, ssid, psk, static=static) else 1
    ctx.ui.say("The tool reaches the clock over USB, then: in BROM mode it reads the clock's user data "
               f"({wifi.READ_TIME}), saves the network in it on this computer, in place of the clock's other saved "
               "networks, and then writes it back in fastboot mode (under a minute). Nothing else on the clock "
               "changes. It needs about 6 GB free "
               "for a while.")
    ssid, psk = ask_wifi(ctx, WIFI_WHY)
    _tools(ctx)
    wifi.over_usb(ctx, ssid, psk, static=ctx.state.get("wifi_static"))
    return 0


def _saved_ip(args) -> str | None:
    f = Paths(args.workdir).progress / f"{args.name}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("ip")
    except (OSError, ValueError):
        return None


def _setup(args) -> int:
    ctx = _context(args)
    ctx.dry_run = args.dry_run
    ctx.state["skip_backup"] = args.skip_backup
    fresh = args.restart or not (ctx.paths.progress / f"{ctx.state.get('progress_id')}.json").exists()
    if not ctx.state.get("ip") and not args.dry_run:
        # UX run 1: a converted clock wasn't recognised without --ip. Ask only what adb already knows.
        try:
            from lck.downloads import fetch
            fetch(f"platform-tools-{platform_id()}", ctx)
            found = clock.find_on_wifi(ctx)
        except Exception:
            found = None
        if found:
            ctx.state["ip"] = found
        else:
            from lck.system import check_saved_ip
            check_saved_ip(ctx)
    if fresh and not args.dry_run and args.start_from is None:
        ctx.ui.intro(_intro(ctx))
    run_steps(plan.setup_steps(), ctx, start_from=args.start_from, restart=args.restart)
    return 0


def _intro(ctx) -> str:
    """What this run will take, and where it keeps its files."""
    return _what_it_takes(ctx) + " " + _where(ctx)


def _where(ctx) -> str:
    """Issue 5: a run from the folder above the one used before found nothing, and started over from step 1.
    So the intro names the folder, and points to progress for this clock in the folder above or one below."""
    root = Path(ctx.paths.root).resolve()
    name = f"{ctx.state.get('progress_id', 'clock')}.json"
    text = (f"Everything it keeps (downloads, the backup, logs and progress) goes in {root}: run lck from this "
            "same folder next time, and it carries on where it stopped.")
    if (root / "progress" / name).exists():
        return text
    try:
        near = [root.parent, *sorted(d for d in root.iterdir() if d.is_dir())]
    except OSError:
        near = [root.parent]
    for folder in near:
        if folder != root and (folder / "progress" / name).is_file():
            return (text + f" Earlier progress for this clock is in {folder}. To carry on from there instead, "
                    f"stop with Ctrl-C, go to that folder (cd \"{folder}\") and run the same command.")
    return text


def _what_it_takes(ctx) -> str:
    """What this run will take, leaving out what's already done (e2e run 2: a seeded backup was
    still promised as 40 minutes)."""
    if ctx.state.get("ip") and clock.converted(ctx):
        return (f"The clock at {ctx.state['ip']} is already converted: steps whose work is there skip "
                "themselves. To only back it up, stop with Ctrl-C and run lck backup instead.")
    has_backup = not backup.verify_backup(ctx.paths.backup / ctx.state.get("progress_id", "clock"))
    if has_backup:
        return ("Setup takes about 45 minutes and needs 15 GB free: about 200 MB of downloads, and the images "
                "built from the backup already here. The tool tells you each time it needs you at the clock.")
    return ("Setup takes about 1 hour 15 minutes and needs 20 GB free: about 200 MB of downloads, a backup of "
            "the whole clock (about 35 minutes, 0.7 GB kept), and the images built from it. The tool tells you "
            "each time it needs you at the clock.")


def _log_error(args, text: str) -> None:
    """The reason itself goes in the log, so the tail shown under it is about this stop
    (e2e run 2: a GitHub error showed the previous run's mtkclient lines)."""
    name, workdir = getattr(args, "name", None), getattr(args, "workdir", None)
    if not name or workdir is None:
        return
    from lck.log import redact
    try:
        f = Paths(workdir).logs / f"{name}.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as out:
            out.write(redact(f"STOPPED {text}") + "\n")
    except OSError:
        pass


def needs_sudo(args) -> bool:
    """On a Mac, the commands that reach the clock over USB run as root (sudo): macOS's own serial driver
    takes the clock in BROM mode, and only root can take it over (a tester's Mac, issue 1). Checked
    before anything is made, so the work folder never mixes files of two accounts."""
    if sys.platform != "darwin" or args.command not in ("setup", "backup", "restore", "wifi"):
        return False
    if getattr(args, "dry_run", False):
        return False
    return os.geteuid() != 0


SUDO_NEEDED = ("On a Mac, this needs administrator rights: macOS's own driver holds the clock in BROM mode, and "
               "only an administrator can take it over. Run it with sudo, and type this Mac's password when it "
               "asks (nothing shows while you type):\n\n  {command}\n\nUse sudo every time, for every command "
               "of this tool.")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if needs_sudo(args):
        # From source, argv[0] is lck/__main__.py: the command to repeat is python -m lck.
        prog = (f"{sys.executable} -m lck" if sys.argv and sys.argv[0].endswith("__main__.py")
                else (sys.argv[0] if sys.argv else "./lck/lck"))
        print(SUDO_NEEDED.format(command=" ".join(["sudo", prog, *sys.argv[1:]])), file=sys.stderr)
        return 2
    try:
        return {"setup": _setup, "backup": _backup, "restore": _restore, "wifi": _wifi, "status": _status,
                "selfcheck": _selfcheck}[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue where you left off.", file=sys.stderr)
        _stuck(args)
        return 130
    except Exception as e:  # every failure ends in plain words, not a traceback
        _log_error(args, str(e))
        print(f"\nStopped: {plain_error(e)}", file=sys.stderr)
        _print_log_tail(args)
        print("Run the same command again to retry this step.", file=sys.stderr)
        _stuck(args, str(e))
        return 1


def plain_error(e: BaseException) -> str:
    """No Python command lists on screen (e2e run 6): what failed, in words. The log has the command."""
    import subprocess
    from lck.runner import CommandFailed
    if isinstance(e, subprocess.TimeoutExpired):
        tool = Path(str(e.cmd[0] if isinstance(e.cmd, (list, tuple)) else e.cmd)).name
        return (f"{tool} got no answer within {e.timeout:.0f} seconds. The clock may have restarted, or lost power "
                "or Wi-Fi.")
    if isinstance(e, CommandFailed):
        tool = Path(e.args_[0]).name
        last = [l for l in e.result.out.strip().splitlines() if l.strip()][-1:] or ["no output"]
        return f"{tool} failed: {last[0].strip()}"
    return str(e)


def _stuck(args, said: str = "") -> None:
    """UX run 1: a stop left the clock in fastboot, dark, with no word about it."""
    if getattr(args, "command", "") == "status":
        return
    from lck.modes import stuck_hint
    hint = stuck_hint()
    if hint and hint not in said:
        print(hint, file=sys.stderr)


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
    # Only this run's lines (e2e run 3: the tail held two earlier runs' lines above this stop).
    if RUN_MARK in text:
        text = text[text.rindex(RUN_MARK):].split("\n", 1)[-1]
    tail = [l for l in text.splitlines() if l.strip()][-lines:]
    if not tail:
        return
    print("\nThe last lines of the log:", file=sys.stderr)
    for line in tail:
        print("  " + line.replace(home, "~"), file=sys.stderr)
    print("Full log: " + str(f).replace(home, "~"), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
