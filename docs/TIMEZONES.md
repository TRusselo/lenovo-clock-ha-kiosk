# Time zones

## Why old clocks show the wrong time

A device works out local time from a time zone database: the rules for every region, including when daylight
saving starts and ends. The clock shipped with a 2021 edition of that database and never gets updates. Its
WebView, which draws your dashboard, carries its own copy, just as old.

Since then, several states, provinces and countries have changed or ended daylight saving. A clock still following
the old rules is an hour off for part of the year, even though its zone setting is right.

## The fix: a fixed-offset zone

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

Home Assistant can format times in the browser's zone or the server's. If a dashboard shows times an hour off,
set your Home Assistant profile's **Time Zone** to "Use server time zone".
