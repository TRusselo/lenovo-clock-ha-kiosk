# Findings: Lenovo Smart Clock gen 1 (CD-24501F, "cube")

Evidence labels: **Observed** (read on a real clock or its backup), **Reported** (someone else's write-up),
**Guess** (not yet checked).

## The device

- MediaTek MT8167S (LK reports MT8167A), 4× Cortex-A35, 1 GB RAM, 8 GB eMMC (Hynix H8G4a2), 480×800 panel
  shown landscape. Android Things 8.1, **32-bit userspace** on a 64-bit 4.4.95 kernel. **Observed.**
- `ro.treble.enabled=false`, no VNDK, monolithic sepolicy: **no GSIs.** **Observed.**
- USB-A port is host-only under Android (OTG ID pulled low): **no USB adb.** Fastboot, BROM and the preloader
  switch it to device mode. **Reported** (joserodpt, kortregt), consistent with what we saw.
- Lenovo's Things app (`com.google.android.things.sparrow.oem`, in `/oem/app`) drives the amp, mic ADC, volume
  keys, mute slider and light sensor through the Things peripheral API. **Reported** (S7ruc7ureV01d).

## Partitions and slots

- A/B, system-as-root, no recovery partition. `system` 512 MiB, `vendor` 64 MiB, `oem` 500 MiB per slot.
  `userdata` is the last partition: **0x13fe56e00 bytes, not a multiple of 4 KiB** (1,310,294 whole blocks).
  img2simg rounds up, which would write 512 bytes into the backup GPT. Truncate first. **Observed.**
- `lk`, `tee` and `oem_bootloader` are identical on both slots. **Observed** on one clock.
- Stock slots on one clock: A = `OIMJ.201212.001`, B = `OIMK.211005.001`. **Observed.**
- **The unlock wipes userdata** (it read back all zeros). Stock fstab has no `formattable`, so a zeroed
  userdata isn't reformatted on boot. **Observed** (zeros); the boot behaviour is a **Guess**, not tested.

## misc

- `0x800`: libavb `AvbABData` (`\0AB0`, big-endian CRC32 over 28 bytes). **Observed.**
- `0x820`: `update_channel[128]` of `bootloader_message_ab`. Android Things' `update_engine` reads its target
  channel here; empty means `ro.update.default_channel` (`stable-channel`). Writing `none-channel` there
  offline: `update_engine` logged "Running from channel none-channel". **Observed.**
- After the first good boot, `update_engine` logged "Marking booted slot as good". **Observed.**

## OTA

- Google's update server was still answering the clock on 2026-11-06 (`update_engine` prefs). An A/B update
  installs to the **other** slot. **Observed.** Block it before the clock ever gets network.

## userdata provisioning

- Stock userdata is plaintext ext4 and needs `e2fsck -fy` (journal replay) before `debugfs` reads it.
  **Observed.**
- Injected, with owners and SELinux labels copied from stock (labels are NUL-terminated):
  `adb_enabled=1` in `system/users/0/settings_global.xml`, `property/persist.adb.tcp.port=5555`,
  `property/persist.sys.usb.config=adb`, `property/persist.sys.disable_rescue=true`, `misc/adb/adb_keys`.
  adb over Wi-Fi came up with the injected key, no prompt. **Observed.**
- `persist.sys.disable_rescue`: Cast (`mediashell`) crash-loops on unlocked clocks (**Reported**, joserodpt).
  On a user build, RescueParty would reset settings (adb off) and then ask for a wipe. The property disables it
  (`RescueParty.isDisabled`, AOSP). **Guess** that it was needed; harmless either way.
- The saved Wi-Fi network in the backup's userdata reconnected on first boot. **Observed.**

## Software on stock

- WebView `com.android.webview` 87.0.4280.101, the only provider allowed by `config_webview_packages`, with no
  signature check. **Observed.** Renders HA fine on the Smart Display (**Reported**, lhk).
- No Google account on the clock, so `dpm set-device-owner` works. **Observed.**
- Disabled with `pm disable-user --user 0`: `com.google.android.apps.mediashell`, `com.google.assistant.launcher`,
  `com.google.assistant.core`, `com.google.android.things.checkin`, `com.google.android.things.customcheckin`.
  `sparrow.oem` kept running afterwards. **Observed.**

## Time zones

- Stock `persist.sys.timezone` on one clock was `Etc/GMT+6` (fixed UTC−6). WebView carries its own ICU time-zone
  data, so a named zone whose rules changed after the WebView build stays wrong in dashboards. A fixed-offset
  zone (`Etc/GMT+6`, `America/Regina`) avoids stale DST rules in both Android and WebView. **Guess** for the
  clock; same reasoning as the Smart Display's finding.

## Open

- Volume keys, light sensor and mute slider under Kiosk Satellite (Things app still running): untested.
- Mic capture for the voice satellite: untested.
- Whether LK falls back to slot B after three failed slot-A boots: untested.
