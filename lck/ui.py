"""What the user sees and types. Plain words; physical steps stand out."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
import textwrap
import threading
import time


def _windows_vt_on() -> bool:
    """Windows consoles show colour codes only with virtual-terminal mode on."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


# Symbols, with plain-ASCII stand-ins for consoles that can't show them
# (Windows with output piped or redirected uses cp1252).
_FANCY = {"ok": "\u2713", "todo": "\u00b7", "fail": "\u2717", "full": "\u2588", "empty": "\u2591",
          "spin": "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f", "arrow": "\u25b6",
          "now": "\u25b8", "to": "\u2192", "skip": "\u2013"}
_PLAIN = {"ok": "[x]", "todo": "[ ]", "fail": "[!]", "full": "#", "empty": "-", "spin": "|/-\\",
          "arrow": ">", "now": ">", "to": "->", "skip": "[-]"}

# Purple and cyan for the screen itself, yellow for anything the user has to do,
# red only for errors. Purple and cyan come from the fixed 256-colour palette:
# colour schemes remap the basic magenta, and some draw it red.
_BOLD, _PURPLE, _TITLE, _YELLOW, _RED, _CYAN, _NOW, _DIM, _RESET = (
    "\x1b[1m", "\x1b[38;5;141m", "\x1b[1;38;5;141m", "\x1b[1;33m", "\x1b[31m", "\x1b[38;5;80m",
    "\x1b[1;38;5;80m", "\x1b[38;5;248m", "\x1b[0m")    # "dim" is a light grey: the terminal's own dim is unreadable on black
_LINK = "\x1b[4;38;5;80m"
CLEAR = "\x1b[H\x1b[2J\x1b[3J"     # the screen and the scrollback: no banner or old screens above
TITLE = "Lenovo Smart Clock {to} Home Assistant"
DEVICE = "Lenovo Smart Clock"
# Bold, always, wherever they're asked for: buttons and keys, and what the person does with the
# power plug (one rule here, so no message can miss it). Anything else to stand out, such as the
# mode and the time a box waits for, is marked **like this** in the message itself.
# \s+ between words: the text is wrapped first, so a phrase can break across lines.
_BUTTONS = re.compile(r"\b(VOLUME\s+UP|VOLUME\s+DOWN|FINISH|Finish|Enter|[Uu]nplug(?:ged)?|[Rr]eplug|"
                      r"plug\s+(?:it|the\s+power|its\s+power|the\s+clock)\s+back\s+in|plug\s+it\s+in|"
                      r"[Hh]old(?:ing)?\s+no\s+buttons|(?:with\s+)?no\s+buttons\s+held|"
                      r"[Ll]et\s+go(?:\s+of\s+the\s+buttons|\s+of\s+VOLUME\s+UP)?)\b")
_MARKED = re.compile(r"\*\*(.+?)\*\*", re.S)
_URL = re.compile(r"https?://[^\s)]+")
# doc()'s "(in the tool's folder, or <url>)": the link goes on a line of its own (e2e run 5: it wrapped badly).
_DOC_LINK = re.compile(r" \(in the tool's folder, or (https?://[^\s)]+)\)")


def _own_line(text: str) -> str:
    return _DOC_LINK.sub(lambda m: " (in the tool's folder, or online:)\n  " + m.group(1), text)


def plain(text: str) -> str:
    """The text without its **marks**, for output that has no bold."""
    return _MARKED.sub(lambda m: m.group(1), text)


def _old_windows_console() -> bool:
    """Windows' classic console window, whose fonts lack these symbols. Windows Terminal sets WT_SESSION."""
    return os.name == "nt" and not os.environ.get("WT_SESSION")


def _elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


