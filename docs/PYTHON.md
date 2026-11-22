# Running from source (Python)

The same tool as the ready-made program: same steps, prompts, checks and resume. You run it with your own
Python, which needs to be 3.10 or newer.

## 1. Install Python and the system tools

| System | How |
|---|---|
| **Windows** | Install Python from [python.org](https://www.python.org/downloads/windows/), and **tick "Add python.exe to PATH"**. Or run `winget install Python.Python.3.12`. Then the [drivers](drivers.md#windows). The tool downloads e2fsprogs by itself. |
| **macOS** | `brew install python@3.12 libusb e2fsprogs`. Apple's own `python3` (3.9) is too old. |
| **Linux** | `sudo apt install python3 python3-venv e2fsprogs libusb-1.0-0` (Debian/Ubuntu), `sudo dnf install python3 e2fsprogs libusb1` (Fedora) or `sudo pacman -S python e2fsprogs libusb` (Arch). Then the [USB rule](drivers.md#linux). |

Check: `python3 --version` (or `py --version` on Windows) shows 3.10 or newer.

## 2. Get the code and its packages

```bash
git clone https://github.com/TRusselo/lenovo-clock-ha-kiosk
cd lenovo-clock-ha-kiosk
python3 -m venv .venv
```

Activate the venv: `source .venv/bin/activate` on macOS or Linux, `.venv\Scripts\activate` on Windows.

```bash
pip install -r requirements.txt
```

## 3. Run

```bash
python -m lck setup
```

**On a Mac, run it with sudo**, and the venv's Python by its path (sudo doesn't keep the activated venv):

```bash
sudo .venv/bin/python -m lck setup
```

macOS's own driver takes the clock in BROM mode, and only an administrator can take it over. Use sudo for every
`lck` command on a Mac, so all the files belong to the same account.

Options:
- `--dry-run`: list the steps, change nothing
- `--from STEP`: start at a step
- `--restart`: forget saved progress
- `--skip-backup`: skip the backup, after a warning (only for a clock that's already converted)
- `--ip ADDRESS`: the clock's address on your Wi-Fi, once it has one
- `--name NAME`: a name for each clock when you do several
- `--workdir DIR`: where downloads, backups, logs and progress go

`python -m lck status` shows which steps are done. `python -m lck backup` backs the clock up at any time.
`python -m lck restore BACKUP` restores a backup: the folder or its zip. `python -m lck wifi` puts the clock on
another Wi-Fi network, or gives it a new password ([guide](GUIDE.md#changing-the-clocks-wi-fi)).

## Tests

```bash
pip install pytest
python -m pytest -q
```

The tests need no clock and no network.
