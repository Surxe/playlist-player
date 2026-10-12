// clock.js against the case table shared with the Python timeline tests.
// Run: node --test tests/js/clock.test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  currentIndex,
  formatTime,
  positionAt,
} from "../../playlist_player/server/static/clock.js";

const table = JSON.parse(
  readFileSync(new URL("../fixtures/timeline_cases.json", import.meta.url), "utf8"),
);

for (const c of table.cases) {
  test(`currentIndex: ${c.name}`, () => {
    assert.equal(currentIndex(c.times, c.position_ms, c.lead_ms, c.offset_ms), c.expected);
  });
}

test("currentIndex defaults lead and offset to zero", () => {
  assert.equal(currentIndex([1000, 2000], 1500), 0);
});

const playing = {
  status: "playing", paused: false, position_s: 10, duration_s: 60, speed: 1,
  captured_at_epoch_ms: 1_000_000,
};

test("positionAt extrapolates while playing", () => {
  assert.equal(positionAt(playing, 1_000_000), 10);
  assert.equal(positionAt(playing, 1_002_500), 12.5);
  assert.equal(positionAt({ ...playing, speed: 2 }, 1_001_000), 12);
});

test("positionAt never runs backwards or past the end", () => {
  assert.equal(positionAt(playing, 999_000), 10); // a clock that reads early
  assert.equal(positionAt(playing, 1_900_000), 60);
  assert.equal(positionAt({ ...playing, duration_s: null }, 1_900_000), 910);
});

test("positionAt holds still unless playing", () => {
  for (const frozen of [
    { ...playing, paused: true, status: "paused" },
    { ...playing, status: "loading" },
    { ...playing, status: "ended" },
  ]) {
    assert.equal(positionAt(frozen, 1_005_000), 10);
  }
  assert.equal(positionAt(null, 5), 0);
});

test("formatTime", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(65.9), "1:05");
  assert.equal(formatTime(600), "10:00");
  assert.equal(formatTime(-1), "0:00");
  assert.equal(formatTime(NaN), "0:00");
});
