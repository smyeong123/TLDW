"use strict";

const tokenEl = document.getElementById("token");
const portEl = document.getElementById("port");
const msgEl = document.getElementById("msg");

chrome.storage.local.get(["token", "port"]).then(({ token = "", port = 8765 }) => {
  tokenEl.value = token;
  portEl.value = port;
});

document.getElementById("save").addEventListener("click", async () => {
  const port = Number(portEl.value) || 8765;
  await chrome.storage.local.set({ token: tokenEl.value.trim(), port });
  msgEl.textContent = " Saved.";
});
