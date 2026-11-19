# macOS, step by step

For Macs with Apple silicon (M1 and later). It takes about **an hour and a quarter**, most of it waiting.

> [!NOTE]
> The clock tool was tested end to end on Linux, seven times. The Mac program is new and hasn't been tried on a
> Mac yet, let alone converted a real clock: USB (BROM mode and fastboot) on macOS is untested. If you try it,
> please say how it went, whether it worked or not, in an [issue](../../../issues) or the Smart Display project's
> [Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).
> If macOS blocks the program, see [If macOS blocks the program](#if-macos-blocks-the-program).

On an **Intel Mac**, there's no ready-made program: use the [Python method](PYTHON.md) instead.

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
- **libusb**, for USB. Install [Homebrew](https://brew.sh), then run `brew install libusb`. No drivers are
  needed on a Mac. (The program brings everything else, e2fsprogs included.)

## 2. Download and unzip

1. Download **[lck-macos-arm64.zip](https://github.com/TRusselo/lenovo-clock-ha-kiosk/releases/latest/download/lck-macos-arm64.zip)**. That link always gets the newest version.
2. Double-click the zip. It unzips into a folder next to it. You can move that folder anywhere, for example
   into your home folder.

   Safari may have unzipped it already: if your Downloads folder has a folder called `lck-macos-arm64` instead of
   a zip, use that folder.

The folder holds a folder called `lck` (the program) and these instructions. Your downloads and the clock's
backup are saved in this folder too.

## 3. Open Terminal in the folder

1. Open **Terminal** (in Applications > Utilities, or search for it with Spotlight).
2. Type `cd ` (with a space after it), then drag the unzipped folder from Finder into the Terminal window, and
   press **Return**.

## 4. Run it

Copy these two lines into Terminal and press **Return**:

```bash
xattr -dr com.apple.quarantine lck
./lck/lck setup
```

The first line stops macOS from blocking the program because it was downloaded and isn't signed by Apple. You
only need it once. Run it from the folder you unzipped, the one that has the `lck` folder in it.

Don't double-click anything inside the `lck` folder in Finder. The program runs in Terminal.

## 5. Follow the prompts

- When it says **On the clock** (or **DO THIS ON THE CLOCK**), it tells you which buttons to hold: unplug the
  clock's round power plug, hold the button, plug the power back in, and keep holding as long as it says. The
  screen stays dark. If you see the clock face, it's starting normally: unplug it and try again. BROM mode can
  take two or three tries. The tool waits as long as you need.
- When it asks for **the clock's address**, the box says where to find it on the clock. It looks like `10.0.0.23`.
- When it asks you to do something on the clock's screen (Kiosk Satellite's setup, Magisk), do it there.
  Kiosk Satellite's setup also works in a browser: the tool shows the address.
- When it says **NOT DONE YET**, come back here and press **Enter**.
- To copy text from the window, use **Cmd+C**: Ctrl-C stops the tool (during a long BROM job,
  it asks first).
- If it stops for any reason, run the same command again. It carries on where it left off.

## 6. Keep your backup

At the end the tool names your backup file, in the `backup` folder. Copy it somewhere safe, off this computer if
you can, and keep it private: it holds the clock's own keys and its Wi-Fi details, and it's the only way back to
the clock's original software.

The `work` folder holds the images built from it, with your Wi-Fi password. Delete it once the clock works.

## If macOS blocks the program

macOS blocks programs from the internet that Apple hasn't checked. This one isn't checked: that needs a paid Apple
developer account. The block is a message that macOS can't verify `lck`, or that it may be malware.

1. Click anything except **Move to Trash** (or **Move to Bin**).
2. In Terminal, in the folder you unzipped, run the `xattr` line from step 4 again, then `./lck/lck setup`.
   Check you're in the right folder first: `ls` should list `lck`.
3. If it's still blocked, open **System Settings > Privacy & Security**, scroll down to the message about `lck`,
   and click **Open Anyway**.
4. If none of that works, [run it from source](PYTHON.md) instead. It's the same tool, run with Python, and macOS
   doesn't block it.

## Questions or problems?

[Open an issue](../../../issues), or ask in the Smart Display project's
[Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).
Say which computer you used, how far it got, and copy the last few lines the tool printed.
