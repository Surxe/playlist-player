// Position extrapolation, current-line lookup and the current-word estimate for the
// remote page.
//
// No DOM access, so this file runs under `node --test`. The functions mirror the Python
// side (models.PlaybackState.position_at, lyrics.timeline.current_index and
// lyrics.words) and are tested against the same tables of cases,
// tests/fixtures/timeline_cases.json and tests/fixtures/word_cases.json.

// Where playback is at `nowMs`, given the last snapshot from the daemon. Snapshots are
// only sent when something changes; in between, the position is the snapshot's position
// plus the wall-clock time since it was captured.
export function positionAt(state, nowMs) {
  if (!state) return 0;
  if (state.paused || state.status !== "playing") return state.position_s;
  const elapsed = Math.max(0, nowMs - state.captured_at_epoch_ms) / 1000;
  const position = state.position_s + elapsed * (state.speed ?? 1);
  return state.duration_s ? Math.min(position, state.duration_s) : position;
}

// Index of the lyric line showing at `positionMs`, or -1 before the first line.
// Equivalent to Python's bisect_right(times, positionMs + leadMs + offsetMs) - 1.
export function currentIndex(times, positionMs, leadMs = 0, offsetMs = 0) {
  const target = positionMs + leadMs + offsetMs;
  let low = 0;
  let high = times.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (times[middle] <= target) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

// How far playback is through the line at `index`, from 0 to 1, or null when that
// cannot be said. Lyric files record only when each line starts, so a line's span is
// taken to run until the next line starts (until the track ends, for the last line):
// it covers any pause or instrumental that follows the words, not just the singing.
// The lead and the offset shift this the same way they shift which line is current.
export function lineProgress(times, index, positionMs, leadMs = 0, offsetMs = 0, durationMs = null) {
  if (index < 0 || index >= times.length) return null;
  const shift = leadMs + offsetMs;
  const start = times[index];
  let end = null;
  if (index + 1 < times.length) end = times[index + 1];
  else if (durationMs !== null && Number.isFinite(durationMs)) end = durationMs + shift;
  if (end === null || end <= start) return null;
  return Math.min(1, Math.max(0, (positionMs + shift - start) / (end - start)));
}

// ---- the current word: an estimate. lyrics/words.py explains the reasoning; this is
// the same arithmetic in the same order, so both give the same numbers.

export const SLACK = 1.3;
export const WINDOW = 4;
const MIN_NEARBY = 3;

// A rough syllable count for `word`; at least 1, whatever it is made of.
export function syllables(word) {
  const letters = word.normalize("NFD").toLowerCase().replace(/[^a-z]/g, "");
  let count = (letters.match(/[aeiouy]+/g) ?? []).length;
  if (count > 1 && letters.endsWith("e") && !letters.endsWith("le") && !letters.endsWith("ee")) {
    count -= 1; // a silent final e: "love", "time"
  }
  return Math.max(1, count);
}

// Where each word of `text` starts and ends, as [start, end] offsets.
export function wordRanges(text) {
  return [...text.matchAll(/\S+/g)].map((match) => [match.index, match.index + match[0].length]);
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

const sum = (values) => values.reduce((total, value) => total + value, 0);

// For each line ({t_ms, text}), when each of its words ends, in ms after the line's
// timestamp. A line's last entry is where its singing is taken to end: its span to the
// next line, cut short when that is longer than its syllables would take at the pace
// of the lines around it. No entries for a line with no words or no usable length.
export function wordEnds(lines) {
  const weights = lines.map((line) => wordRanges(line.text).map(([a, b]) => syllables(line.text.slice(a, b))));
  const spans = lines.map((line, i) => (i + 1 < lines.length ? lines[i + 1].t_ms - line.t_ms : null));
  const paces = weights.map((line, i) =>
    line.length && spans[i] !== null && spans[i] > 0 ? spans[i] / sum(line) : null);
  const known = paces.filter((pace) => pace !== null);
  const overall = known.length ? median(known) : null;

  return weights.map((line, index) => {
    const nearby = paces
      .slice(Math.max(0, index - WINDOW), index + WINDOW + 1)
      .filter((pace) => pace !== null);
    const typical = nearby.length >= MIN_NEARBY ? median(nearby) : overall;
    const span = spans[index];
    const total = sum(line);
    if (!line.length || typical === null || (span !== null && span <= 0)) return [];
    let sung = total * typical * SLACK;
    if (span !== null) sung = Math.min(span, sung);
    let done = 0;
    return line.map((weight) => {
      done += weight;
      return (sung * done) / total;
    });
  });
}

// Index of the word being sung `elapsedMs` into its line, or -1 for none: before the
// line starts, or once its singing has ended. `ends` is one line's entry from wordEnds.
export function currentWord(ends, elapsedMs) {
  if (!ends || !ends.length || elapsedMs < 0 || elapsedMs >= ends[ends.length - 1]) return -1;
  return currentIndex(ends, elapsedMs) + 1;
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
