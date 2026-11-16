/**
 * Kiosk Clock dashboard strategy for Home Assistant.
 *
 * Builds a small dashboard for a clock running Kiosk Satellite (Lenovo Smart Clock gen 1 and similar).
 * Finds the clock's entities through its device and the rooms through Home Assistant's floors and areas.
 * Uses built-in cards only.
 *
 *   strategy:
 *     type: custom:kiosk-clock
 *     device: Bedroom Clock        # optional when you have one Kiosk Satellite device
 *
 * Optional keys: area, lights (list), media_player, weather, rooms (true/false),
 * floor_order (list of floor ids, top first), exclude_areas (list of area ids), path (dashboard url path).
 */

const KIOSK_MANUFACTURER = "kiosk_satellite"; // ESPHome device from the Kiosk Satellite app
const MA_MODEL = "Kiosk Satellite"; // Music Assistant player that the app's provider adds

const lower = (s) => (s || "").toString().trim().toLowerCase();
const deviceName = (d) => d.name_by_user || d.name || "";

export function findClockDevice(config, hass) {
  const devices = Object.values(hass.devices || {});
  if (config.device) {
    const want = lower(config.device);
    // Music Assistant names its player device after the clock too, so prefer the Kiosk Satellite one.
    const named = devices.filter((d) => d.id === config.device || lower(deviceName(d)) === want);
    const hit = named.find((d) => lower(d.manufacturer) === KIOSK_MANUFACTURER) || named[0];
    return hit ? { device: hit } : { error: `No device called "${config.device}".`, candidates: [] };
  }
  const kiosks = devices.filter((d) => lower(d.manufacturer) === KIOSK_MANUFACTURER);
  if (kiosks.length === 1) return { device: kiosks[0] };
  if (kiosks.length === 0) return { error: "No Kiosk Satellite device found.", candidates: [] };
  return { error: "More than one Kiosk Satellite device. Set `device:` to one of them.", candidates: kiosks.map(deviceName) };
}

/** The clock's own entities, matched by domain and the end of the entity id. */
export function clockEntities(device, hass) {
  const ids = Object.values(hass.entities || {})
    .filter((e) => e.device_id === device.id)
    .map((e) => e.entity_id);
  const pick = (domain, suffix) => ids.find((id) => id.startsWith(domain + ".") && id.endsWith("_" + suffix));
  return {
    screen: pick("light", "screen"),
    adaptive: pick("switch", "adaptive_brightness"),
    keepOn: pick("switch", "keep_screen_on"),
    screensaver: pick("switch", "screensaver_active"),
    reload: pick("button", "reload_page"),
    stopAlarm: pick("button", "stop_alarm"),
    snoozeAlarm: pick("button", "snooze_alarm"),
    showMusic: pick("button", "show_music_assistant"),
    alarmRinging: pick("binary_sensor", "alarm_ringing"),
    nextAlarm: pick("sensor", "next_alarm"),
    snoozedUntil: pick("sensor", "alarm_snoozed_until"),
    nextTimer: pick("sensor", "vs_next_timer"),
  };
}

/** Music Assistant adds the clock as its own device, with the same name and model "Kiosk Satellite". */
export function findMediaPlayer(config, device, hass) {
  if (config.media_player) return config.media_player;
  const name = lower(deviceName(device));
  const devices = Object.values(hass.devices || {}).filter((d) => d.model === MA_MODEL && d.id !== device.id);
  const match = devices.find((d) => lower(deviceName(d)) === name) || (devices.length === 1 ? devices[0] : null);
  if (!match) return undefined;
  const e = Object.values(hass.entities || {}).find(
    (x) => x.device_id === match.id && x.entity_id.startsWith("media_player.")
  );
  return e && e.entity_id;
}

/** Area of an entity: its own, else its device's. */
export function entityArea(entry, hass) {
  if (entry.area_id) return entry.area_id;
  const d = entry.device_id && (hass.devices || {})[entry.device_id];
  return (d && d.area_id) || null;
}

