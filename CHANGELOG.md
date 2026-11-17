# Changelog

What changed for people converting a clock. Newest first.

## Unreleased

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
