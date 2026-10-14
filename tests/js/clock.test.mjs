// clock.js against the case table shared with the Python timeline tests.
// Run: node --test tests/js/clock.test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  alignedWord,
  currentIndex,
  currentWord,
  formatTime,
  lineProgress,
  positionAt,
  syllables,
  wordEnds,
  wordRanges,
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

// Lines at 1.0 s, 2.5 s and 4.0 s.
const times = [1000, 2500, 4000];

test("lineProgress runs from a line's start to the next line's start", () => {
  assert.equal(lineProgress(times, 0, 1000), 0);
  assert.equal(lineProgress(times, 0, 1750), 0.5);
  assert.equal(lineProgress(times, 1, 3250), 0.5);
  assert.equal(lineProgress(times, 0, 2500), 1);
});

test("lineProgress is shifted by the lead and the offset exactly as the current line is", () => {
  // 150 ms of lead: the line appears early, so it is already 10% through at its timestamp.
  assert.equal(lineProgress(times, 0, 1000, 150), 0.1);
  // A positive offset shows lyrics sooner: further through at the same position.
  assert.equal(lineProgress(times, 0, 1300, 0, 300), 0.4);
  assert.equal(lineProgress(times, 0, 1300, 0, -300), 0);
  for (const [position, lead, offset] of [[1700, 150, 0], [2000, 0, 450], [2600, 150, -300]]) {
    const index = currentIndex(times, position, lead, offset);
    const progress = lineProgress(times, index, position, lead, offset);
    assert.ok(progress >= 0 && progress < 1, `${position} ${lead} ${offset} -> ${progress}`);
  }
});

test("lineProgress takes the last line to the end of the track", () => {
  assert.equal(lineProgress(times, 2, 4500, 0, 0, 5000), 0.5);
  assert.equal(lineProgress(times, 2, 5000, 0, 0, 5000), 1);
  // The end moves with the shift too: at the last instant of the track it is full.
  assert.equal(lineProgress(times, 2, 5000, 150, 200, 5000), 1);
  assert.equal(lineProgress(times, 2, 4500, 0, 0), null); // no duration known: no span
  assert.equal(lineProgress(times, 2, 4500, 0, 0, null), null);
});

test("lineProgress stays within 0 and 1 and refuses what has no span", () => {
  assert.equal(lineProgress(times, 0, 500), 0);
  assert.equal(lineProgress(times, 0, 9000), 1);
  assert.equal(lineProgress(times, -1, 500), null); // before the first line
  assert.equal(lineProgress(times, 3, 500), null);
  assert.equal(lineProgress([], 0, 500), null);
  assert.equal(lineProgress([1000, 1000, 2000], 0, 1000), null); // two lines at one instant
  assert.equal(lineProgress(times, 2, 4500, 0, 0, 3000), null); // a track shorter than its lyrics
});

// ---- the current word, against the table shared with tests/unit/test_words.py

const words = JSON.parse(
  readFileSync(new URL("../fixtures/word_cases.json", import.meta.url), "utf8"),
);

test("syllables: the shared table", () => {
  for (const c of words.syllables) assert.equal(syllables(c.word), c.expected, c.word);
});

for (const c of words.tracks) {
  test(`wordEnds: ${c.name}`, () => {
    const ends = wordEnds(c.lines);
    assert.equal(ends.length, c.expected.length);
    ends.forEach((line, i) => {
      assert.equal(line.length, c.expected[i].length, `line ${i}`);
      line.forEach((end, k) => assert.ok(Math.abs(end - c.expected[i][k]) < 1e-6, `line ${i} word ${k}: ${end}`));
    });
  });
}

for (const c of words.moments) {
  test(`currentWord: ${c.name}`, () => {
    assert.equal(currentWord(c.ends, c.elapsed_ms), c.expected);
  });
}

for (const c of words.aligned) {
  test(`alignedWord: ${c.name}`, () => {
    assert.equal(alignedWord(c.words, c.position_ms), c.expected);
  });
}

test("alignedWord takes a line with no word times", () => {
  assert.equal(alignedWord(null, 1000), -1);
});

test("wordRanges gives offsets into the text", () => {
  const text = "Se\u00f1or, don't  stop";
  assert.deepEqual(wordRanges(text).map(([a, b]) => text.slice(a, b)), ["Se\u00f1or,", "don't", "stop"]);
  assert.deepEqual(wordRanges(""), []);
});
