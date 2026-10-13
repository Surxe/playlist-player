// Drives the remote-control page in a real headless Chrome, for the page tests.
//
//   node tests/js/drive_page.mjs <page-url> <profile-dir> < steps.json > results.json
//
// Steps are a JSON list read from stdin. Each is one of
//   { "eval": "<expression>" }       evaluated in the page; a promise is awaited
//   { "wait": "<expression>" }       polled until truthy (5 s limit)
//   { "screenshot": "<file.png>" }
// and the output is a JSON list with one result per step. It talks to Chrome over the
// DevTools protocol with node's built-in WebSocket, so it needs no packages.

import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const [url, profile] = process.argv.slice(2);
const steps = JSON.parse(await new Promise((resolve) => {
  let text = "";
  process.stdin.on("data", (chunk) => { text += chunk; });
  process.stdin.on("end", () => resolve(text));
}));

const chrome = spawn(process.env.CHROME ?? "google-chrome", [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
  "--window-size=1280,900", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

function fail(message) {
  chrome.kill();
  console.error(message);
  process.exit(1);
}
const deadline = setTimeout(() => fail("timed out driving the page"), 60_000);

const port = await new Promise((resolve) => {
  let seen = "";
  chrome.stderr.on("data", (chunk) => {
    seen += chunk;
    const found = seen.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//);
    if (found) resolve(Number(found[1]));
  });
  chrome.on("exit", (code) => fail(`chrome exited early (${code}): ${seen.slice(-400)}`));
});

const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const socket = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve, { once: true }));

let nextId = 0;
const waiting = new Map();
socket.addEventListener("message", (message) => {
  const data = JSON.parse(message.data);
  if (data.id && waiting.has(data.id)) {
    waiting.get(data.id)(data);
    waiting.delete(data.id);
  }
});
function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve) => waiting.set(id, resolve));
}
async function evaluate(expression) {
  const reply = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (reply.error || reply.result.exceptionDetails) {
    const detail = reply.error?.message ?? reply.result.exceptionDetails.exception?.description;
    fail(`page error in ${expression}: ${detail}`);
  }
  return reply.result.result.value ?? null;
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

await send("Page.enable");
await send("Page.navigate", { url });
const results = [];
for (const step of steps) {
  if ("eval" in step) {
    results.push(await evaluate(step.eval));
  } else if ("wait" in step) {
    const until = Date.now() + 5000;
    let value = false;
    while (!(value = await evaluate(`Boolean(${step.wait})`))) {
      if (Date.now() > until) fail(`never became true: ${step.wait}`);
      await sleep(40);
    }
    results.push(value);
  } else if ("screenshot" in step) {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(step.screenshot, Buffer.from(shot.result.data, "base64"));
    results.push(step.screenshot);
  }
}
clearTimeout(deadline);
console.log(JSON.stringify(results));
socket.close();
chrome.removeAllListeners("exit");
chrome.kill();
process.exit(0);
