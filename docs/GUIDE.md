# Guide

Every page in one place, in the order you'll need them.

## How setup goes

```text
 Your computer                     The clock
 -------------                     ---------
 1. Download and unzip lck
 2. Run: lck setup
       |
       |-- backup -------------->  BROM mode (screen dark)
       |-- unlock -------------->  fastboot mode (screen dark)
       |-- build slot A from the backup
       |-- flash --------------->  fastboot mode (screen dark)
       |                           starts stock Android Things, joins your Wi-Fi
       |-- you: the clock's address
       |-- root ---------------->  fastboot mode, then you: Magisk
       |-- volume buttons ------>  you: press each one
       |-- Google off, keyboard, Kiosk Satellite (over Wi-Fi)
       |                           you: Kiosk Satellite setup
       |-- Casting Helper (a Kiosk Satellite plugin)
       |-- final check
       v
 3. Keep the backup zip            Home Assistant dashboard
```

The tool tells you each time it needs you at the clock. The screen stays dark in BROM and fastboot modes:
that's normal.

## What you'll see

<img width="600" alt="The setup screen during the backup" src="../assets/images/setup-screen.png" />

One screen that redraws as setup goes: a bar, every step with a ✓ when it's done, and the current one
highlighted. Under it, the job in progress counts up, such as "37%" for the backup.

- **Yellow is your turn.** An "On the clock" box says what to do there, with the buttons in bold. A yellow
  question waits for you to type or press Enter.
- **Clock mode** shows which mode the clock is in while the tool waits for it. It keeps waiting as long as you
  need, so you can walk away: press Ctrl-C to stop.
- **If the clock drops out** during a step over Wi-Fi (it restarted, or lost power or Wi-Fi), the tool says so,
  waits for it to come back, and starts that step again.
- **Red means something went wrong.** The tool shows the last lines of its log and where the log is saved.

## Before you start

Pick the guide for your computer:
- [Windows](WINDOWS.md), with [USB drivers](drivers.md)
- [Mac](MACOS.md) (Apple silicon)
- [Linux](LINUX.md)
- [Running from source](PYTHON.md): any computer with Python 3.10 or newer, including Intel Macs

