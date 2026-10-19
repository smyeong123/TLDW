// Runs on youtube.com. On request from the popup, scrolls Watch Later until every item is
// loaded, then reads each row's id, title, channel, length, and resume-bar width.
// YouTube's markup is undocumented and changes; selectors below try several known variants.

(function () {
  "use strict";
  const P = globalThis.TLDWParse;

  const ROW = "ytd-playlist-video-renderer";
  const SELECTORS = {
    link: ["a#video-title", "a#thumbnail"],
    channel: ["ytd-channel-name #text a", "ytd-channel-name #text", "#channel-name a"],
    duration: [
      "ytd-thumbnail-overlay-time-status-renderer #text",
      "ytd-thumbnail-overlay-time-status-renderer badge-shape",
      "badge-shape .badge-shape-wiz__text",
    ],
    progress: ["ytd-thumbnail-overlay-resume-playback-renderer #progress", "#progress"],
  };

  function first(el, selectors) {
    for (const s of selectors) {
      const hit = el.querySelector(s);
      if (hit) return hit;
    }
    return null;
  }

  function readRow(row) {
    const link = first(row, SELECTORS.link);
    const channel = first(row, SELECTORS.channel);
    const duration = first(row, SELECTORS.duration);
    const progress = first(row, SELECTORS.progress);
    return {
      href: link ? link.getAttribute("href") : null,
      title: link ? link.getAttribute("title") || link.textContent : "",
      channel: channel ? channel.textContent : "",
      durationText: duration ? duration.textContent : "",
      progressWidth: progress ? progress.style.width : "",
    };
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Keep scrolling until the row count stops growing for a few rounds (or limits hit).
  async function loadAll({ maxMs = 120000, stableRounds = 4 } = {}) {
    const start = Date.now();
    let last = -1;
    let stable = 0;
    while (Date.now() - start < maxMs && stable < stableRounds) {
      window.scrollTo(0, document.documentElement.scrollHeight);
      await sleep(800);
      const n = document.querySelectorAll(ROW).length;
      stable = n === last ? stable + 1 : 0;
      last = n;
    }
    window.scrollTo(0, 0);
  }

  async function collect() {
    if (!P.isWatchLaterUrl(location.href)) {
      return { ok: false, error: "Open youtube.com/playlist?list=WL first." };
    }
    await loadAll();
    const rows = Array.from(document.querySelectorAll(ROW)).map(readRow);
    const snapshot = P.buildSnapshot(rows, new Date());
    if (rows.length > 0 && snapshot.items.length === 0) {
      return { ok: false, error: "Found rows but could not read them; YouTube markup changed?" };
    }
    return { ok: true, snapshot };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "tldw-collect") {
      collect()
        .then(sendResponse)
        .catch((e) => sendResponse({ ok: false, error: String(e) }));
      return true; // async response
    }
    return false;
  });
})();
