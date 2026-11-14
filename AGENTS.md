# For AI agents

## Converting a clock

You're probably helping someone convert a Lenovo Smart Clock gen 1 (CD-24501F, "cube").

- **There is no guided tool yet.** Follow [devices/cube/README.md](devices/cube/README.md) in order. **Run each
  step's Check and read its output before going on.** If a Check fails, stop and tell the person. Don't improvise.
- **Never:**
  - write the preloader (eMMC boot area), `lk_*`, `tee_*`, `oem_bootloader_*`, `seccfg`, `proinfo`, `nvram` or
    the GPT. `oem_bootloader` is LK's device-tree table: flashing it with anything else hangs the clock in the
    preloader
  - disable `com.google.android.things.sparrow.oem`: Lenovo's Things app drives the amp, mics, volume keys and
    light sensor
  - edit `/init.rc`
  - edit Kiosk Satellite's `shared_prefs` file directly, or run `pm clear me.jxl.kiosk_satellite`
  - change the update channel without `--nopowerwash`
- **Ask before every write to the clock.** Name the partitions. The person's approval is per flash.
- **Physical steps:** the screen stays dark in fastboot and BROM. Find the mode from the USB IDs: `0e8d:0003`
  is BROM, `0e8d:2000` is the preloader, `0bb4:0c01` is LK fastboot. The clock runs on the barrel jack, not USB.
- **LK's fastboot hangs on `getvar`.** Start the command first (`fbmulti.py` waits for the device), then have the
  person enter fastboot.
- **Secrets:** never ask for, print or store Wi-Fi passwords, Home Assistant tokens or the Kiosk Satellite admin
  password. The injected userdata image holds the Wi-Fi PSK: keep it out of the repo and logs.
- **Backups:** never upload or share a backup. It holds data unique to that clock.

## Working on this repo

- **Layout:** Python package in `lck/`, one module per concern; tests in `tests/`, which never need hardware or
  network.
- **TDD:** write the failing test first.
- **Run the tests:** `python -m pytest -q`.
- **Downloads** are pinned in `manifest.json` with a commit or SHA-256. Never host firmware, credentials or
  third-party tools here.
- **Findings** go in `docs/findings/NOTES.md`, with what was observed versus guessed.
