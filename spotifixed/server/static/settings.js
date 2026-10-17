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

// A section of on/off switches kept in the daemon's session: `ids` maps each setting's
// name to its checkbox's id. `changed` is called with every setting in force after a
// switch is saved, so the page can follow it at once.
export function initSwitches({ getJson, postJson, ids, noteId, changed }) {
  const boxes = Object.fromEntries(
    Object.entries(ids).map(([name, id]) => [name, document.getElementById(id)]),
  );
  const note = document.getElementById(noteId);

  function show(settings) {
    for (const [name, box] of Object.entries(boxes)) box.checked = settings[name];
  }

  for (const [name, box] of Object.entries(boxes)) {
    box.addEventListener("change", async () => {
      const result = await postJson("/session/settings", { [name]: box.checked });
      note.textContent = result.ok ? "" : result.error;
      if (result.ok) {
        show(result.data.settings);
        changed(result.data.settings);
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

// The keyboard section: which keys do what, as the daemon was set up. Nothing to change
// here; the volume keys are the desktop's to rebind, and the media keys are fixed.
export function initKeyboardList({ getJson }) {
  const list = document.getElementById("shortcut-list");

  function show({ shortcuts, media_keys: mediaKeys }) {
    const rows = [...shortcuts, ...mediaKeys];
    list.replaceChildren();
    for (const { keys, description } of rows) {
      const term = document.createElement("dt");
      const what = document.createElement("dd");
      term.textContent = keys;
      what.textContent = description;
      list.append(term, what);
    }
    if (rows.length === 0) {
      const none = document.createElement("dd");
      none.textContent = "No keyboard keys are set up for spotifixed.";
      list.append(none);
    }
  }

  return {
    async opened() {
      const keys = await getJson("/shortcuts").catch(() => null);
      if (keys) show(keys);
    },
  };
}
