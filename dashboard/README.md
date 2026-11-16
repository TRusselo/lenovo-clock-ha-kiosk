# Kiosk Clock dashboard

A small Home Assistant dashboard for a clock running [Kiosk Satellite](https://github.com/jxlarrea/kiosk-satellite),
sized for the Lenovo Smart Clock gen 1's 4" screen. It's made for a bedroom clock, but works in any room.

| View | What's on it |
|---|---|
| Home | Clock, date, next alarm, voice-timer end time, weather. Big buttons for the room's lights, music and screensaver. Stop and Snooze take over while the alarm rings |
| Rooms | Every room in the house, top floor first, showing lights, motion, open windows and temperature |
| Media | The clock's Music Assistant player: art, controls, volume, and a button that opens Music Assistant on the clock |
| Screen | Screen brightness, auto brightness, keep screen on, screensaver now, reload page |

Setting alarms happens in Kiosk Satellite's slide-out menu, so the dashboard has no alarm page.

It uses **built-in cards only**. No other custom cards are needed.

## Easy install

1. Copy `kiosk-clock-strategy.js` to your Home Assistant `config/www/kiosk-clock/` folder.
2. **Settings → Dashboards → ⋮ → Resources → Add resource**:
   URL `/local/kiosk-clock/kiosk-clock-strategy.js?v=0.1.1`, type **JavaScript module**.
3. **Settings → Dashboards → Add dashboard → New dashboard from scratch**. Name it, e.g. "Bedroom Clock". Open it,
   then **⋮ → Edit dashboard → ⋮ → Raw configuration editor**, replace everything with:

   ```yaml
   strategy:
     type: custom:kiosk-clock
   ```

   and save.
4. On the clock, pick the new dashboard in Kiosk Satellite's settings (or with the clock's **Default dashboard**
   entity in Home Assistant).

That's it. The dashboard finds the clock by itself:

- **The clock**: the one Kiosk Satellite device in Home Assistant. With more than one, the dashboard tells you their
  names. Add `device: <name>` to pick one.
- **Room lights**: the lights in the clock's area (up to two). **Give the clock's device an area**
  (Settings → Devices → your clock → ✏️) or the Home view has no light buttons.
- **Music**: the Music Assistant player that Kiosk Satellite adds, with the same name as the clock.
- **Rooms**: every area that has a light, motion, occupancy, window or temperature sensor, grouped by floor.
  Floors are sorted by their **level**, top first. Set levels under Settings → Areas, labels & zones → Floors.

## Options

All optional:

```yaml
strategy:
  type: custom:kiosk-clock
  device: Bedroom Clock            # name or device id, when you have more than one clock
  area: master_bedroom             # area id to take lights from, instead of the clock's own area
  lights:                          # exact lights for the Home view (up to 2 shown)
    - light.bedside_left
    - light.bedside_right
  media_player: media_player.bedroom_clock_player
  weather: weather.home            # default: the first weather entity
  rooms: false                     # hide the Rooms view
  floor_order: [upstairs, main, basement]   # floor ids, top first, when levels aren't set
  exclude_areas: [garage, network]
  theme: my-theme
  path: bedroom-clock              # the dashboard's URL path, if the buttons along the top don't navigate
```

## Advanced: edit it as YAML

Open the dashboard, then **⋮ → Edit dashboard → ⋮ → Take control**. Home Assistant writes out the generated config
with your own entities filled in. From then on it's a normal dashboard you can change however you like, but it no
longer updates itself when you add rooms or devices.

[`examples/bedroom-clock.yaml`](examples/bedroom-clock.yaml) is what it produces for a typical bedroom clock,
if you'd rather start from a file.

## Testing

```sh
cd dashboard && npm test     # node --test, no Home Assistant needed
```
