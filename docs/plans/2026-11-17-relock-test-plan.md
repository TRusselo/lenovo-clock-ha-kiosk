# Test plan: locking verified boot again after a restore (clock)

For the agent running e2e run 4, in the person's terminal. Goal: find out whether the clock can be locked again
after `lck restore` puts stock back, so `lck lock` can be added the Smart Display's way. **Ask the person before
every write to the clock** (restore, lock, unlock, flash), naming it. Write findings into the run-4 report with
**Observed** / **Guess**, as before.

## What we know

- Lenovo's own `Cube-AVB-factory-Unlock.zip` has a `Lock_batch.bat`: `adb reboot bootloader`,
  **`fastboot oem at-lock-vboot`**, `fastboot set_active b`, `fastboot reboot`. So locking AVB is a supported
  factory step on this clock. Its `Unlock_batch.bat` is the `at_auth_unlock` our unlock step runs.
- The Smart Display tool locks with `fastboot oem at-lock-vboot` only, and only after checking both slots'
  `boot` and `vbmeta` are stock (signed vbmeta, verification flags 0, not the converted boot). Its manual warns:
  locking non-stock images can hard-brick the *display* (Qualcomm, no BROM).
- `at_auth_unlock.py` also cleared the factory persistent digest when it unlocked. Whether stock starts locked
  after that is the open question.
- `lck restore` never writes `seccfg`, `lk`, `tee`, `preloader`, `proinfo`, `nvram`, `oem_bootloader`, `kb`, `dkb`.
  The bootloader lock (`fastboot flashing lock`, stored in `seccfg`) is **not** part of this test.

## Before starting

1. Run 4's restore of the stock pre-unlock backup is done, and the clock was replugged and started stock once.
   Note what the screen shows unlocked (stock clock face, or Assistant's dots that never finish, as on the
   display).
2. The way back exists: `backup/clock.zip` (working kiosk) and the stock backup both verify (`verify_backup`).
3. BROM works on this computer (it did for the restore minutes earlier).

## Steps

1. **Read the lock state** (read-only). Fastboot mode: hold VOLUME UP, plug in, about 10 s. Then, each with
   `timeout 30` (LK can hang on `getvar`):
   `fastboot getvar unlocked`, `fastboot getvar at-vboot-state`, `fastboot getvar current-slot`.
   Expect `unlocked: yes`, `avb-locked: 0`, `bootloader-locked: 1`, slot `b` (the stock backup ran from B).
2. **Check the images are stock** (read-only, on the computer): from the stock backup, `vbmeta_a`/`vbmeta_b`
   start with `AVB0`, are signed (algorithm field at 28..31 not 0) and have flags 0 (bytes 120..123); `boot_a`
   and `boot_b` aren't Magisk-patched (no `.backup/.magisk` in their ramdisk). **Stop if any check fails.**
3. **Ask the person**, then lock AVB only: `fastboot oem at-lock-vboot`.
   Then `fastboot getvar at-vboot-state`: expect `avb-locked: 1`. Record the full output.
4. `fastboot reboot`, no buttons. Record: what the screen shows, how long, whether it gets past the boot logo,
   whether it reaches its clock face / setup. (There's no adb on stock, so the screen is the evidence; a photo
   helps.) Give it 10 minutes.
5. **Round trip:** fastboot again, `getvar at-vboot-state` (still `avb-locked: 1`?), then run the unlock step
   (`lck setup --from unlock --workdir <run-4 workdir>`, or `at_auth_unlock.py -v cube_unlock_credentials_v2.zip`
   by hand). Expect `avb-locked: 0` again. This proves locking isn't one-way.
6. Carry on with run 4's `lck setup` from the unlock step to a working kiosk, or restore `backup/clock.zip`.

## If it goes wrong

- **Locked and won't start** (logo loop, dark, an orange/red warning): LK and its fastboot run before boot
  verification, so fastboot still comes up. Unlock again (step 5), then `lck restore`.
- **Fastboot won't come up:** BROM (both volume buttons) works whatever the bootloader does. `lck restore` the
  stock backup, then try fastboot again.
- **Unlock refuses after locking** (the challenge/response fails): the clock is a locked stock clock. Stop, keep
  the clock as it is, and report the exact `at_auth_unlock.py -v` output. Don't try `fastboot flashing unlock`,
  `seccfg` writes or anything else without the person.
- **Never** run `at-lock-vboot` with Magisk's boot or the converted images on either slot.

## What to report

`getvar` outputs before and after each step; what the screen showed locked, with timings; whether the unlock round
trip worked. From that: whether `lck lock` (and restore offering it) should be added, and under which checks.
