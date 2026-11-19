# Windows, step by step

For Windows 10 or 11 on an Intel or AMD PC. It takes about **an hour and a quarter**, most of it waiting.

> [!NOTE]
> The clock tool was tested end to end on Linux, seven times. The Windows program is new and hasn't been tried on
> Windows yet, let alone converted a real clock: USB (BROM mode and fastboot) on Windows is untested. If you
> try it, please say how it went, whether it worked or not, in an [issue](../../../issues) or the Smart Display
> project's [Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).

New to this? [Words you'll see](../README.md#words-youll-see) explains terminal, BROM mode, fastboot mode and slots
in a few lines.

## 1. What you need

- A **Lenovo Smart Clock, gen 1**: the label underneath says **CD-24501F**. Other models aren't supported.
- Its **round barrel-plug power supply**. The clock runs on that, not on USB.
- A **USB A-to-A cable**: a full-size USB-A plug at both ends, straight through, no chip in the middle. USB 2 or
  USB 3 both work ([what it looks like](GUIDE.md#the-usb-cable)). It goes into the USB port on the clock's back.
- The clock **booted and connected to your Wi-Fi** (set it up with the Google Home app if it isn't). The first
  thing the tool asks is whether it is: type YES, or NO to type your Wi-Fi name and password into the tool later.
  After the conversion the tool reaches the clock only over Wi-Fi.
- About **20 GB free**.

## 2. Download and unzip

1. Download **[lck-windows-x86_64.zip](https://github.com/TRusselo/lenovo-clock-ha-kiosk/releases/latest/download/lck-windows-x86_64.zip)**. That link always gets the newest version.
2. Right-click the zip and choose **Extract All**. Pick a folder **without spaces** in its name, for example
   `C:\lck`, and click **Extract**.

The folder now holds a folder called `lck` (the program) and these instructions. Your downloads and the clock's
backup are saved in this folder too.

## 3. Install the USB drivers (once)

Windows needs two drivers to talk to the clock over USB. Follow the **Windows** part of [drivers.md](drivers.md):
- **UsbDk**, for BROM mode (the backup and restore);
- **Google's USB driver**, attached to the clock in fastboot mode (unlocking and flashing).

The tool also downloads the Windows build of e2fsprogs (`debugfs`, `e2fsck`) that it needs, by itself.

## 4. Open a command window in the folder

1. Open the folder in File Explorer.
2. Click the address bar at the top, type `cmd` and press **Enter**.

A black window opens, already in the right folder.

## 5. Run it

Type this and press **Enter**:

```bat
lck\lck setup
```

(In PowerShell or Windows Terminal instead, type `.\lck\lck setup`.)

## 6. Follow the prompts

- When it says **On the clock** (or **DO THIS ON THE CLOCK**), it tells you which buttons to hold: unplug the
  clock's round power plug, hold the button, plug the power back in, and keep holding as long as it says. The
  screen stays dark. If you see the clock face, it's starting normally: unplug it and try again. BROM mode can
  take two or three tries. The tool waits as long as you need.
- When it asks for **the clock's address**, the box says where to find it on the clock. It looks like `10.0.0.23`.
- When it asks you to do something on the clock's screen (Kiosk Satellite's setup, Magisk), do it there.
  Kiosk Satellite's setup also works in a browser: the tool shows the address.
- When it says **NOT DONE YET**, come back here and press **Enter**.
- To copy text from the window, use **Ctrl+Shift+C** in Windows Terminal, or select it and right-click in the
  classic window: Ctrl-C alone stops the tool (during a long BROM job,
  it asks first).
- If it stops for any reason, run the same command again. It carries on where it left off.

## 7. Keep your backup

At the end the tool names your backup file, in the `backup` folder. Copy it somewhere safe, off this computer if
you can, and keep it private: it holds the clock's own keys and its Wi-Fi details, and it's the only way back to
the clock's original software.

The `work` folder holds the images built from it, with your Wi-Fi password. Delete it once the clock works.

## If antivirus blocks the program

The program isn't signed, and antivirus software sometimes flags programs built this way (with PyInstaller) even
when they're harmless. If `lck` disappears, or Windows says it found a threat in it:

1. Check the zip is the real one. In the folder with the zip, run:
   ```bat
   certutil -hashfile lck-windows-x86_64.zip SHA256
   ```
   Compare the result with the `lck-windows-x86_64.zip` line in `SHA256SUMS` on the
   [release page](../../../releases/latest). If they don't match, delete the zip and download it again.
2. If they match, open **Windows Security > Virus & threat protection > Protection history**, find the entry for
   `lck`, and choose **Allow** or **Restore**.
3. Or [run it from source](PYTHON.md) instead: the same tool, run with Python.

## Questions or problems?

[Open an issue](../../../issues), or ask in the Smart Display project's
[Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).
Say which computer you used, how far it got, and copy the last few lines the tool printed.
