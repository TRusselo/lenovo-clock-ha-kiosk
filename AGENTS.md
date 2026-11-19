# For AI agents

## Converting a clock

You're probably helping someone convert a Lenovo Smart Clock gen 1 (CD-24501F, "cube").

- **Prefer the tool.** `.venv/bin/python -m lck setup` checks every step. Run it, and relay its physical prompts
  word for word: the person has to hold the buttons.
- **By hand:** follow [devices/cube/README.md](devices/cube/README.md) in order. **Run each step's Check and read
  its output before going on.** If a Check fails, stop and tell the person. Don't improvise.
- **Never:**
  - write the preloader (eMMC boot area), `lk_*`, `tee_*`, `oem_bootloader_*`, `seccfg`, `proinfo`, `nvram`, `kb`, `dkb` or
    the GPT. `oem_bootloader` is LK's device-tree table: flashing it with anything else hangs the clock in the
    preloader
  - disable `com.google.android.things.sparrow.oem`: Lenovo's Things app drives the amp, mics, volume keys and
    light sensor
  - edit `/init.rc`
  - edit Kiosk Satellite's `shared_prefs` file directly, or run `pm clear me.jxl.kiosk_satellite`
  - change the update channel without `--nopowerwash`
- **Ask before every write to the clock.** Name the partitions. The person's approval is per flash.
- **adb:** the clock is only ever on adb over Wi-Fi. Always pass `-s <clock-ip>:5555`, and check
  `ro.product.device` is `mt8167s_som` before writing anything: another device (a phone, a Smart Display on USB)
  may be connected too.
- **Physical steps:** the screen stays dark in fastboot and BROM. Find the mode from the USB IDs: `0e8d:0003`
  is BROM, `0e8d:2000` is the preloader, `0bb4:0c01` is LK fastboot. The clock runs on the barrel jack, not USB.
- **LK's fastboot hangs on `getvar`.** Flash with `fbmulti.py`; it waits up to 300 s for the device, so have the
  person enter fastboot first, or start it again. mtkclient is the other way round: BROM lasts only 3 to 5
  seconds, so it must already be waiting. VOLUME UP alone reaches BROM. A clock left at `0e8d:2001` by an earlier
  mtkclient session must be unplugged before the next one.
- **Secrets:** never ask for, print or store Wi-Fi passwords, Home Assistant tokens or the Kiosk Satellite admin
  password. When a Wi-Fi network has to be typed, the person types it into the tool, which hides and never logs
  the password. The injected userdata image holds the Wi-Fi PSK: keep it out of the repo and logs.
- **Never factory reset the clock** to go back: done online, it unlinks it from Google, which no restore undoes.
  Use `lck restore`.
- **Backups:** never upload or share a backup. It holds data unique to that clock.

## Working on this repo

- **Layout:** Python package in `lck/`, one module per concern, built on the Smart Display tool's (`lhk`) screen,
  steps, log and downloads, so the two look and feel the same; tests in `tests/`, which use fakes and never need
  hardware or network.
- **Every external command** goes through `ctx.runner`. Every mtkclient command goes through `mtk.assert_safe`,
  every flash through `flash.assert_safe`.
- **TDD:** write the failing test first.
- **Run the tests:** `python -m pytest -q`.
- **Downloads** are pinned in `manifest.json` with SHA-256. Never host firmware, credentials or
  third-party tools here.
- **Findings** go in `docs/findings/NOTES.md`, with what was observed versus guessed.
