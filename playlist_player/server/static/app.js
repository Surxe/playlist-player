// The remote-control page. A pure client of the daemon's API: it never plays audio and
// never reads files. Playback state arrives on one event stream; between snapshots the
// position is extrapolated locally (see clock.js), so nothing here polls.

import { currentIndex, formatTime, positionAt } from "./clock.js";

const API = "/api/v1";
// Show a line slightly before its timestamp; it reads better than exactly on it.
const LEAD_MS = 150;
const SEEK_STEP_S = 5;

const $ = (id) => document.getElementById(id);
const els = {
  playlists: $("playlists"), shuffle: $("shuffle"), coverage: $("coverage"),
  connection: $("connection"), tracks: $("tracks"), lyrics: $("lyrics"),
  lyricsNote: $("lyrics-note"), offset: $("offset"), offsetReset: $("offset-reset"),
  cover: $("cover"), title: $("title"), artists: $("artists"), toggle: $("toggle"),
  prev: $("prev"), next: $("next"), stop: $("stop"), seek: $("seek"),
  elapsed: $("elapsed"), duration: $("duration"), volume: $("volume"),
};

let state = null; // the latest PlaybackState snapshot
let playlist = null; // the playlist shown in the track list
let tracksById = new Map();
let lyrics = { trackId: null, kind: "missing", times: [], rows: [] };
let shownLine = -2;
let seeking = false;
let settingVolume = false;

