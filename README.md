# lenovo-clock-ha-kiosk

**Turn a stock Lenovo Smart Clock (gen 1) into a degoogled Home Assistant kiosk: a dashboard on its 4" screen
and, later, a local voice satellite.**

Lenovo's Smart Clock stopped getting updates, and Google keeps removing its features. The hardware is still good: a
4" touchscreen, two mics, a speaker, a light sensor and Wi-Fi.

This keeps the clock's own Android Things 8.1 and adds [Kiosk Satellite](https://github.com/jxlarrea/kiosk-satellite),
which runs your [Home Assistant](https://www.home-assistant.io/) dashboard and plays music from
[Music Assistant](https://music-assistant.io/). Google Assistant, Cast and the phone-home check-ins are switched
off. It's the clock's sibling of [lenovo-display-ha-kiosk](https://github.com/TRusselo/lenovo-display-ha-kiosk),
which does the same for the Smart Display 8.

> [!CAUTION]
> **Vibecoded, unaudited, your risk, and early.** The code, notes and docs were written by an AI (Claude) in
> conversation with the author. It has run on **one** clock so far, by hand; there is no guided tool yet. It
> unlocks verified boot and rewrites partitions, so **it can brick the clock.** Without a full backup taken
> *before* unlocking, you can't follow this guide. No warranty, no support.

## Clocks

|    | Clock | Model | Status |
|----|-------|-------|--------|
| ⚠️ | Lenovo Smart Clock (gen 1) | CD-24501F | **In progress.** Converted by hand on one clock. [Manual steps](devices/cube/README.md) |
| ❌ | Lenovo Smart Clock 2 | CD-24502F | **Not covered.** Android 10, different firmware. Kiosk Satellite has its own notes for it |
| ❌ | Lenovo Smart Clock Essential | CD-24503F | **Not covered.** No touchscreen |

## What you get

| | Stock | After |
|---|---|---|
| Screen | Google's clock face | Your Home Assistant dashboard |
| Assistant | Google, in the cloud | Off. Home Assistant voice is planned, not yet tested |
| Music | Cast | Music Assistant (Sendspin) |
| Clock | Wrong after DST rule changes | A fixed-offset zone ([why](docs/findings/NOTES.md#time-zones)) |
| Updates | Google OTA | Blocked; Kiosk Satellite updates itself |
| Google | Everything | Off: Assistant, Cast, check-ins |

## What you need

- The clock, its barrel-jack power supply, and a **USB A-to-A** cable (straight-through, no chip)
- A Linux computer with about 15 GB free (other systems are untested)
- A **full eMMC backup taken before the bootloader was unlocked.** Unlocking wipes userdata, and this method
  rebuilds the clock from that backup's userdata
- Home Assistant

## How it works

Unlike the Smart Display, there's no leaked debug build for the clock, and its USB port can't do adb under
Android. So the clock's own stock software is put back on slot A, and **its userdata is edited on the computer
before flashing**: adb over Wi-Fi switched on with your computer's key, Android's automatic reset switched off
(Cast crash-loops on unlocked clocks), and the update channel pointed nowhere so Google can't overwrite slot B.
From then on everything happens over Wi-Fi adb, using the Smart Display's steps that don't need root.

Every step, with the check that proves it worked: [devices/cube/README.md](devices/cube/README.md).
Agents: start with [AGENTS.md](AGENTS.md). All pages: [the guide](docs/GUIDE.md).

## Credits

> [!IMPORTANT]
> **This is built on other people's work.**
> - **kortregt:** [lenovo-smart-clock-linux](https://github.com/kortregt/lenovo-smart-clock-linux): the unlock and
>   backup procedure, and the hardware notes.
> - **joserodpt:** [lenovo-cd24501f-research](https://github.com/joserodpt/lenovo-cd24501f-research): offline
>   userdata provisioning with `debugfs`.
> - **deadman96385:** the AVB unlock credentials, kernel source and firmware dump.
> - **untocodes:** [lenovo-cube-hacking](https://github.com/untocodes/lenovo-cube-hacking) notes (UART, USB, AVB).
> - **S7ruc7ureV01d:** [lenovo-smart-clock-lineage](https://github.com/S7ruc7ureV01d/lenovo-smart-clock-lineage):
>   the LK fastboot workaround (`fbmulti.py`) and the boot-chain findings.
> - **bkerler:** [mtkclient](https://github.com/bkerler/mtkclient).
> - **jxlarrea:** [Kiosk Satellite](https://github.com/jxlarrea/kiosk-satellite).
>
> This repo doesn't host firmware, credentials or third-party tools. [`manifest.json`](manifest.json) pins where
> each comes from.

## Licence

MIT, for this repository's own code and docs ([LICENSE](LICENSE)). Downloaded components keep their own terms.
