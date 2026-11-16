import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDashboard, roomsByFloor, findMediaPlayer, clockEntities } from "../kiosk-clock-strategy.js";

const P = "kiosk_satellite";
function fakeHass() {
  const devices = {
    clock: { id: "clock", name: "Bedroom Clock", manufacturer: "kiosk_satellite", area_id: "bed" },
    ma: { id: "ma", name: "Bedroom Clock", manufacturer: "Kiosk Satellite", model: "Kiosk Satellite" },
    cast: { id: "cast", name: "Bedroom clock", manufacturer: "LENOVO", model: "Lenovo Smart Clock" },
  };
  const entities = {};
  const states = {};
  const add = (entity_id, extra = {}, attrs = {}) => {
    entities[entity_id] = { entity_id, ...extra };
    states[entity_id] = { entity_id, state: "off", attributes: attrs };
  };
  for (const id of [
    `light.${P}_screen`, `switch.${P}_adaptive_brightness`, `switch.${P}_keep_screen_on`, `switch.${P}_screensaver_active`,
    `button.${P}_reload_page`, `button.${P}_stop_alarm`, `button.${P}_snooze_alarm`, `button.${P}_show_music_assistant`,
    `binary_sensor.${P}_alarm_ringing`, `sensor.${P}_next_alarm`, `sensor.${P}_alarm_snoozed_until`, `sensor.${P}_vs_next_timer`,
  ]) add(id, { device_id: "clock" });
  add("media_player.bedroom_clock_player", { device_id: "ma" });
  add("media_player.bedroom_clock_cast", { device_id: "cast" });
  add("light.bed_lamp", { area_id: "bed" });
  add("light.bed_ceiling", { area_id: "bed" });
  add("light.hidden_one", { area_id: "bed", hidden: true });
  add("light.kitchen", { area_id: "kitchen" });
  add("binary_sensor.basement_motion", { area_id: "basement" }, { device_class: "motion" });
  add("sensor.attic_temp", { area_id: "attic" }, { device_class: "temperature" });
  add("sensor.office_power", { area_id: "office" }, { device_class: "power" });
  add("weather.home");
  const areas = {
    bed: { area_id: "bed", name: "Bedroom", floor_id: "up" },
    kitchen: { area_id: "kitchen", name: "Kitchen", floor_id: "main" },
    basement: { area_id: "basement", name: "Basement", floor_id: "down" },
    attic: { area_id: "attic", name: "Attic", floor_id: null },
    office: { area_id: "office", name: "Office", floor_id: "main" },
  };
  const floors = {
    main: { floor_id: "main", name: "Main", level: 0 },
    down: { floor_id: "down", name: "Basement", level: -1 },
    up: { floor_id: "up", name: "Upstairs", level: 1 },
  };
  return { devices, entities, states, areas, floors };
}

test("finds the only Kiosk Satellite device without config", () => {
  const d = buildDashboard({ path: "clock" }, fakeHass());
  assert.deepEqual(d.views.map((v) => v.path), ["home", "rooms", "media", "screen"]);
});

test("explains the problem when the named device is missing", () => {
  const d = buildDashboard({ device: "Nope" }, fakeHass());
  assert.equal(d.views.length, 1);
  assert.match(d.views[0].cards[0].content, /No device called "Nope"/);
});

test("asks for device when there are two clocks", () => {
  const h = fakeHass();
  h.devices.clock2 = { id: "clock2", name: "Kitchen Clock", manufacturer: "kiosk_satellite" };
  const d = buildDashboard({}, h);
  assert.match(d.views[0].cards[0].content, /Kitchen Clock/);
});

test("matches clock entities by suffix whatever the prefix", () => {
  const h = fakeHass();
  const c = clockEntities(h.devices.clock, h);
  assert.equal(c.screen, `light.${P}_screen`);
  assert.equal(c.nextTimer, `sensor.${P}_vs_next_timer`);
});

test("picks the Music Assistant player from the app, not Cast", () => {
  const h = fakeHass();
  assert.equal(findMediaPlayer({}, h.devices.clock, h), "media_player.bedroom_clock_player");
});

test("room lights come from the clock's area, skip hidden and the screen", () => {
  const d = buildDashboard({ path: "clock" }, fakeHass());
  const json = JSON.stringify(d.views[0]);
  assert.ok(json.includes("light.bed_ceiling") && json.includes("light.bed_lamp"));
  assert.ok(!json.includes("light.hidden_one"));
  assert.ok(!json.includes(`"entity":"light.${P}_screen"`));
});

test("rooms sort top floor first, loose areas last, irrelevant areas dropped", () => {
  const groups = roomsByFloor({}, fakeHass());
  assert.deepEqual(groups.map((g) => g.name), ["Upstairs", "Main", "Basement", "Other"]);
  assert.deepEqual(groups[1].areas.map((a) => a.area_id), ["kitchen"]);
  assert.deepEqual(groups[3].areas.map((a) => a.area_id), ["attic"]);
});

test("floor_order overrides levels", () => {
  const groups = roomsByFloor({ floor_order: ["down", "up"] }, fakeHass());
  assert.deepEqual(groups.map((g) => g.name).slice(0, 2), ["Basement", "Upstairs"]);
});

test("navigation targets the dashboard path", () => {
  const d = buildDashboard({ path: "my-clock" }, fakeHass());
  const buttons = d.views[0].cards[0].cards[0].cards;
  assert.deepEqual(buttons.map((b) => b.tap_action.navigation_path), [undefined, "/my-clock/rooms", "/my-clock/media", "/my-clock/screen"]);
  assert.equal(buttons[0].tap_action.action, "none");
});

test("alarm buttons only show while ringing", () => {
  const d = buildDashboard({ path: "c" }, fakeHass());
  const right = d.views[0].cards[0].cards[1].cards[1].cards;
  assert.equal(right[0].visibility[0].state_not, "on");
  assert.equal(right[1].visibility[0].state, "on");
  assert.equal(right[1].cards[0].tap_action.target.entity_id, `button.${P}_stop_alarm`);
});

test("no media view without a player", () => {
  const h = fakeHass();
  delete h.devices.ma;
  const d = buildDashboard({ path: "c" }, h);
  assert.ok(!d.views.some((v) => v.path === "media"));
});

test("a device name shared with the Music Assistant player still finds the clock", () => {
  const h = fakeHass();
  // Music Assistant's device listed first, same name as the clock
  h.devices = { ma: h.devices.ma, clock: h.devices.clock, cast: h.devices.cast };
  const d = buildDashboard({ device: "bedroom clock", path: "c" }, h);
  assert.ok(JSON.stringify(d).includes(`light.${P}_screen`));
});

test("home info text uses headings so it is readable on a 4 inch screen", () => {
  const d = buildDashboard({ path: "c", weather: "weather.home" }, fakeHass());
  const md = d.views[0].cards[0].cards[1].cards[0].cards[1].content;
  const lines = md.split("\n\n");
  assert.match(lines[0], /^## /);
  assert.match(lines.at(-1), /\{% if w %\}# /);
});
