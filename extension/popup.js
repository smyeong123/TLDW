"use strict";

const WL_URL = "https://www.youtube.com/playlist?list=WL";
const statusEl = document.getElementById("status");
const sendBtn = document.getElementById("send");
const downloadBtn = document.getElementById("download");
let lastSnapshot = null;

function show(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

async function settings() {
  const { token = "", port = 8765 } = await chrome.storage.local.get(["token", "port"]);
  return { token, port };
}

async function collectFromTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.startsWith(WL_URL)) {
    await chrome.tabs.create({ url: WL_URL });
    throw new Error("Opened Watch Later in a new tab. Click Send again once it has loaded.");
  }
  const res = await chrome.tabs.sendMessage(tab.id, { type: "tldw-collect" });
  if (!res || !res.ok) throw new Error((res && res.error) || "No answer from the page; reload it.");
  return res.snapshot;
}

async function post(snapshot) {
  const { token, port } = await settings();
  if (!token) throw new Error("Set the token in Settings (same as TLDW_EXTENSION_TOKEN).");
  const resp = await fetch(`http://127.0.0.1:${port}/snapshot`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-TLDW-Token": token },
    body: JSON.stringify(snapshot),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(`tldw serve said ${resp.status}: ${body.error || "error"}`);
  return body;
}

sendBtn.addEventListener("click", async () => {
  sendBtn.disabled = true;
  downloadBtn.hidden = true;
  show("Loading every Watch Later item (this scrolls the page)…");
  try {
    lastSnapshot = await collectFromTab();
    const n = lastSnapshot.items.length;
    show(`Read ${n} videos. Sending…`);
    try {
      const r = await post(lastSnapshot);
      show(`Sent ${n} videos: ${r.new} new, ${r.updated} updated.`, "ok");
    } catch (e) {
      downloadBtn.hidden = false;
      show(`Read ${n} videos but could not reach tldw serve:\n${e.message}`, "err");
    }
  } catch (e) {
    show(e.message, "err");
  } finally {
    sendBtn.disabled = false;
  }
});

downloadBtn.addEventListener("click", () => {
  if (!lastSnapshot) return;
  const blob = new Blob([JSON.stringify(lastSnapshot, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `tldw-watch-later-${lastSnapshot.taken_at.slice(0, 10)}.json`;
  a.click();
  show("Saved. Load it with: uv run tldw import <file>", "ok");
});

document.getElementById("options").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
