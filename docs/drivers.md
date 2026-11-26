# USB drivers and permissions

The clock shows up on USB only in its special modes. Under Android its USB port is host-only, so once it's
converted the tool reaches it over Wi-Fi (adb over Wi-Fi), which needs no driver at all.

| Mode | USB ID | Used for |
|---|---|---|
| BROM | `0e8d:0003` (then `0e8d:2001` while mtkclient works) | The backup and restore |
| Preloader | `0e8d:2000` | Not used: the tool says so and asks again |
| Fastboot (LK) | `0bb4:0c01` | Unlocking, flashing, the Magisk boot image |

## Windows

Tested on Windows 11 x64 ([issue 5](https://github.com/TRusselo/lenovo-clock-ha-kiosk/issues/5)). Three drivers:
UsbDk, a WinUSB driver for the clock's high-speed mode, and Google's USB driver for fastboot.

**BROM (backup and restore): UsbDk.** mtkclient, which reads and writes the clock in BROM mode, uses it on
Windows. BROM lasts only a few seconds, too short to attach a driver by hand, and UsbDk needs no per-device setup.
1. Download the 64-bit `.msi` from [UsbDk's releases](https://github.com/daynix/UsbDk/releases) and install it.
2. Restart Windows if it asks.

The tool checks for UsbDk before it asks for BROM mode, and stops with these steps if it's missing. Without it,
Windows still shows the clock in BROM mode ("Clock mode: BROM"), but nothing can open it: after a few seconds the
clock gives up and starts normally. If the tool says UsbDk isn't working although it's installed, install it again
and restart Windows. If the check can't see a UsbDk that's there, `set LCK_SKIP_USBDK_CHECK=1` in the command
window skips it.

**WinUSB for the high-speed mode (`0E8D 2001`).** After the backup starts, mtkclient switches the clock to
high-speed USB, and it comes back with a new USB ID, `0E8D 2001`. Some PCs have no driver for that ID: the backup
then stays at 0%, and Device Manager shows the device with a yellow mark (code 28). The tool notices this after
about a minute and a half, stops, and shows these steps. The device only exists in this mode, so give it its
driver while the clock is still stuck there, before unplugging anything:
1. Download [Zadig](https://zadig.akeo.ie) and run it.
2. **Options > List All Devices**, then pick the device with USB ID **0E8D 2001**.
3. Choose **WinUSB** and click **Install Driver** (or **Replace Driver**).
4. Unplug the clock's round power plug, and run the same command again.

Windows keeps that driver, so it's needed only once. Only give Zadig's driver to `0E8D 2001`: the clock's BROM
device (`0E8D 0003`) must keep Windows' own serial driver, for UsbDk.

**If BROM still doesn't connect,** please open an issue with the log (`logs\clock.log` in the tool's folder). It
records the tool's version and Windows', whether UsbDk was found, mtkclient's whole output, and what Windows knows
about the clock's MediaTek USB device (from `pnputil /enum-devices /ids`), including its driver. Device Manager
shows the same thing: in BROM mode the clock is a MediaTek device (USB ID `0E8D 0003`), often a **USB Serial
Device (COM…)** under **Ports**.

**Fastboot (unlocking and flashing): Google's USB driver.** The clock stays in fastboot mode until it's unplugged,
so there's time to attach it. Windows doesn't know the clock's fastboot ID, so the driver is picked by hand:
1. Download the [Google USB Driver](https://developer.android.com/studio/run/win-usb) and unzip it.
2. Put the clock in fastboot mode (the tool tells you how; the screen stays dark).
3. Open **Device Manager**, right-click the unknown device (often under **Other devices**), and choose **Update
   driver > Browse my computer > Let me pick from a list**. Don't use "Search automatically": Google's driver
   doesn't list the clock's fastboot ID, so Windows finds nothing. On Windows 11 the list asks for a device type
   first: pick **Show All Devices** (or **Android Device**) and click **Next**. Then click **Have Disk**.
4. Pick `android_winusb.inf` from the unzipped folder, then **Android Bootloader Interface**, and accept the
   warning.

Both the tool's own flashing and Google's `fastboot.exe` (the unlock) use that one driver.

## If the backup stops at 0%

mtkclient connects, reads the clock's disk size, then switches the clock to high-speed USB. If the clock doesn't
come back after that switch, the tool stops after about a minute and a half and says what to do:
- **Windows:** the clock's high-speed mode has no driver. Give it WinUSB with Zadig, as [above](#windows).
- **Linux and macOS:** some USB ports can't take the clock in that mode. Plug the clock into a **USB 2.0 hub** (or
  a USB 2.0 port) instead of straight into the computer, unplug the clock's power, and run the same command again.

Nothing is written to the clock before this point.

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
