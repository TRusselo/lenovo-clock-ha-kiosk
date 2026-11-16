# Changelog

What changed for people converting a clock. Newest first.

## Unreleased

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