class UI:
    def __init__(self, stdin=None, stdout=None, logger=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._status_shown = False
        self._sym = None
        self._col = None
        self._listed = False
        self._intro = []      # stays on each screen until the user is first needed
        self._kept = []       # shown once more, on the next screen
        self._last = None     # the last screen drawn, for redraw()
        self._spinners = []   # busy jobs, innermost last: only it draws
        self.logger = logger  # what the person was told and answered goes in the run log (e2e run 6)
        self._box_shown = False   # an "On the clock" box is on the current screen
        self._since_box = []      # what was said under it, carried over when the next box redraws

    def _symbols(self) -> dict:
        if self._sym is None and _old_windows_console():
            self._sym = _PLAIN
        if self._sym is None:
            enc = getattr(self.stdout, "encoding", None) or "utf-8"
            try:
                "".join(_FANCY.values()).encode(enc)
                self._sym = _FANCY
            except (UnicodeEncodeError, LookupError):
                self._sym = _PLAIN
        return self._sym

    def _paint(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self._colour() else text

    def _end_status(self):
        if self._status_shown:
            self.stdout.write("\n")
            self._status_shown = False

    def _wrap(self, text: str, indent: str = "  ") -> str:
        """Indented to line up with the screen, long lines wrapped. A line's own indent is kept,
        and a numbered item's next lines line up under its text."""
        columns = min(shutil.get_terminal_size((80, 24)).columns, 100)
        out = []
        for line in text.split("\n"):
            if not line.strip():
                out.append(indent.rstrip() if not line else indent + line)
                continue
            lead = line[:len(line) - len(line.lstrip())]
            item = re.match(r"\d+\. ", line.lstrip())
            hang = indent + lead + (" " * len(item.group(0)) if item else "")
            # Never split a path or a link: they have to stay copyable and clickable.
            out += textwrap.wrap(line.lstrip(), max(columns - len(hang), 20), initial_indent=indent + lead,
                                 subsequent_indent=hang, break_on_hyphens=False, break_long_words=False)
        return "\n".join(out)

    def _buttons(self, text: str, colour: str = "") -> str:
        """Buttons, keys, the power plug and **marked** words in bold, then back to the
        surrounding colour."""
        if not self._colour():
            return plain(text)
        text = _MARKED.sub(lambda m: f"{_BOLD}{m.group(1)}{_RESET}{colour}", text)
        # Addresses and links stand out (e2e run 5), in the screen's cyan, underlined.
        text = _URL.sub(lambda m: f"{_LINK}{m.group(0)}{_RESET}{colour}", text)
        return _BUTTONS.sub(lambda m: f"{_BOLD}{m.group(0)}{_RESET}{colour}", text)

    def redraw(self) -> None:
        """Draw the last screen again, clearing what was printed under it (a retry's old
        box and hints). Without a terminal: nothing, the text just scrolls on."""
        if self._live() and self._last:
            self._end_status()
            draw, args = self._last
            draw(*args)

    def _users_turn(self) -> None:
        """The user has something to do: the opening note has been read by now."""
        self._intro = []

    def keep(self, text: str) -> None:
        """Say it now, and again on the next screen, so a redraw doesn't hide it."""
        self.say(text)
        if self._live():
            self._kept.append(text)

    def _log(self, kind: str, text: str) -> None:
        if self.logger:
            self.logger.info("%s %s", kind, plain(text).replace("\n", " / "))

    def say(self, text: str, log: bool = True) -> None:
        if log:
            self._log("SAY", text)
        self._end_status()
        if self._box_shown:
            self._since_box.append(text)
        text = self._buttons(self._wrap(_own_line(text))) if self._live() else plain(text)
        self.stdout.write(f"{text}\n")
        self.stdout.flush()

    def physical(self, text: str) -> None:
        """Something to do with your hands, at the clock. One box on screen at a time: a second
        box in the same step redraws the screen first (e2e run 5: two boxes, easy to read the
        wrong one)."""
        self._log("BOX", text)
        self._end_status()
        self._users_turn()
        if self._live() and self._box_shown:
            # What was said since the last box (why this one comes) is said again under the fresh screen.
            pending, self._since_box = self._since_box, []
            self.redraw()
            for line in pending:
                self.say(line, log=False)      # already in the log once (e2e run 7: logged twice)
        self._box_shown = True
        self._since_box = []
        if self._colour():
            head = self._paint(_YELLOW, f"{self._symbols()['arrow']} On the clock")
            self.stdout.write(f"\n  {head}\n{self._buttons(self._wrap(_own_line(text), '    '))}\n")
        else:
            bar = "=" * 72
            self.stdout.write(f"\n{bar}\n  DO THIS ON THE CLOCK:\n  {plain(text)}\n{bar}\n")
        self.stdout.flush()

    def title(self, text: str) -> None:
        """A fresh screen for a job that isn't the step-by-step setup, such as a restore."""
        self._end_status()
        if not self._live():
            self.stdout.write(f"\n{text}\n\n")
            self.stdout.flush()
            return
        self._last = (self.title, (text,))
        self._box_shown, self._since_box = False, []
        self.stdout.write(f"{CLEAR}\n  {self._paint(_TITLE, DEVICE)}\n  {self._paint(_NOW, text)}\n\n")
        self.stdout.flush()

    def _bar(self, fraction: float, width: int = 12) -> str:
        sym = self._symbols()
        filled = max(0, min(width, round(width * fraction)))
        return self._paint(_PURPLE, sym["full"] * filled) + self._paint(_DIM, sym["empty"] * (width - filled))

    def intro(self, text: str) -> None:
        """Words for the top of the next screen. Without a terminal: shown now."""
        if self._live():
            self._intro.append(text)
        else:
            self.say(text)

    def _live(self) -> bool:
        """The redrawn screen: a terminal that takes colour, and isn't Windows' classic console."""
        return self._colour() and not _old_windows_console()

    def screen(self, titles, current: int, done, skipped=frozenset()) -> None:
        """The setup screen at the start of step `current` (1-based; past the end: all done).
        On a terminal: cleared and redrawn. Otherwise: the list once, then a line per step."""
        self._end_status()
        total = len(titles)
        if not self._live():
            if not self._listed:
                self.overview([(t, i in done) for i, t in enumerate(titles, 1)])
                self._listed = True
            if current <= total:
                self.stdout.write(f"\nStep {current} of {total}: {titles[current - 1]}\n")
                self.stdout.flush()
            return
        self._last = (self.screen, (titles, current, done, skipped))
        self._box_shown, self._since_box = False, []
        sym = self._symbols()
        width = 32
        count = len(done)
        filled = width if current > total else round(width * count / total) if total else 0
        out = [CLEAR, "\n  " + self._paint(_TITLE, TITLE.format(to=sym["to"])) + "\n\n",
               "  " + self._paint(_PURPLE, sym["full"] * filled) + self._paint(_DIM, sym["empty"] * (width - filled))
               + "  " + self._paint(_DIM, f"{count}/{total}") + "\n\n"]
        for i, t in enumerate(titles, 1):
            if i == current:
                out.append("  " + self._paint(_NOW, f"{sym['now']} {t}") + "\n")
            elif i in done:
                out.append(f"  {self._paint(_CYAN, sym['ok'])} {self._paint(_DIM, t)}\n")
            elif i in skipped:
                out.append("  " + self._paint(_DIM, f"{sym['skip']} {t} (skipped)") + "\n")
            else:
                out.append("  " + self._paint(_DIM, f"{sym['todo']} {t}") + "\n")
        out.append("\n")
        for text in self._intro + self._kept:
            out.append(self._buttons(self._wrap(_own_line(text))) + "\n\n")
        self._kept = []
        self.stdout.write("".join(out))
        self.stdout.flush()

    def overview(self, steps) -> None:
        """Every step, with the finished ones marked. steps: (title, done) pairs."""
        self._end_status()
        sym = self._symbols()
        self.stdout.write("\nThe steps:\n")
        for i, (title, done) in enumerate(steps, 1):
            mark = self._paint(_CYAN, sym["ok"]) if done else sym["todo"]
            self.stdout.write(f"  {mark} {i:>2}. {title}\n")
        self.stdout.flush()

    def header(self, index: int, total: int, title: str) -> None:
        """The start of a step: a progress bar and its number, matching the manual's headings."""
        self._end_status()
        sym = self._symbols()
        width = 20
        filled = round(width * (index - 1) / total) if total else 0
        bar = f"[{sym['full'] * filled}{sym['empty'] * (width - filled)}]"
        self.stdout.write(f"\n{self._paint(_PURPLE, bar)} {self._paint(_BOLD, f'Step {index} of {total}: {title}')}\n")
        self.stdout.flush()

    def ok(self, text: str) -> None:
        """A finished step."""
        self._end_status()
        self.stdout.write(f"  {self._paint(_CYAN, self._symbols()['ok'])} {text}\n")
        self.stdout.flush()

    def section(self, title: str, lines) -> None:
        """A heading with indented lines under it, e.g. what to do next."""
        self._end_status()
        if self._live():
            self.stdout.write(f"\n  {self._paint(_TITLE, title)}\n")
            for line in lines:
                self.stdout.write(self._buttons(self._wrap(_own_line(line), "    ")).replace(
                    "    ", f"  {self._paint(_PURPLE, self._symbols()['todo'])} ", 1) + "\n")
            self.stdout.flush()
            return
        self.stdout.write(f"\n{self._paint(_TITLE, title)}\n")
        for line in lines:
            self.stdout.write(f"  - {plain(line)}\n")
        self.stdout.flush()

    def _colour(self) -> bool:
        if self._col is None:
            if os.environ.get("NO_COLOR") or not getattr(self.stdout, "isatty", lambda: False)():
                self._col = False
            else:
                self._col = _windows_vt_on() if os.name == "nt" else True
        return self._col

    def alert(self, text: str) -> None:
        """Something easy to miss that matters: bold yellow on a terminal.
        Callers write it in capitals, so it stands out without colour too."""
        self._log("ALERT", text)
        self._end_status()
        if self._live():
            self.stdout.write(f"\n{_YELLOW}{self._buttons(self._wrap(text), _YELLOW)}{_RESET}\n")
        elif self._colour():
            self.stdout.write(f"\n\x1b[1;33m{plain(text)}\x1b[0m\n")
        else:
            self.stdout.write(f"\n{plain(text)}\n")
        self.stdout.flush()

    def status(self, mode: str) -> None:
        if self._live():
            self.stdout.write(f"\r\x1b[K  {self._paint(_DIM, 'Clock mode:')} {self._paint(_CYAN, mode)}")
            self.stdout.flush()
            self._status_shown = True
            return
        self.stdout.write(f"\r  Clock mode: {mode:<10}")
        self.stdout.flush()
        self._status_shown = True

    @contextlib.contextmanager
    def busy(self, text: str, interval: float = 3.0):
        """Show that slow work is running, on one line. Yields a Job: job.note() adds a detail
        (how far along), job.done() sets the words for the finished line.
        On a terminal: a spinner and the time so far. Otherwise: the text, a dot every few seconds."""
        self._end_status()
        job = Job(text)
        if not self._colour():
            with self._dots(text, interval):
                yield job
            return
        sym = self._symbols()
        start = time.monotonic()
        stop = threading.Event()

        self._spinners.append(job)

        def spin():
            i = 0
            while True:
                if self._spinners and self._spinners[-1] is not job:
                    # One spinner at a time: an inner job draws, this one waits (e2e run 6: two lines
                    # redrawing each other at 30 a second).
                    if stop.wait(0.1):
                        return
                    continue
                took = time.monotonic() - start
                time_text = f"  {_elapsed(took)}"     # from 0s: a timer that appears at 3s reads as late
                note = f"  {job.detail}" if job.detail else ""
                bar = f"  {self._bar(job.fraction)}" if job.fraction is not None else ""
                # Stay inside one row: a wrapped line can't be redrawn with \r.
                room = shutil.get_terminal_size((80, 24)).columns - 1 - 4 - len(time_text) - (14 if bar else 0)
                # Cut the words, never the bar (e2e run 6: "about 35 minut..  bar ."): the bar goes after them.
                head, tail = text, note
                if len(head) + len(tail) > room:
                    if len(head) > room - 3:
                        head, tail = head[:max(room - 3, 0)] + "...", ""
                    else:
                        tail = tail[:max(room - len(head) - 3, 0)] + "..."
                shown = head + bar + self._paint(_DIM, tail)
                extra = f"  {self._paint(_DIM, time_text.strip())}" if time_text else ""
                self.stdout.write(f"\r\x1b[K  {self._paint(_PURPLE, sym['spin'][i % len(sym['spin'])])} "
                                  f"{shown}{extra}")
                self.stdout.flush()
                i += 1
                if stop.wait(0.1):
                    return
        t = threading.Thread(target=spin, daemon=True)
        t.start()
        ok = False
        try:
            yield job
            ok = True
        finally:
            stop.set()
            t.join()
            if job in self._spinners:
                self._spinners.remove(job)
            took = _elapsed(time.monotonic() - start)
            if ok and job.paused:
                self.stdout.write(f"\r\x1b[K  {self._paint(_YELLOW, '! ' + job.final)}\n")
            elif ok:
                self.stdout.write(f"\r\x1b[K  {self._paint(_CYAN, sym['ok'])} {job.final or text} "
                                  f"{self._paint(_DIM, f'({took})')}\n")
            else:
                stopped_at = f" ({job.detail})" if job.detail else ""
                self.stdout.write(f"\r\x1b[K  {self._paint(_RED, sym['fail'] + ' ' + text + stopped_at)}: "
                                  f"stopped ({took})\n")
            self.stdout.flush()

    @contextlib.contextmanager
    def _dots(self, text: str, interval: float):
        self.stdout.write(text)
        self.stdout.flush()
        stop = threading.Event()

        def dots():
            while not stop.wait(interval):
                self.stdout.write(".")
                self.stdout.flush()
        t = threading.Thread(target=dots, daemon=True)
        t.start()
        ok = False
        try:
            yield
            ok = True
        finally:
            stop.set()
            t.join()
            self.stdout.write(" done\n" if ok else " stopped\n")
            self.stdout.flush()

    def ask_secret(self, text: str) -> str:
        """A question whose answer isn't shown as it's typed, and never logged (a Wi-Fi password)."""
        self._log("ASK", text + " (answer not logged)")
        self._end_status()
        self._users_turn()
        shown = f"{_YELLOW}{self._buttons(self._wrap(text.rstrip(' ')), _YELLOW)}{_RESET} " if self._live() else plain(text)
        if getattr(self.stdin, "isatty", lambda: False)():
            import getpass
            return getpass.getpass(shown)
        self.stdout.write(shown)
        self.stdout.flush()
        return self.stdin.readline().rstrip("\r\n")

    def ask(self, text: str, log_answer: bool = True) -> str:
        self._log("ASK", text)
        self._end_status()
        self._users_turn()
        if self._live() and self.stdin is sys.stdin and sys.stdin.isatty():
            # The question on its own lines, the answer after a short "> " with line editing:
            # typing that wrapped past the line couldn't be backspaced (e2e run 6).
            self.stdout.write(f"{_YELLOW}{self._buttons(self._wrap(text.rstrip(' ')), _YELLOW)}{_RESET}\n")
            self.stdout.flush()
            try:
                import readline  # noqa: F401  (line editing for input())
            except ImportError:
                pass
            line = input("  > ")
        else:
            if self._live():
                # Your turn: yellow, like the boxes, wrapped to the terminal.
                text = f"{_YELLOW}{self._buttons(self._wrap(text.rstrip(' ')), _YELLOW)}{_RESET} "
            self.stdout.write(plain(text))
            self.stdout.flush()
            line = self.stdin.readline()
        line = line.rstrip("\r\n")
        self._log("ANSWER", line if log_answer else "(answer not logged)")
        return line


class Job:
    """One line of slow work: what it's doing now, and what to say when it's finished."""

    def __init__(self, text: str):
        self.text = text
        self.detail = ""
        self.final = None
        self.paused = False
        self.fraction = None

    def note(self, detail: str, done: float | None = None, total: float | None = None) -> None:
        """What it's doing now; with done and total, the line also gets a small progress bar."""
        self.detail = detail
        if done is not None and total:
            self.fraction = done / total

    def done(self, final: str) -> None:
        self.final = final

    def pause(self, final: str) -> None:
        """Ends the line as waiting on the user, not as done or failed."""
        self.final = final
        self.paused = True


def choose(ui, prompt: str, word: str) -> bool:
    """A prompt with two answers: type `word` (True) or press Enter (False).
    Anything else is a typo, and asks again rather than counting as either."""
    while True:
        answer = ui.ask(prompt).strip().lower()
        if answer == word:
            return True
        if not answer:
            return False
        ui.say(f'"{answer}" isn\'t an answer here. Type {word}, or just press Enter.')
