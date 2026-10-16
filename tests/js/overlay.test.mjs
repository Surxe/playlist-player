// The pure parts of the overlay settings panel.
// Run: node --test tests/js/overlay.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FIELDS,
  marginSlider,
  parseValue,
  pickerColor,
} from "../../spotifixed/server/static/overlay.js";

const whole = { kind: "int", min: -5000, max: 5000 };

test("a number is sent only when it is whole and in range", () => {
  assert.equal(parseValue(whole, "260"), 260);
  assert.equal(parseValue(whole, " -40 "), -40);
  assert.equal(parseValue(whole, 12), 12);
  for (const raw of ["", "-", "12.5", "1e3", "abc", "5001", "-5001"]) {
    assert.equal(parseValue(whole, raw), undefined, JSON.stringify(raw));
  }
});

test("a choice must be one of the choices", () => {
  const rule = { kind: "choice", choices: ["top", "bottom"] };
  assert.equal(parseValue(rule, "top"), "top");
  assert.equal(parseValue(rule, "middle"), undefined);
});

test("a colour is sent only in a plain form", () => {
  const rule = { kind: "color" };
  for (const ok of ["#fff", "#ffe066", "#11223344", "white"]) assert.equal(parseValue(rule, ok), ok);
  for (const bad of ["", "#ff", "#gggggg", "red; }", "rgb(1,2,3)"]) {
    assert.equal(parseValue(rule, bad), undefined, bad);
  }
});

test("a switch is sent only as a real true or false", () => {
  const rule = { kind: "bool" };
  assert.equal(parseValue(rule, true), true);
  assert.equal(parseValue(rule, false), false);
  for (const raw of ["true", "on", 1, ""]) assert.equal(parseValue(rule, raw), undefined);
});

test("text is passed through for the daemon to judge", () => {
  assert.equal(parseValue({ kind: "text" }, "Serif Italic"), "Serif Italic");
  assert.equal(parseValue({ kind: "text" }, ""), "");
});

test("the colour picker is given a form it understands, or left alone", () => {
  assert.equal(pickerColor("#FFE066"), "#ffe066");
  assert.equal(pickerColor("#FE0"), "#ffee00");
  assert.equal(pickerColor("#11223344"), "#112233");
  assert.equal(pickerColor("white"), null);
});

test("every setting is listed once", () => {
  const keys = FIELDS.map((field) => field.key);
  assert.equal(new Set(keys).size, keys.length);
  assert.equal(keys.length, 13);
});

test("a margin slider cannot drag an attached overlay off the screen", () => {
  const x = FIELDS.find((field) => field.key === "margin_x");
  const y = FIELDS.find((field) => field.key === "margin_y");
  assert.deepEqual(marginSlider(y, "bottom"), [0, 600]);
  assert.deepEqual(marginSlider(x, "bottom"), [-1000, 1000]); // centred on that axis
  assert.deepEqual(marginSlider(x, "top-right"), [0, 1000]);
  assert.deepEqual(marginSlider(y, "top-right"), [0, 600]);
  assert.deepEqual(marginSlider(x, "center"), [-1000, 1000]);
  assert.deepEqual(marginSlider(y, "center"), [-600, 600]);
  assert.deepEqual(marginSlider(y, "left"), [-600, 600]);
});
