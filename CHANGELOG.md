# Changelog

What changed for people converting a clock. Newest first.

## 0.1.2 (2026-11-21)

### Fixes for testers (issue 1)
- **Linux: runs on older PCs and older Linux.** v0.1.1's Linux program needed a CPU with AVX2 (about 2013 or
  newer: older ones stopped with "Illegal instruction") and glibc 2.38 (Debian 12 and q4os have 2.36). It's now
  built on Debian 11 for any 64-bit PC CPU, needs glibc 2.30 or newer, and every build checks it runs on a 2008 CPU.
- **Mac: run it with sudo.** macOS's own driver takes the clock in BROM mode, and only an administrator can take it
  over. The Mac guide says so now, and the tool stops at once with the exact `sudo` command if it's missing.

### Volume buttons
- **The clock's volume buttons work** after the conversion: before, they did nothing (Google's handler for them
  doesn't run). A small helper from the Smart Display tool turns them into volume presses, 5% a press, repeating
  while held. It's a Magisk module (the system image isn't changed); setup asks you to press each button once.
  Tested on a clock.

### Time zones (from the Smart Display tool)
- **Current time zone data:** setup installs the 2026f IANA database over the clock's 2021 copy, as a Magisk
  module (the system image isn't changed), so regions whose daylight saving rules changed since, such as
  Alberta, get the right time. Untested on a clock so far; tested on the Smart Display.
- **The time zone question:** setup shows the clock's zone and lets you type another, checked against that data.
- **[TIMEZONES.md](docs/TIMEZONES.md):** dashboards keep the browser's own old rules; a fixed-offset zone or a
  Home Assistant template fixes them. "Use server time zone" doesn't.

### Windows: BROM mode (issue 1)
- **UsbDk is checked before BROM mode,** at the start of setup and before every backup or restore. Without it,
  Windows shows the clock in BROM mode but can't open it, and the clock started normally after a few seconds with
  no word why. The tool now stops first, with the install steps. `LCK_SKIP_USBDK_CHECK=1` skips the check.
- **"Windows couldn't open the clock in BROM mode"** when mtkclient reports exactly that, instead of waiting; and
  "mtkclient hasn't connected" if BROM showed and nothing happened for 10 seconds, instead of "Nothing yet".
- **Connected means connected:** mtkclient's own handshake counts, not only the USB watcher, so a backup or
  restore under way never gets the "unplug it" hint.
- **The log says more:** lck's version and the OS on every run, whether UsbDk was found, why mtkclient couldn't
  open USB (it hid that), all of mtkclient's output (one line per progress bar), and on Windows the MediaTek USB
  devices with their drivers.

## 0.1.1 and earlier

### Windows and macOS
- **Ready-made programs for Windows, macOS (Apple silicon) and Linux**, built by GitHub Actions on a version tag,
  like the Smart Display's: download a zip, unzip, run `lck setup`. Guides for each:
  [Windows](docs/WINDOWS.md) (with [USB drivers](docs/drivers.md)), [Mac](docs/MACOS.md), [Linux](docs/LINUX.md),
  and [from source](docs/PYTHON.md). Tested with a clock on Linux only so far.
- **No more Linux-only tools:** zstd and the sparse images for fastboot are now Python (the images still come out
  the same: checked against the ones the clock booted).
- **e2fsprogs comes in the zip** (`debugfs`, `e2fsck`): built from the pinned upstream source for Linux and macOS,
  Cygwin's build for Windows, with its licence, and its source attached to each release. Run from source, the tool
  uses the system's (Linux), Homebrew's (macOS), or downloads Cygwin's (Windows).
- **USB** uses the libusb the tool brings (from the libusb-package wheel), on every system.
- **The dashboard has its own home:** [lenovo-clock-dashboard](https://github.com/TRusselo/lenovo-clock-dashboard), installed with HACS. Its copy in this repo
  is gone, so there's one place to get it and ask about it.
### Fixes from the seventh end-to-end run
- **Fastboot waits never give up.** The flash helper stopped setup after 5 minutes without a device (twice, with
  the person away). The tool now waits for fastboot mode itself, for as long as it takes, and starts the flash
  only once the clock is there; if the clock leaves fastboot first, it asks again. A failed flash ends with ✗,
  not ✓.
- **A USB pull during a BROM job** is reported as one ("the USB connection dropped. Nothing was written").
- **Restore's second BROM session** reads as the normal next step ("Now unplug the clock's round power plug: it
  goes into BROM mode once more, this time to write"), not "still held by the last try". No "Nothing yet" hint
  during the handover from BROM.
- **After the backup**, the fastboot wait asks you to unplug the clock first, instead of two boxes at once.
- **Magisk:** while you work in it, Cast's "Link Account" screen is stopped and Magisk brought back if it takes
  over. A rerun reuses the boot image Magisk already patched.
- **Wi-Fi:** a too-short password asks only for the password again; the message about the work folder names the
  password you typed; the Wi-Fi name stays out of the log.
- **Log:** "token:" in a sentence is no longer masked; lines carried to a redrawn box aren't logged twice.

### Fixes from the sixth end-to-end run (planned faults)
- **Losing the clock no longer stops setup:** a power cut, restart or Wi-Fi drop in a step over Wi-Fi now says so,
  waits for the clock to come back, and starts that step again (up to 3 times). Errors read as words, not command
  lists.
- **A clock still held by the last BROM session** is caught first: the tool asks you to unplug it and waits.
  0-byte reads count as a failed try. Each retry says why it failed and starts on a fresh screen ("Try 2 of 5").
- **Ctrl-C during a BROM job only asks** the first time (press again within 5 s to stop); mtkclient can't be
  killed by it by accident. The guide says to copy with Ctrl+Shift+C.
- **BROM with VOLUME UP alone**, as every catch in the run used.
- **Magisk stays in front:** Cast is stopped before Magisk opens. The whole system is set to landscape, the
  right way up, after the clock is found, so nothing comes up sideways.
- **Typed answers** go after a short `>` on their own line, with line editing (no more backspacing problems past a
  wrapped line). One spinner draws at a time; the bar no longer lands inside a cut-off line.
- **The log records** what the tool said and asked, and the answers (never a password), and each BROM try with
  its reason.
- **YES** and **NO** in bold; the address box names the setup screen's IP= line and "Can't connect to Wi-Fi"; the
  flash's closing line says which Wi-Fi it joins; the backup note doesn't promise a Wi-Fi password.
- **Docs:** never factory reset to go back; use `lck restore`.

### Fixes from the fifth end-to-end run
- **Wi-Fi is a full stop in step 1.** After the flash the clock can only be reached over Wi-Fi, on the network
  it's connected to when backed up: a network it once saved isn't enough. Step 1 asks: "Is the clock booted and
  connected to Wi-Fi? If not, connect it to Wi-Fi using the Google Home app before typing YES. Or you can type
  NO, and enter your Wi-Fi name and password in the tool later." With NO, the name and password (not shown as you
  type, never logged, never saved in the progress file) are added to the clock's user data before the flash. With
  YES, the backup is still checked for a saved network. Adding the network is untested on a clock so far.
- **Mistyped Wi-Fi can be fixed in place:** if the clock starts but can't join (no IP address under About device),
  type wifi at the address question to enter the name and password again. The tool rebuilds the user data from
  the backup with only the new details and writes just that again, in fastboot mode, as many times as needed.
- **One "On the clock" box at a time:** a new box in the same step redraws the screen, and carries over what was
  said just before it.
- **Magisk:** **tap OK** in bold, "Touch nothing else", 3 minutes for its restart; the sideways note in the Shell
  box too.
- **Addresses and links stand out** (cyan, underlined), and doc links go on a line of their own.
- **A backup folder with no zip gets one** at the end, so "keep only the zip" works for older backups too.
- The address box reads as one instruction: "Then swipe up from the bottom, tap the gear (Settings), then About
  device: the address is under IP address."

### Fixes from the fourth end-to-end run
- **Switching Google off sticks:** Android saves `pm disable-user` 5 to 10 seconds later, and a restart sooner lost
  all four. The step now waits until the changes are saved, then restarts. The blank-screen note comes before the
  restart.
- **Magisk stays in front:** it's opened once the clock's screen has settled after a restart, and opened again if
  you press Enter without the grant. Its box says it may open sideways.
- **The address box says to wait for the clock face first**, right after a flash.
- **Spinners show their time from 0s,** and open before a slow first check.
- **BROM:** "Found BROM. Let go of the buttons" as soon as it shows; restore's bar no longer flashes full between
  partitions; "Found it" in a fastboot wait stays on screen.
- **Kiosk Satellite copies over with a progress bar** (MB of MB), then installs.
- **Only the backup zip is kept after a successful setup:** the folder was the same backup twice. It's removed
  only if the zip holds the same checksums and reads back whole. A later setup unpacks the zip rather than backing
  up the converted clock over the stock backup, and `lck restore backup/clock` finds the zip.
- **No `lck lock`:** relocking was looked at and left out. The way back after a lock depends on the leaked unlock
  credential working a second time, and on where the bootloader keeps the lock state.

### Fixes from the third end-to-end run
- **No more false "this computer account isn't allowed" stops** when the clock leaves BROM or fastboot (unplugged,
  or started normally): the tool looks again a moment later. A fastboot wait that sees BROM and then nothing says
  the clock started normally, and to hold VOLUME UP the whole time.
- **Cast:** the Magisk boot script is the only thing seen to keep it off, so the step uses it directly: one
  restart. The script is now installed correctly (the whole command reaches `su -c` as one argument).
- **The root step picks up where it stopped:** no new patch and fastboot trip when Magisk's boot image is running,
  no endless wait when Magisk doesn't ask for its extra setup, and Magisk is opened before each box.
- **Restart spinners** run from the moment of the restart, with the real time.
- **Bold:** the mode and the time a box waits for, and always unplug, plug it back in and replug.
- **Restore's second BROM box** says it's the second one, to write, on a fresh screen.
- The failure screen shows only this run's log lines; the GitHub message suggests switching the VPN; a saved
  address gets a minute to answer before the tool asks; "Setting up mtkclient's packages" comes after its
  download; What's next says how to add the clock in Home Assistant if it doesn't show up.

### Fixes from the second end-to-end run
- **Cast stays off.** Removing it for user 0 on its own only lasted until the next restart. The step now disables it
  before removing it for user 0, as on the first clock where it stayed off. If it comes back, it tries the first
  clock's exact order (with a restart in the middle), then a Magisk boot script; root now comes before this step
  so that last fallback is there.
- **After a flash, the replug box stays on screen** until the clock has left fastboot, instead of being replaced
  at once by the address question.
- **The address question says where to find it on the clock** (Settings, About device), and why the rest is over
  Wi-Fi. A converted clock adb already knows is found without `--ip`.
- **Clearer boxes:** the fastboot box names the mode and says passing through BROM is normal; "Found it" only after
  asking; Magisk's Shell grant matches v31.1 (+, Shell, back arrow); Unplug and Hold no buttons are bold; the blank
  screen before Kiosk Satellite is explained; Kiosk Satellite's settings import is mentioned.
- **The screen tells the truth:** a step passed over with `--from` shows as skipped, not ✓; a stop logs its own
  reason, so the log lines under it are about this stop; a stop that leaves the clock in BROM or fastboot says
  how to start it normally; the opening note leaves out a backup that's already there.
- **Restore:** one bar for the whole write, real times (about 15 minutes to write, 35 to back up).
- **GitHub's lookup limit:** `GITHUB_TOKEN` lifts it.

### The guided tool, `lck`
- **One command converts a clock:** `lck setup`, in 15 steps numbered like the [manual](devices/cube/README.md).
  It looks and works like the Smart Display's `lhk`: the same redrawn setup screen with a progress bar and every
  step ticked as it's done, yellow "On the clock" boxes for what to do with your hands, a live line for slow work,
  and the last lines of the log when something stops. Run the same command again and it carries on.
- **It finds the clock's mode by its USB IDs** (BROM, preloader, fastboot) and says when the buttons gave the
  preloader instead of BROM. mtkclient is started before you press the buttons, because BROM lasts only a few
  seconds.
- **It only talks to the clock over Wi-Fi at its own address**, and checks it's a gen-1 Smart Clock before
  writing anything. Other adb devices on the computer are never touched.
- **`lck backup`:** the whole clock in one BROM session (disk, both boot areas), compressed to about 0.7 GB and
  packed into one zip with its checksums. Setup makes one first and won't unlock without it.
- **`lck restore`:** checks the backup's checksums and that it's from this clock (disk GUID and layout, read in
  BROM), asks you to type restore, then writes back both slots, misc, userdata and the rest the conversion
  changes. It never writes the bootloader chain, keys or calibration.
- **Root with Magisk** is a setup step: the stock boot image is patched on the clock itself, and only `boot_a` is
  flashed. The stock image is kept as the undo.
- Every download is pinned with its SHA-256: platform-tools, uv, mtkclient, the unlock credentials, the fastboot
  helpers, Simple Keyboard and Magisk. Kiosk Satellite is the newest release, checked against its published
  digest and signing certificate.
- Docs in the Smart Display's layout: a README with a quick start, [the guide](docs/GUIDE.md), a
  [Linux guide](docs/LINUX.md) with a USB rule, and [time zones](docs/TIMEZONES.md).

### Earlier
- Kiosk Clock dashboard ([dashboard/](dashboard/README.md)): a Home Assistant dashboard strategy that finds the clock,
  its Music Assistant player and the house's rooms by itself, built-in cards only. Example YAML for hand editing.
- First conversion, by hand, of one Lenovo Smart Clock gen 1: stock Android Things on slot A with offline-provisioned
  userdata (adb over Wi-Fi, RescueParty off, OTA channel blocked), Kiosk Satellite installed as device owner,
  Google Assistant, Cast and check-ins disabled.
- [Manual steps](devices/cube/README.md), each with its check.
- `lck` package: misc A/B and OTA-channel builder, sparse-image size check, userdata injector, with tests.
