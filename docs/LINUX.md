# Linux, step by step

For x86-64 and ARM64 PCs with Python 3.10 or newer. It takes about **1 hour 30 minutes**, most of it waiting.

New to this? [Words you'll see](../README.md#words-youll-see) explains terminal, BROM mode, fastboot mode and slots
in a few lines.

## 1. What you need

- A **Lenovo Smart Clock, gen 1**: the label underneath says **CD-24501F**. Other models aren't supported.
- Its **round barrel-plug power supply**. The clock runs on that, not on USB.
- A **USB A-to-A cable**, straight-through (no chip in the middle). It goes into the USB port on the clock's back.
- The clock already **set up on your Wi-Fi**. After the conversion it reconnects to the same network, and the
  tool reaches it there.
- About **20 GB free**.

## 2. Install the packages (once)

The tool needs a few standard Linux programs. Copy the line for your distribution; it asks for your password:

```bash
sudo apt install python3-venv e2fsprogs android-sdk-libsparse-utils zstd libusb-1.0-0    # Debian, Ubuntu, Mint
```

```bash
sudo pacman -S --needed python e2fsprogs android-tools zstd libusb                      # Arch, CachyOS, Manjaro
```

```bash
sudo dnf install python3 e2fsprogs android-tools zstd libusb1                           # Fedora
```

Then, in the tool's folder, set up its Python:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 3. Allow USB access (once)

Linux needs a rule that lets you use the clock over USB without root. Copy these lines into the terminal:

```bash
sudo cp assets/linux/51-lck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

It covers the clock's BROM, preloader and fastboot modes. If the tool ever says this account isn't allowed to use
the clock, this step is the fix.

## 4. Run it

Plug the USB cable into the clock and this computer. Leave the clock running for now.

```bash
.venv/bin/python -m lck setup
```

## 5. Follow the prompts

- When it says **On the clock** (or **DO THIS ON THE CLOCK**), it tells you which buttons to hold. Unplug the
  clock's round power plug, hold the buttons, plug the power back in, and keep holding as long as it says. The
  screen stays dark. If you see the clock face, it's starting normally: unplug it and try again. BROM mode often
  takes two or three tries.
- When it asks for **the clock's address**, find it in your router's list of connected devices. The clock may
  show up as "Android". It looks like `10.0.0.23`.
- When it asks you to do something on the clock's screen (Kiosk Satellite's setup, Magisk), do it there.
  Kiosk Satellite's setup also works in a browser: the tool shows the address.
- When it says **NOT DONE YET**, come back to the terminal and press **Enter**.
- If it stops for any reason, run the same command again. It carries on where it left off.

## 6. Keep your backup

At the end the tool names your backup file, in the `backup` folder. Copy it somewhere safe, off this computer if
you can, and keep it private: it holds the clock's own keys and your Wi-Fi password, and it's the only way back to
the clock's original software.

The `work` folder holds the images built from it, including your Wi-Fi password. Delete it once the clock works:

```bash
rm -rf work
```

Afterwards you can remove the USB rule:

```bash
sudo rm /etc/udev/rules.d/51-lck.rules
```

## If GitHub limits lookups

The tool asks GitHub for the newest Kiosk Satellite. GitHub allows 60 of those an hour, shared by every device on
your network, so on a busy network the tool can stop and say when to try again. A VPN changes the address GitHub sees: turning
it on or off, or picking another location, often gets you going at once (a shared VPN address may have the 60
used up already). Or give it a GitHub token (any
[personal access token](https://github.com/settings/tokens), with no permissions ticked) for that run:

```bash
GITHUB_TOKEN=<your token> .venv/bin/python -m lck setup
```

The token goes only to GitHub's API, and isn't saved or logged.

## Questions or problems?

[Open an issue](../../../issues), or ask in the Smart Display project's
[Home Assistant Community thread](https://community.home-assistant.io/t/lenovo-smart-display-8-as-a-de-googled-ha-dashboard-running-kiosk-satellite-w-assist-working-testers-wanted/1033810).
Say how far it got, and copy the last few lines the tool printed.