function visibleEntries(hass) {
  return Object.values(hass.entities || {}).filter((e) => !e.hidden && !e.entity_category);
}

const deviceClass = (hass, id) => ((hass.states[id] || {}).attributes || {}).device_class;

export function roomLights(config, device, area, clock, hass) {
  if (Array.isArray(config.lights)) return config.lights;
  if (!area) return [];
  return visibleEntries(hass)
    .filter((e) => e.entity_id.startsWith("light.") && e.entity_id !== clock.screen && entityArea(e, hass) === area)
    .map((e) => e.entity_id)
    .sort()
    .slice(0, 2);
}

/** Floors top to bottom, each with the areas worth showing. */
export function roomsByFloor(config, hass) {
  const exclude = new Set(config.exclude_areas || []);
  const entries = visibleEntries(hass);
  const interesting = new Set();
  for (const e of entries) {
    const area = entityArea(e, hass);
    if (!area) continue;
    const id = e.entity_id;
    const dc = deviceClass(hass, id);
    if (
      id.startsWith("light.") ||
      (id.startsWith("binary_sensor.") && ["motion", "occupancy", "presence", "window"].includes(dc)) ||
      (id.startsWith("sensor.") && dc === "temperature")
    ) {
      interesting.add(area);
    }
  }
  const areas = Object.values(hass.areas || {}).filter((a) => interesting.has(a.area_id) && !exclude.has(a.area_id));
  const floors = Object.values(hass.floors || {});
  const order = config.floor_order;
  const rank = (f) => (order ? (order.indexOf(f.floor_id) === -1 ? 1e6 : order.indexOf(f.floor_id)) : -(f.level ?? -1e6));
  floors.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  const byName = (a, b) => a.name.localeCompare(b.name);
  const groups = floors
    .map((f) => ({ name: f.name, areas: areas.filter((a) => a.floor_id === f.floor_id).sort(byName) }))
    .filter((g) => g.areas.length);
  const known = new Set(floors.map((f) => f.floor_id));
  const loose = areas.filter((a) => !a.floor_id || !known.has(a.floor_id)).sort(byName);
  if (loose.length) groups.push({ name: groups.length ? "Other" : "Rooms", areas: loose });
  return groups;
}

// ---------- cards ----------

const VIEWS = [
  ["home", "Home", "mdi:clock-outline"],
  ["rooms", "Rooms", "mdi:floor-plan"],
  ["media", "Media", "mdi:music"],
  ["screen", "Screen", "mdi:brightness-6"],
];

function nav(base, current, enabled) {
  // Big buttons rather than heading badges: badges are too small to read or hit on a 4" screen.
  return {
    type: "grid",
    columns: enabled.length,
    square: false,
    cards: VIEWS.filter((v) => enabled.includes(v[0])).map(([p, name, icon]) => ({
      type: "button",
      name: p === current ? `• ${name}` : name,
      icon,
      show_name: true,
      show_icon: true,
      icon_height: "22px",
      tap_action: p === current ? { action: "none" } : { action: "navigate", navigation_path: `${base}/${p}` },
    })),
  };
}

const press = (entity) => ({ action: "perform-action", perform_action: "button.press", target: { entity_id: entity } });

function infoMarkdown(c, weather) {
  const ts = (id) => `as_datetime(states('${id}'))`;
  // Markdown headings are the only way to size text in a built-in card without card-mod.
  // Sized for a 4" screen: weather biggest, date and alarm next.
  const lines = [`## {{ now().strftime('%a, %b %-d') }}`];
  if (c.nextTimer) {
    lines.push(
      `{% set t = ${ts(c.nextTimer)} %}{% if t and t > now() %}### ⏳ Timer ends {{ (t | as_local).strftime('%-I:%M %p') }}{% endif %}`
    );
  }
  if (c.nextAlarm || c.snoozedUntil) {
    const z = c.snoozedUntil ? ts(c.snoozedUntil) : "none";
    const a = c.nextAlarm ? ts(c.nextAlarm) : "none";
    lines.push(
      `{% set z = ${z} %}{% set a = ${a} %}` +
        `{% if z and z > now() %}### 💤 Snoozed until {{ (z | as_local).strftime('%-I:%M %p') }}` +
        `{% elif a and a > now() %}### ⏰ {{ (a | as_local).strftime('%a %-I:%M %p') }}{% endif %}`
    );
  }
  if (weather) {
    lines.push(
      `{% set w = states.${weather} %}{% if w %}# {{ w.attributes.temperature | round(0) | int }}° {{ w.state | replace('partlycloudy','partly cloudy') | replace('clear-night','clear') | replace('-',' ') }}{% endif %}`
    );
  }
  return { type: "markdown", text_only: true, content: lines.join("\n\n") };
}

