# Guide

Every page in one place, in the order you'll need them.

## How setup goes

```text
 Your computer                     The clock
 -------------                     ---------
 1. Full backup (mtkclient) ---->  BROM mode (screen dark)
 2. Unlock -------------------->  fastboot (screen dark)
 3. Build slot A images + inject userdata
 4. Flash slot A -------------->  fastboot (screen dark)
                                   starts stock Android Things, joins your Wi-Fi
 5. adb over Wi-Fi: Kiosk Satellite, grants, Google off
                                   you: Kiosk Satellite setup wizard
 6. Settings and checks
    v
 Keep the backup                   Home Assistant dashboard
```

## Pages

- [The manual](../devices/cube/README.md): every step, each with the check that proves it worked.
- [Findings](findings/NOTES.md): what was learned about this clock, observed versus guessed.
- [For agents](../AGENTS.md).
