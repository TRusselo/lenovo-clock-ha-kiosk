# Linux, step by step

For x86-64 PCs. It takes about **an hour and a quarter**, most of it waiting. On other CPUs, use the
[Python method](PYTHON.md).

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

## 2. Install e2fsprogs (once)

The tool edits the clock's user data with e2fsprogs (`debugfs`, `e2fsck`). Most distributions have it already;
if not, copy the line for yours (it asks for your password):

```bash
sudo apt install e2fsprogs libusb-1.0-0     # Debian, Ubuntu, Mint
```

```bash
sudo pacman -S --needed e2fsprogs libusb   # Arch, CachyOS, Manjaro
```

```bash
sudo dnf install e2fsprogs libusb1         # Fedora
```

## 3. Download and unzip

1. Download **[lck-linux-x86_64.zip](https://github.com/TRusselo/lenovo-clock-ha-kiosk/releases/latest/download/lck-linux-x86_64.zip)**. That link always gets the newest version.
2. Unzip it into a folder of its own: right-click it and choose **Extract Here**, or run
   `unzip lck-linux-x86_64.zip -d lck-clock`.

The folder holds a folder called `lck` (the program) and these instructions. Your downloads and the clock's backup
are saved in this folder too. Open a terminal in it: right-click inside the folder and choose **Open Terminal
Here**.

## 4. Allow USB access (once)

Linux needs a rule that lets you use the clock over USB without root. Copy these lines into the terminal:

```bash
sudo cp lck/_internal/assets/linux/51-lck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

(From source, the rule is at `assets/linux/51-lck.rules`.) It covers the clock's BROM, preloader and fastboot
modes. If the tool ever says this account isn't allowed to use the clock, this step is the fix.

## 5. Run it

Plug the USB cable into the clock and this computer. Leave the clock running for now.

```bash
./lck/lck setup
```

## 6. Follow the prompts

- When it says **On the clock** (or **DO THIS ON THE CLOCK**), it tells you which buttons to hold: unplug the
  clock's round power plug, hold the button, plug the power back in, and keep holding as long as it says. The
  screen stays dark. If you see the clock face, it's starting normally: unplug it and try again. BROM mode can
  take two or three tries. The tool waits as long as you need.
- When it asks for **the clock's address**, the box says where to find it on the clock. It looks like `10.0.0.23`.
- When it asks you to do something on the clock's screen (Kiosk Satellite's setup, Magisk), do it there.
  Kiosk Satellite's setup also works in a browser: the tool shows the address.
- When it says **NOT DONE YET**, come back here and press **Enter**.
- To copy text from the window, use **Ctrl+Shift+C**: Ctrl-C alone stops the tool (during a long BROM job,
  it asks first).
- If it stops for any reason, run the same command again. It carries on where it left off.

## 7. Keep your backup

At the end the tool names your backup file, in the `backup` folder. Copy it somewhere safe, off this computer if
you can, and keep it private: it holds the clock's own keys and its Wi-Fi details, and it's the only way back to
the clock's original software.

The `work` folder holds the images built from it, with your Wi-Fi password. Delete it once the clock works.

```bash
rm -rf work
```

Afterwards you can remove the USB rule:

```bash
sudo rm /etc/udev/rules.d/51-lck.rules
```

## If GitHub limits lookups

The tool asks GitHub for the newest Kiosk Satellite. GitHub allows 60 of those an hour, shared by every device on
your network, so on a busy network the tool can stop and say when to try again. A VPN changes the address GitHub
sees: turning it on or off, or picking another location, often gets you going at once (a shared VPN address may
have the 60 used up already). Or give it a GitHub token (any
[personal access token](https://github.com/settings/tokens), with no permissions ticked) for that run:

```bash
GITHUB_TOKEN=<your token> ./lck/lck setup
```

On Windows: `set GITHUB_TOKEN=<your token>` first, then `lck\lck setup`. On a Mac, as on Linux. The token goes
only to GitHub's API, and isn't saved or logged.

## Questions or problems?

[Open an issue](../../../issues), or ask in the Smart Display project's
[Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).
Say which computer you used, how far it got, and copy the last few lines the tool printed.
