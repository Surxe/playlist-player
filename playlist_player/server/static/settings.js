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
