# USB drivers and permissions

The clock shows up on USB only in its special modes. Under Android its USB port is host-only, so once it's
converted the tool reaches it over Wi-Fi (adb over Wi-Fi), which needs no driver at all.

| Mode | USB ID | Used for |
|---|---|---|
| BROM | `0e8d:0003` (then `0e8d:2001` while mtkclient works) | The backup and restore |
| Preloader | `0e8d:2000` | Not used: the tool says so and asks again |
| Fastboot (LK) | `0bb4:0c01` | Unlocking, flashing, the Magisk boot image |

## Windows

**Untested on a real clock so far.** These are the drivers mtkclient's and Google's instructions call for; please
report how it goes.

**BROM (backup and restore): UsbDk.** mtkclient, which reads and writes the clock in BROM mode, uses it on
Windows. BROM lasts only a few seconds, too short to attach a driver by hand, and UsbDk needs no per-device setup.
1. Download the 64-bit `.msi` from [UsbDk's releases](https://github.com/daynix/UsbDk/releases) and install it.
2. Restart Windows if it asks.

**Fastboot (unlocking and flashing): Google's USB driver.** The clock stays in fastboot mode until it's unplugged,
so there's time to attach it. Windows doesn't know the clock's fastboot ID, so the driver is picked by hand:
1. Download the [Google USB Driver](https://developer.android.com/studio/run/win-usb) and unzip it.
2. Put the clock in fastboot mode (the tool tells you how; the screen stays dark).
3. Open **Device Manager**, right-click the unknown device (often under **Other devices**), and choose **Update
   driver > Browse my computer > Let me pick from a list > Have Disk**.
4. Pick `android_winusb.inf` from the unzipped folder, then **Android Bootloader Interface**, and accept the
   warning.

Both the tool's own flashing and Google's `fastboot.exe` (the unlock) use that one driver.

## macOS

No drivers. Install libusb with Homebrew: `brew install libusb`.

## Linux

One udev rule, for BROM, the preloader and fastboot. One-time, with `sudo`:

```bash
sudo cp lck/_internal/assets/linux/51-lck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

From source, the rule is at `assets/linux/51-lck.rules`. Then unplug the clock's power and plug it back in, if it
was in one of those modes. When access is missing, the tool says so and points here.