New to this? [Words you'll see](../README.md#words-youll-see) explains the terms.

### The USB cable

<img width="300" alt="A USB A-to-A cable: a full-size USB-A plug at both ends" src="../assets/images/usb-a-to-a-cable.png" />

You need a **USB A-to-A cable**: a full-size USB-A plug at both ends, wired straight through. A "data transfer"
or "bridge" cable, with a box or chip in the middle, won't work.

- **USB 2 or USB 3** both work. The clock uses only the USB 2 wires for data.
- **Bonus, not covered here:** on the clock, the pins a USB 3 plug adds are wired to a low-voltage serial console
  (UART: 1.8 V, 921600 baud in [untocodes' notes](https://github.com/untocodes/lenovo-cube-hacking)). Cut open a
  USB 3 cable, peel out those extra wires, and they can go to a matching UART adapter. Check the voltage before
  you connect anything: a common 3.3 V adapter can damage the clock.

### Plugging in

The clock has two sockets on its back: the round barrel plug for power, and a full-size USB-A port. One end of the
cable goes there, the other into the computer. The clock runs on its barrel plug; the USB cable alone doesn't
start it.

## During setup

- The [manual](../devices/cube/README.md) has the same steps as the tool, numbered the same way, each with the
  check that proves it worked. Read it to see what a step does, or to do it by hand.
- If the tool stops, run the same command again. It carries on where it left off.
- Run it from the same folder each time. Downloads, the backup, the logs and the progress are kept in the folder
  you run it from: the tool names that folder when it starts, and points to earlier progress in the folder above
  or one below.
- Once the clock runs Android (from "the clock's address" on), the USB cable does nothing: unplugging it is
  harmless. Keep it within reach, though: the root step needs it again, in fastboot mode, for the Magisk boot image.

## After setup

- Kiosk Satellite's settings: on the clock, or in a browser at `http://<the clock's address>:2324`. The tool
  shows the address at the end.
- [Lenovo Clock Dashboard](https://github.com/TRusselo/lenovo-clock-dashboard): a Home Assistant dashboard made
  for the clock's small screen. Install it with HACS, then pick it in Kiosk Satellite. Its own page has the steps,
  and is where to ask about it.
- [Time zones](TIMEZONES.md): the time zone data the tool installs, and dashboards that are an hour off.
- [Changing the Wi-Fi](#changing-the-clocks-wi-fi): a new network or password, or a fixed address, without
  setting up again.
- [Casting Helper](#casting-helper): cast video and music to the clock from a phone or computer.
- Another backup, at any time: `lck backup --name <a new name>`.
- To go back to a backup: `lck restore`, explained under "How it works" in the [README](../README.md#how-it-works).

## If BROM mode won't come

- Hold **VOLUME UP**, then plug in the power, and keep holding about 5 seconds, or until "Clock mode" says BROM.
  (Both volume buttons work too. VOLUME DOWN alone gives the preloader: the tool says so, and starts again.)
- It can take two or three tries. Unplug the power fully each time.
- If a try fails, the tool says why and starts the next one on a fresh screen. If the clock is still held by the
  last try, it asks you to unplug the power first.
- Check the cable: straight-through A-to-A, into the clock's USB port.

## Volume buttons

On the stock clock, Google's software handles the volume buttons, and that software doesn't run once the clock is
converted. Setup installs a small helper (a Magisk module, **Volume buttons (lck)**) that turns the buttons into
volume presses again: 5% a press, repeating while held. It asks you to press each button once to check.

If a button doesn't change the volume: run `lck setup --from volume-keys`. Still nothing? [Open an
issue](../../../issues) with the log. Step 10 of the [manual](../devices/cube/README.md) has the details.

## Changing the clock's Wi-Fi

A new network, or a new password for the same one, without setting up again:

```
lck wifi
```

(`lck\lck wifi` on Windows, `./lck/lck wifi` on Linux, `sudo ./lck/lck wifi` on a Mac, from the folder you
ran setup in.) It asks for the network's name and password, then picks one of two ways:

- **The clock is on Wi-Fi:** the tool tells it over Wi-Fi, in seconds, with no USB and no restart. If the clock
  can't join the new network (a mistyped password, or out of range), it goes back to the one it was on by itself,
  after about 30 seconds, and the tool says so. You may see "Wi-Fi lost" on the clock for a moment.
- **The clock is off Wi-Fi** (the old network is gone, or its password changed): the tool uses USB, as setup does.
  In BROM mode it reads the clock's user data (about 20 minutes), saves the new network in it on this computer,
  and writes it back in fastboot mode (under a minute). The new network replaces the clock's other saved
  networks: with two saved networks in range, the clock may pick either. Nothing else on the clock changes. To
  use this way even when the clock is on Wi-Fi: `lck wifi --usb`.

The password is never logged. If the clock's address changes on the new network, find it in your router's list of devices, and update
it in Home Assistant if Home Assistant loses the clock (Settings, Devices & services, Kiosk Satellite).

### A fixed address

After the password, setup and `lck wifi` both ask for a fixed address for the clock. Press Enter to let your
router give it one (automatic), or type one: it must be free on your network, and outside the range your router
hands out. Then the tool asks for the subnet mask, the gateway (your router) and the DNS servers, each with a
default you keep with Enter: 255.255.255.0, this computer's own gateway when it's on the same network, and the
gateway again for DNS. Several DNS servers go with commas between them.

Over USB, and in setup, the address is saved with the network. Over Wi-Fi, the clock joins the network first
(so a wrong password still falls back by itself), then the tool saves the address and the clock restarts on
it, in about a minute. If it doesn't come back there, run `lck wifi --usb` and type another address, or none.

By hand, with the clock on Wi-Fi, the tool does this. In `adb shell` on the clock, with the single quotes kept:

```
am startservice -n com.google.wifisetup/.WifiSetupService -a WifiSetupService.Connect -e ssid 'NAME' -e passphrase 'PASSWORD'
```

## Casting Helper

Setup adds [Casting Helper](https://gitlab.tiered.top/api/v4/projects/3/packages/generic/casting-helper/latest/casting-helper.zip),
a Kiosk Satellite plugin: phones and computers on the same network see the clock in their apps' cast button,
and video and music play full screen over the dashboard. Its settings are in Kiosk Satellite, under Plugin
Manager. The tool downloads its newest release with the other tools; if that download fails, setup goes on
without it, without a word. If the install fails, the tool says so in one line, and you can install the ZIP it
names yourself: Plugin Manager, Install from ZIP.

## Copying text from the terminal

Use **Ctrl+Shift+C**: Ctrl-C alone stops the tool. During a long BROM job, one Ctrl-C only asks; a second one
within 5 seconds stops it.

## Going back to stock

Use `lck restore`, never the clock's own factory reset. A reset while online unlinks the clock from your Google
account, which no restore can undo: it then stays on Google's setup screen until you set it up again in Google
Home.

## Why everything after the flash is over Wi-Fi

Once Android runs, the clock's USB port only works as a host (for a USB stick, say), so the computer can't reach it
over the cable. The setup switches on adb over Wi-Fi in the clock's user data before flashing, so the tool can
reach it there.

## If the clock doesn't come back

If you typed your Wi-Fi name and password into the tool and the clock says "Can't connect to Wi-Fi", or shows no
IP address (at the top of Google's setup screen, or under Settings, About device), the details were probably
mistyped. At the address question, type wifi to enter them
again: the tool writes only the user data again, in fastboot mode.

The clock's address can change whenever it restarts. When it's slow to answer, the tool asks whether it has, so you
can type the new one and carry on. It never relies on an address from an earlier run without checking it.

After the flash, the first start takes a few minutes, and the clock joins the Wi-Fi saved in the backup, or the
one you typed. If the tool can't reach it:

1. Check the screen: the clock face, or Google's setup screen, means it started.
2. Find its address: the "IP=" line at the top of the setup screen, or swipe up from the bottom, the gear, About
   device, IP address, or your router's list of devices. Type it at the address question, or run the same
   command with `--ip <address>`.
3. If it has no address, it isn't on Wi-Fi. If you typed the Wi-Fi, type wifi at the address question (above).
   If the Wi-Fi came from the backup and that network is gone, run setup again with a new name (`--name`) and
   answer NO to the Wi-Fi question, to type a working one.

## Problems or questions

[Open an issue](../../../issues). Say how far it got, and copy the last lines the tool printed.

## More

- [Findings](findings/NOTES.md): what was learned about this clock, observed versus guessed.
- [For agents](../AGENTS.md).
- [What's changed](../CHANGELOG.md) lists what's new in each version.
