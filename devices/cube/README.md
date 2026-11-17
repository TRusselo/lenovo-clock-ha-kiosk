# Lenovo Smart Clock gen 1 (CD-24501F, "cube"): manual steps

These are the same steps `lck setup` runs, in the same order. Each step says what to do and **how to check it
worked**: don't go on until the check passes. The guided tool does all of this for you; use this page if you'd
rather do it by hand, or if you're an AI agent following along.

> [!CAUTION]
> This unlocks verified boot and rewrites partitions. **You need a full backup taken before unlocking**
> (step 2): unlocking wipes the clock's user data, and step 5 rebuilds it from that backup. A clock that was
> unlocked without a backup isn't supported.

**Conventions:**
- Commands run on a Linux computer, in a terminal, from this repo's folder. `adb` and `fastboot` are Google's
  [platform-tools](https://developer.android.com/tools/releases/platform-tools). Third-party tools are pinned in
  [`manifest.json`](../../manifest.json).
- **Power:** the clock runs on its round barrel plug, not USB. With USB alone the chip stays off.
- **Cable:** USB **A-to-A**, straight-through (no chip in the middle), into the clock's USB port.
- **Modes.** The screen stays **dark** in all of them. Unplug the barrel power, hold the button(s), plug the power
  in. Find the mode from the USB device list (`lsusb`):
  - **fastboot:** hold **VOLUME UP**, keep holding 20 to 30 seconds. `0bb4:0c01`.
  - **BROM:** hold **VOLUME UP and VOLUME DOWN together**, about 5 seconds. `0e8d:0003`. It often takes two or
    three tries. One button alone gives the preloader, `0e8d:2000`, which mtkclient handles badly on this clock:
    unplug and try again.
  - One mtkclient command per BROM power-cycle. Several commands go in one with `mtk.py multi "a;b"`.
  - The clock never shows up as adb on USB: under Android its port is host-only. adb runs over Wi-Fi.
- **LK's fastboot hangs** on the `getvar` queries the stock `fastboot` client sends before a flash. Flash with
  `fbmulti.py`, and start it first, then enter fastboot: it waits up to 5 minutes for the clock.
- **Never write** the preloader, `lk_*`, `tee_*`, `oem_bootloader_*`, `seccfg`, `proinfo`, `nvram`, `kb`,
  `dkb` or the GPT. `oem_bootloader` is LK's device-tree table: anything else there hangs the clock in the
  preloader.

## 1. `prerequisites`: this computer and the tools

- About **20 GB free**, `e2fsprogs` (`debugfs`, `e2fsck`), `img2simg` (`android-sdk-libsparse-utils` on Debian and
  Ubuntu, `android-tools` on Arch and Fedora), `zstd` and libusb-1.0.
- USB access without root: [docs/LINUX.md](../../docs/LINUX.md), step 3.
- Download and check: platform-tools, [uv](https://github.com/astral-sh/uv), mtkclient, the AVB unlock zip,
  `at_auth_unlock.py`, `fbmulti.py`, `sparsesplit.py`, Simple Keyboard, Magisk and Kiosk Satellite.
- mtkclient runs with `uv run --python 3.12 --with pyusb --with pycryptodome --with pycryptodomex --with colorama
  --with pyserial --with keystone-engine --with capstone --with unicorn python mtk.py ...` (its own
  `requirements.txt` pulls in a GUI you don't need).

**Check:** `debugfs -V`, `img2simg` (prints usage), `zstd -V`, `adb version`.

## 2. `backup`: the whole clock, before unlocking

In BROM, from a new folder, in one session:

```bash
python mtk.py multi "rf full_user.bin;r preloader boot1.bin --parttype boot1;r preloader boot2.bin --parttype boot2"
```

The whole user area (every partition, **userdata included**) takes about 35 minutes. Then:

```bash
head -c 17408 full_user.bin > gpt.bin
sha256sum full_user.bin > SHA256SUMS
zstd -T0 -10 --long=27 full_user.bin -o full_user.bin.zst && zstd -t --long=27 full_user.bin.zst
sha256sum full_user.bin.zst gpt.bin boot1.bin boot2.bin >> SHA256SUMS
rm full_user.bin
```

`lck backup` does the same at any time, for example before trying something new.

**Check:** `gpt.bin` holds 29 partitions ending in `userdata`; `zstd -t` passes. **Keep the backup private.**
It holds the clock's own keys and your Wi-Fi password.

## 3. `unlock`: bootloader and verified boot

In fastboot:

```bash
fastboot flashing unlock
python at_auth_unlock.py -v cube_unlock_credentials_v2.zip     # from Cube-AVB-factory-Unlock.zip
```

**Check:** `fastboot getvar unlocked` is `yes`; `getvar at-vboot-state` shows `avb-locked: 0`
(`bootloader-locked: 1` stays, that's normal). Unlocking wiped userdata.

## 4. `images`: the stock slot set

Split the backup into partitions with its own partition table. Pick the stock slot whose build matches the
backup's userdata: `ro.build.id` from each slot's system (`debugfs -R "cat /system/build.prop" system_a.bin`),
and the fingerprint in userdata's `/system/packages.xml` (after `e2fsck -fy` on a copy). On the first clock that
was slot B, `OIMK.211005.001`.

From that slot: `boot`, `vendor`, `vbmeta`, `system`, `oem`. Keep the stock `boot` as `boot-stock.img`: it's the
undo for step 14. Make sparse pieces of the big ones:

```bash
img2simg system_b.bin system.simg && python sparsesplit.py system.simg system_chunk 94371840
img2simg oem_b.bin oem.simg       && python sparsesplit.py oem.simg oem_chunk 94371840
```

**Check:** each piece's sparse header declares no more than its partition's size
(`python -m lck.sparse gpt.txt system_a system_chunk.*`, with mtkclient's `printgpt` output).

## 5. `userdata`: switch on adb over Wi-Fi, offline

**First, before the backup (step 2), the clock must be booted and connected to a Wi-Fi that works now** (the Google
Home app sets that up): once Android runs, the clock's USB is host-only, so after the flash the only way in is
Wi-Fi, and the only network it knows is the one in its user data. A network it saved once isn't enough. `lck`
asks this as a YES/NO in step 1; NO means the name and password are typed in, and added to the user data here.
As a check: `debugfs -R "cat /misc/wifi/WifiConfigStore.xml" userdata_copy.img | grep -c '<Network>'` (after
`e2fsck -fy` on a copy) must be 1 or more. If it's 0, either set the clock up with the Google Home app and back it
up again, or add a WPA2 network to the copy: `lck.userdata.add_wifi(img, name, password)` writes one entry in the
shape Android Things 8.1 uses (owner 1000:1000, mode 0600, label `wifi_data_file`). **Untested on a clock:** the
tool's own way of adding it, found needed in e2e run 5. If a typed network was wrong, the clock starts but has no
IP address: rebuild this step's image from the backup with the right details and flash only `userdata` again
(`lck` offers this at the address question: type wifi).


```bash
cp --sparse=always userdata.bin userdata_things.img
e2fsck -fy userdata_things.img                     # replays the journal; exit code 1 is fine
python -m lck.userdata userdata_things.img ~/.android/adbkey.pub
truncate -s $((1310294*4096)) userdata_things.img  # whole blocks only
e2fsck -fn userdata_things.img
img2simg userdata_things.img userdata.simg && python sparsesplit.py userdata.simg userdata_chunk 94371840
```

This switches on adb over Wi-Fi with your computer's key, and switches off RescueParty. The clock reconnects to
the Wi-Fi saved in the backup; if that network is gone, it has no network and no adb.

**Check:** `e2fsck -fn` is clean. `userdata_things.img` holds your Wi-Fi password: don't share it.

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

`misc` goes last: if anything before it fails, the clock still starts slot B.

**Check:** `ALL DONE`, about 4 minutes. Then unplug and replug the power with no buttons held. The first start
takes a few minutes.

## 8. `connect`: adb over Wi-Fi

Find the clock's address in your router's list of devices (it may show as "Android"), then
`adb connect <clock-ip>:5555`. On the clock itself: swipe up from the bottom, the gear (Settings), About device,
IP address. Always pass `-s <clock-ip>:5555`, so nothing else connected is touched.

**Check:**
- `adb -s <clock-ip>:5555 shell getprop ro.product.device` → `mt8167s_som`
- `... getprop ro.boot.slot_suffix` → `_a`
- `... update_engine_client --show_channel` → `none-channel`

## 9. `root`: Magisk

Root comes before switching Google off: keeping Cast off needs it (step 10). Most people expect root anyway. The clock has no userdebug build, so the Smart Display's `adb root` route doesn't exist
here, and **`adb disable-verity` would likely stop it starting** (LK refuses a vbmeta with verity flags set).
Magisk patches only `boot_a`; system and verity stay untouched.

1. Patch the stock boot image (`boot-stock.img` from step 4) **on the clock**, as the Magisk app does: push
   Magisk v31.1's `assets/boot_patch.sh`, `assets/util_functions.sh` and its `lib/armeabi-v7a/` binaries
   (`libmagiskboot.so` as `magiskboot`, and so on, plus an empty `stub.apk`) to `/data/local/tmp/lck-magisk`,
   then run `KEEPVERITY=true KEEPFORCEENCRYPT=true LEGACYSAR=true sh boot_patch.sh boot.img` there and pull
   `new-boot.img`. `LEGACYSAR` matters: LK always passes `skip_initramfs`, and Magisk's kernel patch turns it into
   `want_initramfs` so its ramdisk loads.
2. Flash only `boot_a`: start `python fbmulti.py new-boot.img=boot_a`, then enter fastboot. Power-cycle.
3. `adb install Magisk-v31.1.apk`, then open it. It shows **Requires Additional Setup**: tap OK; the clock
   restarts. Until then `magiskd` rejects every `su`.
4. Run `adb shell su -c id` once (it's rejected and logged), then in Magisk's **Superuser** tab tap **+**, tick
   **Shell**, and tap the back arrow (top left): Shell is then listed with its switch on. Avoid the Modules tab's file picker: it has no way out on the clock's screen (`adb shell input keyevent
   KEYCODE_BACK` twice gets out). Close the "patch" popup: boot is already patched.

**Check:** `adb shell magisk -v` prints `31.1:MAGISK:R`; `adb shell su -c id` prints `uid=0(root)`;
`pidof com.google.android.things.sparrow.oem` prints a number.

## 10. `degoogle`: Cast, Assistant, check-ins

Cast (`mediashell`) and Assistant core are **persistent** apps: `disable-user` alone doesn't stop them, Android
starts them again at boot, and Cast grew to about 594 MB of the clock's 1 GB, starving WebView until Kiosk
Satellite was killed. **Uninstalling Cast for user 0 doesn't last:** it answers `Success`, but in e2e runs 2 and 3
Cast was back for user 0 after the next restart, whether or not it was disabled first and with or without a
restart between. What lasts, with root (step 9): a Magisk boot script that mounts an empty folder over Cast's app
folder before Android looks there. Note the quotes: `su -c` must get the whole command as one argument, or only
its first part runs as root.

```bash
cat > hide-cast.sh <<'EOS'
#!/system/bin/sh
# lck: hide Cast from Android. Undo: delete this file and /data/adb/empty-cast, then restart.
mkdir -p /data/adb/empty-cast
mount -o bind /data/adb/empty-cast /oem/app/com.google.android.apps.mediashell
EOS
adb push hide-cast.sh /data/local/tmp/
adb shell "su -c 'mkdir -p /data/adb/post-fs-data.d /data/adb/empty-cast && cp /data/local/tmp/hide-cast.sh /data/adb/post-fs-data.d/ && chmod 755 /data/adb/post-fs-data.d/hide-cast.sh && rm /data/local/tmp/hide-cast.sh'"
for p in com.google.assistant.launcher com.google.assistant.core com.google.android.things.checkin \
         com.google.android.things.customcheckin com.google.android.apps.mediashell; do
  adb shell pm disable-user --user 0 $p
done
adb reboot
```

Undo Cast: delete `/data/adb/post-fs-data.d/hide-cast.sh` and `/data/adb/empty-cast`, restart, then
`adb shell pm enable com.google.android.apps.mediashell`.

Until Kiosk Satellite is installed (step 12), the screen stays blank or shows four dots: Assistant's home screen
is off. Swiping up still opens the settings.

**Never disable** `com.google.android.things.sparrow.oem`: on the clock it drives the light sensor and volume keys.

**Check, after the restart:** `adb shell pm list packages com.google.android.apps.mediashell` prints nothing;
`adb shell pidof com.google.android.things.sparrow.oem` prints a number.

## 11. `keyboard`: an on-screen keyboard

The clock's Android Things has **no input method at all**, so text fields on the screen can't be typed into.
Install [Simple Keyboard](https://github.com/rkkr/simple-keyboard) (release 149, pinned in the manifest):

```bash
adb install -r simple-keyboard-149.apk
adb shell ime enable rkr.simplekeyboard.inputmethod/.latin.LatinIME
adb shell ime set rkr.simplekeyboard.inputmethod/.latin.LatinIME
```

**Check:** `adb shell settings get secure default_input_method` prints
`rkr.simplekeyboard.inputmethod/.latin.LatinIME`.

## 12. `kiosk-install`: Kiosk Satellite

Download the newest `*armeabi-v7a.apk` from Kiosk Satellite's releases. Check its digest (release API) and its
signing certificate `2a77134ce11f65ba9d08bed669fdcb742a25633c5665a6135aeca30040b068fc`
(`apksigner verify --print-certs`). Then `adb install -r -g kiosk-satellite-VERSION.armeabi-v7a.apk`.

**Check:** `adb shell pm path me.jxl.kiosk_satellite` prints a `package:` line.

## 13. `kiosk-grants`: permissions and device owner

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

## 14. `kiosk-settings`: setup wizard and settings

1. Go through Kiosk Satellite's setup wizard on the clock or at `http://<clock-ip>:2324`, and press **Finish**.
2. Turn on "Act as the home screen" (`home.enabled`). Without it, Android falls back to the Things launcher.
3. Turn on Kiosk Satellite's **forced landscape** override if it comes up sideways on the clock's portrait panel.
4. Leave the mic gain at its default: the clock keeps Google's own mic setup.

**Never edit the app's settings file directly**, and never `pm clear` it.

**Check:** `adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c
android.intent.category.HOME` ends with `me.jxl.kiosk_satellite/...`; the light sensor changes brightness and the
volume keys change the volume (Lenovo's Things app handles both).

## 15. `verify`: final check

**Check:** slot `_a`, channel `none-channel`, Kiosk Satellite is device owner, Cast isn't running and Lenovo's
Things app is.

## Restore

- **A backup, back on its own clock:** `lck restore backup/clock.zip`. It reads the clock's partition table in
  BROM first and stops unless the disk GUID and layout match the backup. Then, in BROM again, it writes `boot`,
  `vbmeta`, `vendor`, `system` and `oem` on both slots, `misc`, `frp`, `metadata` and `userdata`. It never writes
  the bootloader chain or the clock's keys and calibration. The bootloader stays unlocked.
- **Back to the converted clock before root:** flash `boot-stock.img` to `boot_a` with `fbmulti.py`.
- **Back to whatever is on slot B (Alpine, for example):** `python fbmulti.py cmd:set_active:b`, then enter
  fastboot.
