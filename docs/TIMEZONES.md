# Time zones

## Why old clocks show the wrong time

A device works out local time from a time zone database: the rules for every region, including when daylight
saving starts and ends. The clock shipped with a 2021 edition of that database and never gets updates. Its
WebView, which draws your dashboard, carries its own copy, just as old.

Since then, several states, provinces and countries have changed or ended daylight saving. A clock still following
the old rules is an hour off for part of the year, even though its zone setting is right.

## What the tool does

During setup, after rooting the clock, it:

1. **Asks for the time zone.** It shows the clock's current zone; press Enter to keep it, or type a zone's name
   (for example `America/Edmonton`). It checks the name against the time zone data it brings.
2. **Installs the current [IANA time zone database](https://www.iana.org/time-zones)** (2026f, built into
   Android's own format), as a Magisk module named *Time zone data (lck)*, and restarts the clock. The clock's
   system image isn't changed: Magisk lays the new file over the old one at every start. To undo it, remove the
   module in the Magisk app and restart.

Android itself and Kiosk Satellite's clock then follow today's rules for your real zone. The same file was tested
on the Smart Display (Android Things 8.1, like the clock), where the C library, Java and Kiosk Satellite all
followed it. **On the clock it's untested so far:** if the tool says the clock still reads its old copy, please
report it, and use a fixed-offset zone (below) meanwhile.

The dashboard's browser ignores all this: it has its own copy of the rules. See
[Dashboards](#dashboards-can-still-be-an-hour-off).

The file is built from the official IANA release by `spikes/tzdata/build_tzdata.py` and lives in
`assets/tzdata/tzdata`. When IANA publishes a new release, rebuild it with that script (it needs `zic` from the
IANA tz code) and run `lck setup --from tzdata`.

## The other fix: a fixed-offset zone

This one also fixes the dashboard, because no copy of the rules can be wrong about a zone that never changed.

If your region now keeps one offset all year, pick a zone that never had daylight saving and has that offset.
Old rules can't be wrong about a zone that never changes:

| Your offset, all year | Pick |
|---|---|
| UTC−7 | America/Phoenix |
| UTC−6 | America/Regina |
| UTC−5 | America/Panama |
| UTC−4 | America/Puerto_Rico |
| any whole hour | Etc/GMT+N (the sign is reversed: Etc/GMT+6 is UTC−6) |

Set it over adb, as root (the setup roots the clock):

```bash
adb -s <clock-ip>:5555 shell su -c "setprop persist.sys.timezone America/Regina"
```

then restart the clock. Kiosk Satellite's clock, its alarms and the dashboard all follow it. The zone's name shows
in a few places; the time is right.

If your region still changes its clocks twice a year under the old rules, the stock zone is fine as it is.

## Dashboards can still be an hour off

Home Assistant dashboards run in the clock's built-in web browser (Android System WebView). That browser carries
its **own copy** of the time zone rules, from 2021, and ignores the data the tool installs. Setting your Home
Assistant profile to use the server's time zone doesn't help: the browser still applies its own old rules to that
zone (found on the Smart Display).

So on a dashboard, with your real zone set:

- **Right:** times Home Assistant works out on the server, such as a clock from `sensor.time`, a Markdown card
  template, or a template sensor.
- **Can be an hour off:** times the browser works out itself, from a timestamp: timestamp sensors shown by Home
  Assistant's own cards and pop-ups, history, logbook and calendar times, and custom cards that format dates in
  JavaScript.

**Fixes:** a fixed-offset zone ([above](#the-other-fix-a-fixed-offset-zone)) for the whole clock, such as
`America/Regina` for Alberta's year-round UTC-6. Or keep your real zone and have Home Assistant turn the time into
text: a template helper (Settings > Devices & services > Helpers > Create helper > Template > Sensor) such as

```jinja
{% set t = as_datetime(states('sensor.my_clock_next_alarm'), none) %}
{{ as_local(t).strftime('%a %-I:%M %p') if t else 'none' }}
```

and show its state as it is.
