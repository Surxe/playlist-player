// Position extrapolation and current-line lookup for the remote page.
//
// No DOM access, so this file runs under `node --test`. Both functions mirror the
// Python side (models.PlaybackState.position_at and lyrics.timeline.current_index) and
// are tested against the same table of cases, tests/fixtures/timeline_cases.json.

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

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
