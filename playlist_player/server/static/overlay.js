// The overlay settings panel. It edits the daemon's overlay settings document, which a
// running overlay follows live, so moving a slider moves the lyric line on screen.
//
// The daemon owns every rule about what a setting may be and sends them with the
// document (`rules`); this file only decides how each kind of setting is drawn. Values
// set here are overrides on top of the configured ones, and the panel says which
// settings are overridden and offers the lines that would make them the configuration.

// How each setting is shown. `slider` is the comfortable range for dragging; the number
// box beside it accepts the whole range the daemon allows. A margin's slider depends on
// the anchor (see marginSlider), so `slider` there is only its widest form.
export const FIELDS = [
  { key: "anchor", label: "Anchor" },
  { key: "margin_x", label: "Margin X", unit: "px", slider: [-1000, 1000], edges: ["left", "right"] },
  { key: "margin_y", label: "Margin Y", unit: "px", slider: [-600, 600], edges: ["top", "bottom"] },
  { key: "monitor", label: "Monitor", placeholder: "e.g. DP-2; empty lets the compositor choose" },
  { key: "font_size", label: "Font size", unit: "pt", slider: [8, 96] },
  { key: "max_width", label: "Max width", unit: "px", slider: [200, 3000] },
  { key: "font", label: "Font", placeholder: "e.g. Sans Bold" },
  { key: "color", label: "Text colour" },
  { key: "outline_color", label: "Outline colour" },
  { key: "lines", label: "Lines" },
  { key: "lead_ms", label: "Lead", unit: "ms", slider: [-500, 1000] },
  { key: "fade_ms", label: "Fade", unit: "ms", slider: [0, 1000] },
];

const SEND_EVERY_MS = 60;
const PREVIEW_S = 8;
const PREVIEW_RENEW_MS = 3000;