function homeView(base, c, lights, player, weather, enabled) {
  const tiles = lights.map((l) => ({ type: "tile", entity: l, vertical: true, tap_action: { action: "toggle" }, hold_action: { action: "more-info" } }));
  if (player) {
    tiles.push({
      type: "tile",
      entity: player,
      vertical: true,
      icon_tap_action: { action: "perform-action", perform_action: "media_player.media_play_pause", target: { entity_id: player } },
      tap_action: { action: "perform-action", perform_action: "media_player.media_play_pause", target: { entity_id: player } },
      hold_action: { action: "more-info" },
    });
  }
  if (c.screensaver) {
    tiles.push({
      type: "tile",
      entity: c.screensaver,
      name: "Screensaver",
      icon: "mdi:sleep",
      vertical: true,
      tap_action: { action: "perform-action", perform_action: "switch.turn_on", target: { entity_id: c.screensaver } },
    });
  }
  const notRinging = c.alarmRinging ? [{ condition: "state", entity: c.alarmRinging, state_not: "on" }] : [];
  const ringing = c.alarmRinging ? [{ condition: "state", entity: c.alarmRinging, state: "on" }] : null;
  const right = [{ type: "grid", columns: 2, square: false, cards: tiles.slice(0, 4), visibility: notRinging }];
  if (ringing && c.stopAlarm) {
    right.push({
      type: "grid",
      columns: 1,
      square: false,
      visibility: ringing,
      cards: [
        { type: "tile", entity: c.stopAlarm, name: "Stop alarm", icon: "mdi:alarm-off", color: "red", vertical: true, tap_action: press(c.stopAlarm) },
        ...(c.snoozeAlarm
          ? [{ type: "tile", entity: c.snoozeAlarm, name: "Snooze", icon: "mdi:alarm-snooze", color: "amber", vertical: true, tap_action: press(c.snoozeAlarm) }]
          : []),
      ],
    });
  }
  return {
    title: "Home",
    path: "home",
    icon: "mdi:clock-outline",
    type: "panel",
    cards: [
      {
        type: "vertical-stack",
        cards: [
          nav(base, "home", enabled),
          {
            type: "horizontal-stack",
            cards: [
              { type: "vertical-stack", cards: [{ type: "clock", clock_size: "large" }, infoMarkdown(c, weather)] },
              { type: "vertical-stack", cards: right },
            ],
          },
        ],
      },
    ],
  };
}

function roomsView(base, groups, enabled) {
  const cards = [nav(base, "rooms", enabled)];
  for (const g of groups) {
    cards.push({ type: "heading", heading: g.name, heading_style: "subtitle" });
    cards.push({
      type: "grid",
      columns: 3,
      square: false,
      cards: g.areas.map((a) => ({
        type: "area",
        area: a.area_id,
        display_type: "compact",
        alert_classes: ["motion", "occupancy", "presence", "window"],
        sensor_classes: ["temperature"],
        features: [{ type: "area-controls", controls: ["light"] }],
        features_position: "inline",
      })),
    });
  }
  return { title: "Rooms", path: "rooms", icon: "mdi:floor-plan", type: "panel", cards: [{ type: "vertical-stack", cards }] };
}

