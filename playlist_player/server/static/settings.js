// The settings pane: one place for everything that is set once and kept. It only opens
// and closes; each section looks after its own controls and is told when the pane
// opens (to fetch what it shows) and closes (to stop anything that needs the pane open).

export function initSettingsPane(sections) {
  const pane = document.getElementById("settings-pane");
  const open = document.getElementById("settings-open");
  const close = document.getElementById("settings-close");

  function show() {
    pane.hidden = false;
    for (const section of sections) section.opened?.();
  }

  function hide() {
    pane.hidden = true;
    for (const section of sections) section.closed?.();
  }

  open.addEventListener("click", () => (pane.hidden ? show() : hide()));
  close.addEventListener("click", hide);
}

// The playback section: settings of the daemon's own playback, kept in its session.
export function initPlaybackSettings({ getJson, postJson }) {
  const slider = document.getElementById("fade-in-slider");
  const box = document.getElementById("fade-in");
  const note = document.getElementById("playback-note");
  let timer = 0;

  function show(seconds) {
    for (const input of [slider, box]) {
      if (input !== document.activeElement) input.value = String(seconds);
    }
  }

  async function save(seconds) {
    const result = await postJson("/session/settings", { fade_in_s: seconds });
    note.textContent = result.ok ? "" : result.error;
    if (result.ok) show(result.data.settings.fade_in_s);
  }

  for (const input of [slider, box]) {
    input.addEventListener("input", () => {
      const seconds = Number(input.value);
      if (input.value.trim() === "" || !Number.isFinite(seconds)) return; // half-typed
      (input === slider ? box : slider).value = String(seconds);
      clearTimeout(timer);
      timer = setTimeout(() => save(seconds), 150); // one request per pause in dragging
    });
  }

  return {
    async opened() {
      const session = await getJson("/session").catch(() => null);
      if (session) show(session.settings.fade_in_s);
    },
  };
}

// The tracks section: which tracks a playlist is made of. `changed` is called after a
// switch is saved, so the track list can be drawn again without the tracks left out.
export function initTrackSettings({ getJson, postJson, changed }) {
  const boxes = {
    include_without_lyrics: document.getElementById("include-without-lyrics"),
    include_without_audio: document.getElementById("include-without-audio"),
  };
  const note = document.getElementById("track-note");

  function show(settings) {
    for (const [name, box] of Object.entries(boxes)) box.checked = settings[name];
  }

  for (const [name, box] of Object.entries(boxes)) {
    box.addEventListener("change", async () => {
      const result = await postJson("/session/settings", { [name]: box.checked });
      note.textContent = result.ok ? "" : result.error;
      if (result.ok) {
        show(result.data.settings);
        changed();
      } else {
        box.checked = !box.checked; // it was not taken: show what is still in force
      }
    });
  }

  return {
    async opened() {
      const session = await getJson("/session").catch(() => null);
      if (session) show(session.settings);
    },
  };
}
