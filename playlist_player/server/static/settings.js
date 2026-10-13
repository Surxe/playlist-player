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