async function getJson(path) {
  const response = await fetch(API + path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function post(path, body = {}) {
  const response = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    console.warn(`${path}: ${detail.error ?? response.status}`);
  }
  return response.ok;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ------------------------------- playlists ------------------------------- //

async function loadPlaylists() {
  const { playlists } = await getJson("/playlists");
  els.playlists.replaceChildren(
    ...playlists.map((p) => {
      const option = element("option", "", `${p.name} (${p.coverage.audio}/${p.coverage.tracks})`);
      option.value = p.id;
      return option;
    }),
  );
  if (!playlists.length) {
    els.coverage.textContent = "No playlists are cached yet; run the sync command first.";
    return;
  }
  const playing = state?.playlist_id;
  if (playing && playlists.some((p) => p.id === playing)) els.playlists.value = playing;
  await showPlaylist(els.playlists.value);
}

async function showPlaylist(id) {
  playlist = await getJson(`/playlists/${id}`);
  tracksById = new Map(playlist.tracks.map((t) => [t.id, t]));
  const c = playlist.coverage;
  els.coverage.textContent =
    `${c.tracks} tracks, ${c.audio} with audio, ${c.synced} with synced lyrics`;
  els.tracks.replaceChildren(...playlist.tracks.map(trackRow));
  markCurrentTrack();
  renderNowPlaying();
}

function trackRow(track, index) {
  const row = element("li", track.has_audio ? "" : "unplayable");
  row.dataset.id = track.id;
  const name = element("span", "name");
  name.append(track.title, element("span", "by", `  ${track.artists.join(", ")}`));
  const badges = element("span", "badges");
  if (!track.has_audio) badges.append(element("span", "badge bad", "no audio"));
  if (track.lyrics_kind !== "synced") {
    badges.append(element("span", "badge warn", "no synced lyrics"));
  } else if (track.timing_confidence === "low") {
    badges.append(element("span", "badge warn", "timing?"));
  }
  row.append(
    element("span", "index", String(index + 1)),
    name,
    badges,
    element("span", "length", formatTime(track.duration_ms / 1000)),
  );
  if (track.has_audio) {
    row.addEventListener("click", () =>
      post("/player/load", {
        playlist_id: playlist.id,
        start_track_id: track.id,
        shuffle: els.shuffle.checked,
      }),
    );
  }
  return row;
}

function markCurrentTrack() {
  for (const row of els.tracks.querySelectorAll("li.current")) row.classList.remove("current");
  if (!state?.track_id || state.playlist_id !== playlist?.id) return;
  const row = els.tracks.querySelector(`li[data-id="${state.track_id}"]`);
  if (!row) return;
  row.classList.add("current");
  row.scrollIntoView({ block: "nearest" });
}

// --------------------------------- lyrics -------------------------------- //

async function loadLyrics(trackId) {
  lyrics = { trackId, kind: "missing", times: [], rows: [] };
  shownLine = -2;
  els.lyrics.replaceChildren();
  els.lyrics.className = "";
  els.lyricsNote.textContent = "";
  if (!trackId) return;
  const data = await getJson(`/tracks/${trackId}/lyrics`).catch(() => null);
  if (!data || state?.track_id !== trackId) return; // the track changed while fetching
  lyrics.kind = data.kind;
  if (data.kind === "synced") {
    lyrics.times = data.lines.map((line) => line.t_ms);
    lyrics.rows = data.lines.map((line) => element("li", "", line.text));
    els.lyrics.replaceChildren(...lyrics.rows);
    els.lyricsNote.textContent =
      data.timing_confidence === "low" ? "Timing may be off; nudge the offset." : "";
  } else if (data.kind === "plain") {
    els.lyrics.className = "plain";
    els.lyrics.replaceChildren(...data.plain.split("\n").map((text) => element("li", "", text)));
    els.lyricsNote.textContent = "Lyrics are not time-synced for this track.";
  } else {
    els.lyricsNote.textContent =
      data.kind === "instrumental" ? "Instrumental." : "No lyrics for this track.";
  }
}

function showLine(index) {
  if (index === shownLine) return;
  shownLine = index;
  lyrics.rows.forEach((row, i) => {
    row.classList.toggle("current", i === index);
    row.classList.toggle("past", i < index);
  });
  lyrics.rows[Math.max(index, 0)]?.scrollIntoView({ block: "center", behavior: "smooth" });
}

// ---------------------------------- state --------------------------------- //

// Title, artists and cover for the current track. Called when the track changes and
// again when a playlist finishes loading, since the first state event usually arrives
// before the track list that holds the titles.
function renderNowPlaying() {
  const track = tracksById.get(state?.track_id);
  els.title.textContent = track ? track.title : state?.track_id ?? "Nothing playing";
  els.artists.textContent = track ? track.artists.join(", ") : state?.error ?? "";
  const showCover = Boolean(track?.has_cover);
  els.cover.hidden = !showCover;
  const source = showCover ? `${API}/tracks/${track.id}/cover` : "";
  if (els.cover.getAttribute("src") !== source) els.cover.setAttribute("src", source);
}

function applyState(next) {
  if (state && next.version <= state.version) return; // stale or repeated event
  const trackChanged = next.track_id !== state?.track_id;
  state = next;

  const active = state.status === "playing" || state.status === "loading";
  els.toggle.textContent = active ? "Pause" : "Play";
  els.offset.textContent = `${state.offset_ms > 0 ? "+" : ""}${state.offset_ms} ms`;
  els.duration.textContent = formatTime(state.duration_s ?? 0);
  els.seek.max = String(state.duration_s ?? 1);
  if (!settingVolume) els.volume.value = String(state.volume);
  renderNowPlaying();

  if (trackChanged) {
    markCurrentTrack();
    loadLyrics(state.track_id);
  }
  if (state.playlist_id && playlist && state.playlist_id !== playlist.id) {
    els.playlists.value = state.playlist_id;
    showPlaylist(state.playlist_id);
  }
}

function tick() {
  if (state) {
    const position = positionAt(state, Date.now());
    if (!seeking) els.seek.value = String(position);
    els.elapsed.textContent = formatTime(position);
    if (lyrics.kind === "synced" && lyrics.trackId === state.track_id) {
      showLine(currentIndex(lyrics.times, position * 1000, LEAD_MS, state.offset_ms));
    }
  }
  requestAnimationFrame(tick);
}

function connect() {
  const events = new EventSource(`${API}/events`);
  events.addEventListener("state", (event) => applyState(JSON.parse(event.data)));
  events.onopen = () => {
    els.connection.textContent = "connected";
    els.connection.className = "pill on";
  };
  events.onerror = () => {
    // EventSource reconnects by itself; forget the old version so a restarted daemon,
    // whose versions start again from 1, is not mistaken for stale events.
    els.connection.textContent = "offline";
    els.connection.className = "pill off";
    if (state) state = { ...state, version: 0 };
  };
}

// -------------------------------- controls -------------------------------- //

els.playlists.addEventListener("change", () => showPlaylist(els.playlists.value));
els.toggle.addEventListener("click", () => {
  if (!state?.queue_length && playlist) {
    post("/player/load", { playlist_id: playlist.id, shuffle: els.shuffle.checked });
  } else {
    post("/player/toggle");
  }
});
els.prev.addEventListener("click", () => post("/player/prev"));
els.next.addEventListener("click", () => post("/player/next"));
els.stop.addEventListener("click", () => post("/player/stop"));

els.seek.addEventListener("input", () => { seeking = true; });
els.seek.addEventListener("change", async () => {
  await post("/player/seek", { position_s: Number(els.seek.value) });
  seeking = false;
});
els.volume.addEventListener("input", () => { settingVolume = true; });
els.volume.addEventListener("change", async () => {
  await post("/player/volume", { volume: Number(els.volume.value) });
  settingVolume = false;
});

function setOffset(value) {
  if (state?.track_id) post(`/tracks/${state.track_id}/offset`, { offset_ms: value });
}
for (const button of document.querySelectorAll("[data-nudge]")) {
  button.addEventListener("click", () =>
    setOffset((state?.offset_ms ?? 0) + Number(button.dataset.nudge)),
  );
}
els.offsetReset.addEventListener("click", () => setOffset(0));

document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLSelectElement || event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  const actions = {
    " ": () => els.toggle.click(),
    ArrowLeft: () => post("/player/seek", { delta_s: -SEEK_STEP_S }),
    ArrowRight: () => post("/player/seek", { delta_s: SEEK_STEP_S }),
    ArrowUp: () => post("/player/prev"),
    ArrowDown: () => post("/player/next"),
  };
  const action = actions[event.key];
  if (!action) return;
  event.preventDefault();
  action();
});

connect();
loadPlaylists().catch((error) => { els.coverage.textContent = String(error); });
requestAnimationFrame(tick);
