"""Ordered, resumable steps.

Progress is saved after every step to progress/<serial>.json, so a run that
stops (Ctrl-C, a dropped cable, an error) starts again where it left off.
Each step can also check the device itself (Step.done) and is skipped when its
work is already there, so a rerun never repeats an unlock, flash or wipe.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lck import __version__


@dataclass
class Paths:
    root: Path

    def __post_init__(self):
        self.root = Path(self.root)

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def backup(self) -> Path:
        return self.root / "backup"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def progress(self) -> Path:
        return self.root / "progress"


@dataclass
class Context:
    runner: Any
    ui: Any
    paths: Paths
    dry_run: bool = False
    state: dict = field(default_factory=dict)
    # Closing words, shown after the last step's done screen (on a terminal, each
    # step clears the screen, so anything printed before it would vanish).
    after: list = field(default_factory=list)

    @property
    def serial(self) -> str:
        return self.state.get("serial") or "unknown"


RECOVERIES = 3     # times a step that lost the clock starts again before the run stops


class Step:
    name = "step"
    title = "Step"

    def done(self, ctx: Context) -> bool:
        """True when the device already has this step's result."""
        return False

    def run(self, ctx: Context) -> None:
        raise NotImplementedError


def _progress_file(ctx: Context) -> Path:
    # A fixed id per clock (the CLI's --name), not the serial: the first
    # steps run before the serial is known.
    return ctx.paths.progress / f"{ctx.state.get('progress_id') or ctx.serial}.json"


def _load(ctx: Context) -> list[str]:
    f = _progress_file(ctx)
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    if data.get("ip") and "ip" not in ctx.state:
        # The clock's Wi-Fi address, from the connect step (--ip overrides it).
        ctx.state["ip"] = data["ip"]
    if "wifi_ready" in data and "wifi_ready" not in ctx.state:
        ctx.state["wifi_ready"] = data["wifi_ready"]     # step 1's YES or NO (never the password)
    if data.get("serial"):
        # Later steps refuse a different clock than the one this progress is for.
        ctx.state["bound_serial"] = data["serial"]
    return data.get("done", [])


def _save(ctx: Context, done: list[str]) -> None:
    f = _progress_file(ctx)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    serial = ctx.state.get("serial") or ctx.state.get("bound_serial")
    tmp.write_text(json.dumps({"done": done, "serial": serial, "ip": ctx.state.get("ip"),
                               "wifi_ready": ctx.state.get("wifi_ready"),
                               "version": __version__}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, f)


def run_steps(steps: list[Step], ctx: Context, *, start_from: str | None = None,
              restart: bool = False) -> None:
    names = [s.name for s in steps]
    if start_from is not None and start_from not in names:
        raise ValueError(f"no step named {start_from!r}; steps are: {', '.join(names)}")
    done = [] if restart else _load(ctx)
    # Steps --from passes over without their work being done: shown as skipped, never as done,
    # and not saved as done, so a later run without --from still does them.
    skipped = set()
    if start_from is not None:
        skipped = {n for n in names[:names.index(start_from)] if n not in done}
    if all(n in done or n in skipped for n in names) and not ctx.dry_run and ctx.ui is not None:
        ctx.ui.say("Every step is already done for this clock. Use --restart to run them again, "
                   "or --name to start a different clock.")
        return
    finished = [n for n in names if n in done]
    shown = not ctx.dry_run and ctx.ui is not None
    if finished and shown and start_from is None:
        nxt = next(s for s in steps if s.name not in done)
        ctx.ui.intro(f"Picking up where the last run stopped, at: {nxt.title} "
                   f"({len(finished)} of {len(names)} steps done).")
    titles = [s.title for s in steps]

    def done_numbers():
        return {i for i, s in enumerate(steps, 1) if s.name in done}

    skipped_numbers = {i for i, s in enumerate(steps, 1) if s.name in skipped}

    for number, step in enumerate(steps, 1):
        if step.name in done or step.name in skipped:
            continue
        if ctx.dry_run:
            if ctx.ui is not None:
                ctx.ui.say(f"[dry run] {step.title}")
            continue
        if shown:
            # Numbered like the headings in the manual (devices/cube/README.md).
            ctx.ui.screen(titles, number, done_numbers(), skipped_numbers)
        for attempt in range(RECOVERIES + 1):
            try:
                if step.done(ctx):
                    if ctx.ui is not None:
                        ctx.ui.say(f"Already done on the clock: {step.title}.")
                else:
                    step.run(ctx)
                break
            except Exception as e:
                # e2e run 6: a power cut mid-step stopped setup, though the clock was back in a minute.
                # A step that can tell it lost the clock waits for it and starts again.
                recover = getattr(step, "recover", None)
                if attempt < RECOVERIES and recover is not None and recover(ctx, e):
                    continue
                raise
        done.append(step.name)
        _save(ctx, done)
        if shown:
            ctx.ui.ok(step.title)
    if shown:
        ctx.ui.screen(titles, len(steps) + 1, done_numbers(), skipped_numbers)
    for closing in ctx.after:
        closing()