// What to send for the text a control holds, or undefined when it is not sendable yet
// (a half-typed number, say). The daemon has the final word; this only avoids sending
// what it would certainly refuse.
export function parseValue(rule, raw) {
  if (rule.kind === "int") {
    if (!/^-?\d+$/.test(String(raw).trim())) return undefined;
    const value = Number(raw);
    return value >= rule.min && value <= rule.max ? value : undefined;
  }
  if (rule.kind === "choice") return rule.choices.includes(raw) ? raw : undefined;
  if (rule.kind === "color") {
    return /^(#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})|[a-z]{3,20})$/i.test(raw) ? raw : undefined;
  }
  return String(raw);
}

// The slider range for a margin under an anchor. Along an axis the overlay is attached
// on, the margin is a distance in from that edge, and a negative one would slide the
// line off the screen, so the slider starts at 0. On an axis it is centred on, the
// margin is an offset either way.
export function marginSlider(field, anchor) {
  const [low, high] = field.slider;
  const attached = field.edges.some((edge) => String(anchor).split("-").includes(edge));
  return attached ? [0, high] : [low, high];
}

// The value a colour picker can show for a colour setting: it only understands #rrggbb.
export function pickerColor(value) {
  const text = String(value);
  if (/^#[0-9a-f]{6}$/i.test(text)) return text.toLowerCase();
  if (/^#[0-9a-f]{8}$/i.test(text)) return text.slice(0, 7).toLowerCase();
  if (/^#[0-9a-f]{3}$/i.test(text)) {
    return `#${[...text.slice(1)].map((c) => c + c).join("")}`.toLowerCase();
  }
  return null;
}

function node(tag, attributes = {}, ...children) {
  const made = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "class") made.className = value;
    else if (name === "text") made.textContent = value;
    else made.setAttribute(name, value);
  }
  made.append(...children);
  return made;
}

export function initOverlayPanel({ getJson, postJson }) {
  const $ = (id) => document.getElementById(id);
  const els = {
    open: $("overlay-open"), pane: $("overlay-pane"), close: $("overlay-close"),
    status: $("overlay-status"), preview: $("overlay-preview"), form: $("overlay-form"),
    reset: $("overlay-reset"), copy: $("overlay-copy"), lines: $("overlay-lines"),
    note: $("overlay-note"),
  };
  const rows = new Map(); // key -> { row, inputs, drop }
  let rules = null;
  let doc = null;
  let pending = {};
  let inFlight = {};
  let sendTimer = 0;
  let sending = false;
  let previewTimer = 0;

  // ------------------------------- sending ------------------------------- //

  function queue(key, value) {
    pending[key] = value;
    if (!sendTimer) sendTimer = setTimeout(flush, SEND_EVERY_MS);
  }

  async function flush() {
    sendTimer = 0;
    if (sending || !Object.keys(pending).length) return;
    inFlight = pending;
    pending = {};
    sending = true;
    const result = await postJson("/overlay", inFlight);
    sending = false;
    inFlight = {};
    if (!result.ok) {
      els.note.textContent = result.error;
      render(); // put the controls back to what the daemon actually holds
    } else {
      els.note.textContent = "";
      apply(result.data);
    }
    if (Object.keys(pending).length) sendTimer = setTimeout(flush, SEND_EVERY_MS);
  }

  function changed(key, raw) {
    const value = parseValue(rules[key], raw);
    if (value === undefined) return;
    for (const input of rows.get(key).inputs) {
      if (input !== document.activeElement) setInput(input, value);
    }
    queue(key, value);
  }

  // ------------------------------- building ------------------------------ //

  function setInput(input, value) {
    if (input.type === "color") {
      const shown = pickerColor(value);
      if (shown) input.value = shown;
    } else {
      input.value = String(value);
    }
  }

  function controlsFor(field, rule) {
    const id = `overlay-field-${field.key}`;
    if (rule.kind === "choice" || field.key === "lines") {
      const choices = rule.kind === "choice" ? rule.choices : ["1", "2"];
      const select = node("select", { id }, ...choices.map((c) => node("option", { value: c, text: c })));
      select.addEventListener("change", () => changed(field.key, select.value));
      return [select];
    }
    if (rule.kind === "int") {
      const [low, high] = field.slider ?? [rule.min, rule.max];
      const slider = node("input", { type: "range", min: low, max: high, step: 1, "aria-label": field.label });
      const box = node("input", { id, type: "number", min: rule.min, max: rule.max, step: 1 });
      for (const input of [slider, box]) {
        input.addEventListener("input", () => changed(field.key, input.value));
      }
      return [slider, box];
    }
    if (rule.kind === "color") {
      const picker = node("input", { type: "color", "aria-label": field.label });
      const box = node("input", { id, type: "text", spellcheck: "false", size: 10 });
      picker.addEventListener("input", () => changed(field.key, picker.value));
      box.addEventListener("change", () => changed(field.key, box.value.trim()));
      return [picker, box];
    }
    const box = node("input", { id, type: "text", spellcheck: "false", placeholder: field.placeholder ?? "" });
    box.addEventListener("change", () => changed(field.key, box.value.trim()));
    return [box];
  }

  function build() {
    els.form.replaceChildren();
    rows.clear();
    for (const field of FIELDS) {
      const rule = rules[field.key];
      if (!rule) continue; // a setting this daemon does not have
      const inputs = controlsFor(field, rule);
      const drop = node("button", {
        type: "button", class: "drop", title: "Back to the configured value", text: "x",
      });
      drop.addEventListener("click", () => queue(field.key, null));
      const row = node(
        "div",
        { class: "row" },
        node("label", { for: `overlay-field-${field.key}`, text: field.label }),
        node("span", { class: "controls" }, ...inputs),
        node("span", { class: "unit muted", text: field.unit ?? "" }),
        drop,
      );
      rows.set(field.key, { row, inputs, drop });
      els.form.append(row);
    }
  }

  // ------------------------------- showing ------------------------------- //

  function render() {
    if (!doc || !rules) return;
    for (const [key, { row, inputs, drop }] of rows) {
      const overridden = key in doc.overrides;
      row.classList.toggle("overridden", overridden);
      drop.hidden = !overridden;
      const configured = doc.defaults[key] === "" ? "(empty)" : doc.defaults[key];
      row.title = overridden ? `Configured value: ${configured}` : "";
      const field = FIELDS.find((candidate) => candidate.key === key);
      if (field.edges) {
        const [low, high] = marginSlider(field, doc.settings.anchor);
        inputs[0].min = String(low);
        inputs[0].max = String(high);
      }
      // A value on its way to the daemon is newer than the document in hand.
      if (key in pending || key in inFlight) continue;
      for (const input of inputs) {
        if (input !== document.activeElement) setInput(input, doc.settings[key]);
      }
    }
    const running = doc.clients > 0;
    els.status.textContent = running ? "overlay running" : "overlay not running";
    els.status.className = `pill ${running ? "on" : "off"}`;
    els.lines.textContent = doc.env_lines.length
      ? doc.env_lines.join("\n")
      : "Nothing is overridden; the configured values are in use.";
    els.reset.disabled = els.copy.disabled = !doc.env_lines.length;
  }

  function apply(next) {
    if (doc && next.version < doc.version) return; // stale
    doc = next;
    render();
  }

  // -------------------------------- preview ------------------------------ //

  function setPreview(on) {
    clearInterval(previewTimer);
    previewTimer = 0;
    els.preview.checked = on;
    if (on) {
      // The daemon ends a preview by itself; keep asking while the box stays ticked, so
      // a closed tab cannot leave the sample line on screen.
      const renew = () => postJson("/overlay/preview", { seconds: PREVIEW_S });
      renew();
      previewTimer = setInterval(renew, PREVIEW_RENEW_MS);
    } else {
      postJson("/overlay/preview", { seconds: 0 });
    }
  }

  // -------------------------------- wiring ------------------------------- //

  async function show() {
    els.pane.hidden = false;
    if (rules) return;
    try {
      const described = await getJson("/overlay");
      rules = described.rules;
      build();
      apply(described);
    } catch (error) {
      els.note.textContent = String(error);
    }
  }

  function hide() {
    els.pane.hidden = true;
    if (els.preview.checked) setPreview(false);
  }

  els.open.addEventListener("click", () => (els.pane.hidden ? show() : hide()));
  els.close.addEventListener("click", hide);
  els.preview.addEventListener("change", () => setPreview(els.preview.checked));
  els.form.addEventListener("submit", (event) => event.preventDefault());
  els.reset.addEventListener("click", async () => {
    pending = {};
    const result = await postJson("/overlay/reset", {});
    if (result.ok) apply(result.data);
  });
  els.copy.addEventListener("click", async () => {
    const text = `${doc.env_lines.join("\n")}\n`;
    try {
      await navigator.clipboard.writeText(text);
      els.note.textContent = "Copied. Paste the lines into your settings file, then restart.";
    } catch {
      // No clipboard access: select the lines so they can be copied by hand.
      getSelection().selectAllChildren(els.lines);
      els.note.textContent = "Select and copy the lines above.";
    }
  });

  return {
    apply,
    disconnected() {
      // A restarted daemon counts versions from 1 again; do not mistake it for stale.
      if (doc) doc = { ...doc, version: 0 };
    },
  };
}
