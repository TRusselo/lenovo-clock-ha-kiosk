# Guide

Every page in one place, in the order you'll need them.

## How setup goes

```text
 Your computer                     The clock
 -------------                     ---------
 1. Get the tool, Linux guide
 2. Run: lck setup
       |
       |-- backup -------------->  BROM mode (screen dark)
       |-- unlock -------------->  fastboot mode (screen dark)
       |-- build slot A from the backup
       |-- flash --------------->  fastboot mode (screen dark)
       |                           starts stock Android Things, joins your Wi-Fi
       |-- you: the clock's address
       |-- root ---------------->  fastboot mode, then you: Magisk
       |-- Google off, keyboard, Kiosk Satellite (over Wi-Fi)
       |                           you: Kiosk Satellite setup
       |-- final check
       v
 3. Keep the backup zip            Home Assistant dashboard
```

The tool tells you each time it needs you at the clock. The screen stays dark in BROM and fastboot modes:
that's normal.

## What you'll see

One screen that redraws as setup goes: a bar, every step with a ✓ when it's done, and the current one
highlighted. Under it, the job in progress counts up, such as "37%" for the backup.

- **Yellow is your turn.** An "On the clock" box says what to do there, with the buttons in bold. A yellow
  question waits for you to type or press Enter.
- **Clock mode** shows which mode the clock is in while the tool waits for it. It keeps waiting as long as you
  need: press Ctrl-C to stop.
- **Red means something went wrong.** The tool shows the last lines of its log and where the log is saved.

## Before you start

- [Linux](LINUX.md): packages, USB access, and running the tool. Windows and macOS aren't supported yet.

New to this? [Words you'll see](../README.md#words-youll-see) explains the terms.

### Plugging in

The clock has two sockets on its back: the round barrel plug for power, and a full-size USB-A port. The USB
cable is A-to-A, and has to be straight-through: a "data transfer" cable with a chip in the middle won't work.
The clock runs on its barrel plug; the USB cable alone doesn't start it.

## During setup

- The [manual](../devices/cube/README.md) has the same steps as the tool, numbered the same way, each with the
  check that proves it worked. Read it to see what a step does, or to do it by hand.
- If the tool stops, run the same command again. It carries on where it left off.

## After setup

- Kiosk Satellite's settings: on the clock, or in a browser at `http://<the clock's address>:2324`. The tool
  shows the address at the end.
- [Kiosk Clock dashboard](../dashboard/README.md): a Home Assistant dashboard made for the clock's small screen.
  Import it into Home Assistant, then pick it in Kiosk Satellite.
- [Time zones](TIMEZONES.md): why the clock uses a fixed-offset zone.
- Another backup, at any time: `lck backup --name <a new name>`.
- To go back to a backup: `lck restore`, explained under "How it works" in the [README](../README.md#how-it-works).

## If BROM mode won't come

- Hold **both** volume buttons, then plug in the power, and keep holding about 5 seconds. One button alone gives
  the preloader: the tool says so, and starts again.
- It often takes two or three tries. Unplug the power fully each time.
- Check the cable: straight-through A-to-A, into the clock's USB port.

## Why everything after the flash is over Wi-Fi

Once Android runs, the clock's USB port only works as a host (for a USB stick, say), so the computer can't reach it
over the cable. The setup switches on adb over Wi-Fi in the clock's user data before flashing, so the tool can
reach it there.

## If the clock doesn't come back

If you typed your Wi-Fi name and password into the tool and the clock shows its clock face but no IP address (under
Settings, About device), the details were probably mistyped. At the address question, type wifi to enter them
again: the tool writes only the user data again, in fastboot mode.

The clock's address can change whenever it restarts. When it's slow to answer, the tool asks whether it has, so you
can type the new one and carry on. It never relies on an address from an earlier run without checking it.

After the flash, the first start takes a few minutes, and the clock joins the Wi-Fi saved in the backup. If the
tool can't reach it:

1. Check the screen: the clock face means it started.
2. Find its address on the clock (swipe up from the bottom, the gear, About device, IP address) or in your
   router's list of devices, and run the same command with `--ip <address>`.
3. If the Wi-Fi saved in the backup no longer exists, the clock has no network. Please report this.

## Problems or questions

[Open an issue](../../../issues). Say how far it got, and copy the last lines the tool printed.

## More

- [Findings](findings/NOTES.md): what was learned about this clock, observed versus guessed.
- [For agents](../AGENTS.md).
- [What's changed](../CHANGELOG.md) lists what's new in each version.
