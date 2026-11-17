# Findings: Lenovo Smart Clock gen 1 (CD-24501F, "cube")

Evidence labels: **Observed** (read on a real clock or its backup), **Reported** (someone else's write-up),
**Guess** (not yet checked).

## The device

- MediaTek MT8167S (LK reports MT8167A), 4× Cortex-A35, 1 GB RAM, 8 GB eMMC (Hynix H8G4a2), 480×800 panel
  shown landscape. Android Things 8.1, **32-bit userspace** on a 64-bit 4.4.95 kernel. **Observed.**
- `ro.treble.enabled=false`, no VNDK, monolithic sepolicy: **no GSIs.** **Observed.**
- USB-A port is host-only under Android (OTG ID pulled low): **no USB adb.** Since observed (e2e run 2, with
  root): with the cable that carried fastboot minutes earlier, Android's device controller reports `not attached`
  and the kernel logs `usb_state<DISCONNECTED>` every 5 s, while `sys.usb.config=adb`. Fastboot, BROM and the preloader
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
- **Disabled persistent apps still start at boot.** `mediashell` and `assistant.core` came back after a reboot
  with `enabled=3`, and `am force-stop` can't stop them as `shell`. `mediashell` grew to ~594 MB PSS
  (Cast preloading app `2DB7CC49` in a loop), leaving ~118 MB; WebView renderers and then Kiosk Satellite were
  killed. `pm uninstall -k --user 0` works as `shell` (installed=false). `pm hide` needs `MANAGE_USERS`
  (refused). **Observed.**
- **Correction (e2e run 2, 2026-11-16): the user-0 uninstall of Cast alone doesn't last.** It answers `Success`,
  but after every restart Cast is installed for user 0 again (`installed=true`, read before and after two
  restarts). **The order seems to matter:** on the first clock Cast was `disable-user`'d first (04:17), the clock
  restarted, then it was uninstalled for user 0 (05:00), and it stayed gone through every restart after
  (`cast-gone` at 06:23, after the Magisk flashes). **But e2e run 3 (2026-11-16) did exactly that order on the
  same clock** (disable, restart, uninstall, restart) and Cast was back after the last restart. So the order isn't
  what kept it off on the first clock; what did is unknown. **Observed.** Android Things seems to reinstall the `/oem` apps
  for user 0 at boot (**Guess**: the log doesn't say why). `pm disable` as root doesn't stop it either
  (persistent: started at boot anyway). **What works, observed:** a Magisk `post-fs-data.d` script that
  bind-mounts an empty folder over `/oem/app/com.google.android.apps.mediashell`. After a restart Cast isn't
  listed or running, `sparrow.oem` runs, about 257 MB RAM free. Again in run 3. It's the only thing seen to keep
  Cast off on every try, so the tool uses it directly, and root comes before the degoogle step.
- Light sensor and volume keys work under Kiosk Satellite with `sparrow.oem` running: no bootfix needed.
  Kiosk Satellite comes up rotated wrong; its forced-landscape setting fixes it. **Observed** (by the person).

## Time zones

- Stock `persist.sys.timezone` on one clock was `Etc/GMT+6` (fixed UTC−6). WebView carries its own ICU time-zone
  data, so a named zone whose rules changed after the WebView build stays wrong in dashboards. A fixed-offset
  zone (`Etc/GMT+6`, `America/Regina`) avoids stale DST rules in both Android and WebView. **Guess** for the
  clock; same reasoning as the Smart Display's finding.

## Keyboard

- Stock clock has no input method (`ime list -a` empty; the Smart Display's Things build ships one). Simple
  Keyboard 149 installs and `ime enable`/`ime set` work as `shell`. **Observed.**

## Root

- Magisk v31.1 (32-bit, minSdk 24) on the stock OIMK boot image, patched with `LEGACYSAR` (kernel
  `skip_initramfs` → `want_initramfs`) and `KEEPVERITY`: boots, `magiskd` runs as root, `magisk -v` →
  `31.1:MAGISK:R`. No fstab in the boot DTB. System untouched; Kiosk Satellite and `sparrow.oem` unaffected.
  **Observed.**
- `pm hide` needs `MANAGE_USERS` and `am force-stop` can't stop persistent apps as `shell`: root is what makes
  those possible. **Observed.**

## Open

- Mute slider under Kiosk Satellite: untested.
- Mic capture for the voice satellite: untested.
- Whether LK falls back to slot B after three failed slot-A boots: untested.

## USB modes, seen from the computer

- When the clock leaves BROM or fastboot (it starts normally, or is unplugged), its device file under
  `/dev/bus/usb` goes at once. A scan that lists it and then checks its permissions can find it gone in between,
  which reads like a permission problem. Seen 2 of 3 times in e2e run 3. **Observed.**
- Holding VOLUME UP passes through BROM at 3 to 5 seconds and reaches fastboot at about 8 to 10. Letting go in
  between lets the clock start normally, and then its USB shows nothing. **Observed.**
