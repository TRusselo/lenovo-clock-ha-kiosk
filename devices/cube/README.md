# Lenovo Smart Clock gen 1 (CD-24501F, "cube"): manual steps

The steps that converted the first clock, in order. Each step says what to do and **how to check it worked**:
don't go on until the check passes. There's no guided tool yet; these steps are what it will run.

> [!CAUTION]
> This unlocks verified boot and rewrites partitions. **You need a full eMMC backup taken before unlocking**
> (step 2): unlocking wipes userdata, and step 5 rebuilds it from that backup. A clock that was unlocked without
> a userdata backup isn't supported yet.

**Conventions:**
- Commands run on a Linux computer, in a terminal, from this repo's folder. `adb` and `fastboot` are Google's
  platform-tools. Third-party tools are pinned in [`manifest.json`](../../manifest.json).
- **Power:** the clock runs on its barrel jack, not USB. With USB alone the chip stays off.
- **Cable:** USB **A-to-A**, straight-through.
- **Modes** (the screen stays dark in all of them; unplug the barrel power, hold the buttons, plug in):
  - **fastboot:** hold **Vol+**, keep holding 20 to 30 seconds. USB ID `0bb4:0c01`.
  - **BROM:** hold **Vol+ and Vol− together**, about 5 seconds. `0e8d:0003` (it can take a few tries). Vol− alone
    gives the preloader, `0e8d:2000`, which mtkclient handles badly: power-cycle and try BROM again.
  - One mtkclient command per BROM power-cycle.
- **LK's fastboot hangs** on the `getvar` queries the stock `fastboot` client sends. Start the command first,
  then enter fastboot: `fbmulti.py` waits up to 5 minutes for the clock.
- **Never write** the preloader, `lk_*`, `tee_*`, `oem_bootloader_*`, `seccfg`, `proinfo`, `nvram` or the GPT.

## 1. `prerequisites`: tools and space

- About **15 GB free**, `e2fsprogs` (`debugfs`, `e2fsck`), `img2simg`, `zstd`, `nmap`, platform-tools.
- A Python venv with `pyusb` for mtkclient and `fbmulti.py` (manifest: `mtkclient`, `fbmulti.py`,
  `sparsesplit.py`).

**Check:** `debugfs -V`, `img2simg` (prints usage), `adb version`, `venv/bin/python -c 'import usb'`.

## 2. `backup`: full eMMC backup, before unlocking

In BROM, from the mtkclient folder (one command per power-cycle):

```bash
python mtk.py printgpt > backup/gpt.txt
python mtk.py rf backup/full_user.bin          # whole user area, about 30 to 40 minutes
python mtk.py r preloader backup/boot1.bin --parttype boot1
python mtk.py r preloader backup/boot2.bin --parttype boot2
zstd -T0 -10 --long=27 backup/full_user.bin -o backup/full_user.bin.zst
```

The full image is what matters: it includes **userdata**. kortregt's `split_dump.py` skips userdata unless you
pass `--include-userdata`.

**Check:** `gpt.txt` lists 31 partitions ending in `userdata`; `zstd -t --long=27` passes; keep SHA-256 sums.
**Keep the backup private.** It holds per-device keys and your Wi-Fi password.

## 3. `unlock`: bootloader and AVB

In fastboot:

```bash
fastboot flashing unlock
python at_auth_unlock.py -v cube_unlock_credentials_v2.zip
```