function mediaView(base, c, player, enabled) {
  const cards = [nav(base, "media", enabled), { type: "media-control", entity: player }];
  cards.push({ type: "tile", entity: player, features: [{ type: "media-player-volume-slider" }], features_position: "inline" });
  if (c.showMusic) {
    cards.push({ type: "tile", entity: c.showMusic, name: "Open Music Assistant", icon: "mdi:music-box-multiple", tap_action: press(c.showMusic) });
  }
  return { title: "Media", path: "media", icon: "mdi:music", type: "panel", cards: [{ type: "vertical-stack", cards }] };
}

function screenView(base, c, enabled) {
  const tiles = [];
  if (c.adaptive) tiles.push({ type: "tile", entity: c.adaptive, name: "Auto brightness", vertical: true, tap_action: { action: "toggle" } });
  if (c.keepOn) tiles.push({ type: "tile", entity: c.keepOn, name: "Keep screen on", vertical: true, tap_action: { action: "toggle" } });
  if (c.screensaver)
    tiles.push({
      type: "tile",
      entity: c.screensaver,
      name: "Screensaver now",
      icon: "mdi:sleep",
      vertical: true,
      tap_action: { action: "perform-action", perform_action: "switch.turn_on", target: { entity_id: c.screensaver } },
    });
  if (c.reload) tiles.push({ type: "tile", entity: c.reload, name: "Reload page", icon: "mdi:refresh", vertical: true, tap_action: press(c.reload) });
  const cards = [nav(base, "screen", enabled)];
  if (c.screen) cards.push({ type: "tile", entity: c.screen, name: "Screen brightness", features: [{ type: "light-brightness" }], features_position: "inline", tap_action: { action: "none" } });
  cards.push({ type: "grid", columns: 2, square: false, cards: tiles });
  return { title: "Screen", path: "screen", icon: "mdi:brightness-6", type: "panel", cards: [{ type: "vertical-stack", cards }] };
}

function errorDashboard(found) {
  const list = found.candidates && found.candidates.length ? "\n\nKiosk Satellite devices: " + found.candidates.join(", ") : "";
  return {
    views: [
      {
        title: "Kiosk Clock",
        cards: [
          {
            type: "markdown",
            content: `## Kiosk Clock\n\n${found.error}${list}\n\nSet it in the dashboard's raw config:\n\n\`\`\`yaml\nstrategy:\n  type: custom:kiosk-clock\n  device: Bedroom Clock\n\`\`\``,
          },
        ],
      },
    ],
  };
}

/** Pure: config + hass in, Lovelace dashboard config out. */
export function buildDashboard(config, hass) {
  config = config || {};
  const found = findClockDevice(config, hass);
  if (!found.device) return errorDashboard(found);
  const device = found.device;
  const c = clockEntities(device, hass);
  const area = config.area || device.area_id || null;
  const lights = roomLights(config, device, area, c, hass);
  const player = findMediaPlayer(config, device, hass);
  const weather =
    config.weather || Object.keys(hass.states || {}).filter((id) => id.startsWith("weather.")).sort()[0];
  const groups = config.rooms === false ? [] : roomsByFloor(config, hass);
  const enabled = ["home"];
  if (groups.length) enabled.push("rooms");
  if (player) enabled.push("media");
  if (c.screen || c.adaptive || c.keepOn) enabled.push("screen");
  const base = "/" + (config.path || (typeof location !== "undefined" ? location.pathname.split("/")[1] : "kiosk-clock"));
  const views = [homeView(base, c, lights, player, weather, enabled)];
  if (enabled.includes("rooms")) views.push(roomsView(base, groups, enabled));
  if (enabled.includes("media")) views.push(mediaView(base, c, player, enabled));
  if (enabled.includes("screen")) views.push(screenView(base, c, enabled));
  if (config.theme) views.forEach((v) => (v.theme = config.theme));
  return { views };
}

if (typeof customElements !== "undefined" && !customElements.get("ll-strategy-dashboard-kiosk-clock")) {
  class KioskClockStrategy extends HTMLElement {
    static async generate(config, hass) {
      return buildDashboard(config, hass);
    }
  }
  customElements.define("ll-strategy-dashboard-kiosk-clock", KioskClockStrategy);
}