**Check:** `fastboot getvar unlocked` is `yes`; `getvar at-vboot-state` shows `avb-locked: 0`
(`bootloader-locked: 1` stays, that's normal).

## 4. `images`: the stock slot set

Split the backup into partitions (kortregt's `tools/split_dump.py`, with `--include-userdata`). Pick the stock
slot whose build matches the backup's userdata: read `ro.build.id` from each `system_*.bin`, and the
fingerprint in userdata's `system/packages.xml`. On the first clock that was slot B, `OIMK.211005.001`.

From that slot: `boot`, `vendor`, `vbmeta`, `system`, `oem`. Make sparse pieces of the big ones:

```bash
img2simg system_b.bin system.simg && python sparsesplit.py system.simg system_chunk 94371840
img2simg oem_b.bin oem.simg       && python sparsesplit.py oem.simg oem_chunk 94371840
```

**Check:** each source matches the backup's SHA-256, and
`python -m lck.sparse backup/gpt.txt system_a system_chunk.*` (and `oem_a`) print OK.

## 5. `userdata`: provision adb over Wi-Fi offline

```bash
cp --sparse=always userdata.bin userdata_things.img
e2fsck -fy userdata_things.img                     # replays the journal; exit code 1 is fine
python -m lck.userdata userdata_things.img ~/.android/adbkey.pub
truncate -s $((1310294*4096)) userdata_things.img  # whole blocks only
e2fsck -fn userdata_things.img
img2simg userdata_things.img userdata.simg && python sparsesplit.py userdata.simg userdata_chunk 94371840
```

This switches on adb over Wi-Fi with your key, and switches off RescueParty. The clock reconnects to the Wi-Fi
saved in the backup; if that network is gone, it has no network and no adb.

**Check:** `e2fsck -fn` is clean; `python -m lck.sparse backup/gpt.txt userdata userdata_chunk.*` prints OK.
`userdata_things.img` holds your Wi-Fi password: don't share it.

## 6. `misc`: boot slot A, block updates

```bash
python -m lck.misc misc.bin misc.img
```

Slot A gets priority 15 with 3 tries, slot B stays the fallback, and the update channel becomes `none-channel`,
so Google can't install an update over slot B.

**Check:** it prints `slot A Slot(priority=15, tries=3, successful=False)` and `channel none-channel`.

## 7. `flash`: slot A, userdata, misc

Start this, **then** put the clock in fastboot:

```bash
python fbmulti.py boot.img=boot_a vendor.img=vendor_a vbmeta.img=vbmeta_a \
  system_chunk.0=system_a system_chunk.1=system_a system_chunk.2=system_a system_chunk.3=system_a system_chunk.4=system_a \
  oem_chunk.0=oem_a oem_chunk.1=oem_a oem_chunk.2=oem_a \
  userdata_chunk.0=userdata userdata_chunk.1=userdata \
  misc.img=misc
```

`misc` goes last: if anything before it fails, the clock still boots slot B.

**Check:** `ALL DONE`, about 4 minutes. Then unplug and replug the power with no buttons held. The first boot
takes a few minutes.

## 8. `connect`: adb over Wi-Fi

Find the clock: your router's DHCP list, or `nmap -p5555 --open <your-subnet>/24` and then
`adb connect <clock-ip>:5555`, `adb shell getprop ro.product.device` → `mt8167s_som`.

**Check:**
- `adb shell getprop ro.boot.slot_suffix` → `_a`
- `adb shell update_engine_client --show_channel` → `none-channel`
- `adb logcat -d -s update_engine | grep "Marking booted slot as good"` finds a line

## 9. `degoogle`: Cast, Assistant, check-ins

Cast (`mediashell`) and Assistant core are **persistent** apps: `disable-user` alone doesn't stop them, Android
starts them again at boot, and Cast grew to about 594 MB of the clock's 1 GB, starving WebView until Kiosk
Satellite was killed. Uninstall Cast for user 0 instead (the APK stays; reversible), and disable the rest:

```bash
adb shell pm uninstall -k --user 0 com.google.android.apps.mediashell
for p in com.google.assistant.launcher com.google.assistant.core \
         com.google.android.things.checkin com.google.android.things.customcheckin; do
  adb shell pm disable-user --user 0 $p
done
adb reboot
```

Undo Cast: `adb shell cmd package install-existing com.google.android.apps.mediashell`.

**Never disable** `com.google.android.things.sparrow.oem`: on the clock it drives the light sensor and volume keys.

**Check, after the reboot:** `adb shell pidof com.google.android.apps.mediashell` prints nothing;
`adb shell pidof com.google.android.things.sparrow.oem` prints a number.

## 10. `kiosk-install`: Kiosk Satellite

Download the newest `*armeabi-v7a.apk` from Kiosk Satellite's releases. Check its digest (release API) and its
signing certificate `2a77134ce11f65ba9d08bed669fdcb742a25633c5665a6135aeca30040b068fc`
(`apksigner verify --print-certs`). Then `adb install -r -g kiosk-satellite-VERSION.armeabi-v7a.apk`.

**Check:** `adb shell pm path me.jxl.kiosk_satellite` prints a `package:` line.

## 11. `kiosk-grants`: permissions and device owner

```bash
adb shell appops set me.jxl.kiosk_satellite REQUEST_INSTALL_PACKAGES allow
adb shell appops set me.jxl.kiosk_satellite WRITE_SETTINGS allow
adb shell appops set me.jxl.kiosk_satellite SYSTEM_ALERT_WINDOW allow
adb shell appops set me.jxl.kiosk_satellite GET_USAGE_STATS allow
adb shell dumpsys deviceidle whitelist +me.jxl.kiosk_satellite
adb shell cmd notification allow_listener me.jxl.kiosk_satellite/.MediaSessionListener
adb shell dpm set-active-admin me.jxl.kiosk_satellite/.KioskAdminReceiver
adb shell dpm set-device-owner me.jxl.kiosk_satellite/.KioskAdminReceiver
```

**Check:** `adb shell dumpsys device_policy` shows Kiosk Satellite under `Device Owner`.

## 12. `kiosk-settings`: setup wizard and settings

1. Go through Kiosk Satellite's setup wizard on the clock or at `http://<clock-ip>:2324`, and press **Finish**.
2. Set `home.enabled=true` ("Act as the home screen"). Without it, Android falls back to the Things launcher.
3. Turn on Kiosk Satellite's **forced landscape** override: by default it comes up rotated wrong on the clock's
   portrait panel.
4. Leave `audio.mic_gain_db` at its default: the clock keeps Google's own mic setup. Raise it only if the wake
   word misses from across the room.

**Never edit the app's settings file directly**, and never `pm clear` it.

**Check:** `adb shell dumpsys activity activities | grep mResumedActivity` shows `me.jxl.kiosk_satellite`; the
light sensor changes brightness and the volume keys change the volume (Lenovo's Things app handles both).

## Restore

- **Back to Alpine or whatever is on slot B:** `python fbmulti.py cmd:set_active:b`, then enter fastboot.
- **Back to stock exactly as backed up:** write `full_user.bin` back with mtkclient in BROM.
