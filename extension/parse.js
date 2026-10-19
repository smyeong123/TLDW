// Pure helpers shared by the content script and the node tests. No DOM access here.
// Loaded as a classic script in the page (defines globalThis.TLDWParse) and via require() in tests.

(function (root) {
  "use strict";

  const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

  // "/watch?v=dQw4w9WgXcQ&list=WL&index=3" -> "dQw4w9WgXcQ"; null if absent/invalid.
  function videoIdFromHref(href) {
    if (!href) return null;
    let url;
    try {
      url = new URL(href, "https://www.youtube.com");
    } catch (e) {
      return null;
    }
    const id = url.searchParams.get("v");
    return id && VIDEO_ID_RE.test(id) ? id : null;
  }

  // "1:02:03" -> 3723, "12:34" -> 754. Badges like "LIVE" / "SHORTS" -> null.
  function parseDuration(text) {
    if (!text) return null;
    const t = text.trim();
    if (!/^\d+(:\d{1,2}){1,2}$/.test(t)) return null;
    return t.split(":").reduce((acc, part) => acc * 60 + Number(part), 0);
  }

  // Resume bar width "45%" -> 0.45, clamped to 0..1. No bar (never opened) -> 0.
  function parseProgress(styleWidth) {
    if (!styleWidth) return 0;
    const m = /^\s*([\d.]+)\s*%\s*$/.exec(styleWidth);
    if (!m) return 0;
    const pct = Number(m[1]) / 100;
    return Math.max(0, Math.min(1, Math.round(pct * 1000) / 1000));
  }

  // Raw rows scraped from the page -> snapshot JSON accepted by `tldw serve` / `tldw import`.
  function buildSnapshot(rows, now) {
    const seen = new Set();
    const items = [];
    for (const r of rows) {
      const id = videoIdFromHref(r.href);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      items.push({
        video_id: id,
        title: (r.title || "").trim() || id,
        channel: (r.channel || "").trim() || null,
        duration_s: parseDuration(r.durationText),
        watched_pct: parseProgress(r.progressWidth),
      });
    }
    return {
      version: 1,
      source: "watch_later",
      taken_at: (now || new Date()).toISOString(),
      items,
    };
  }

  function isWatchLaterUrl(href) {
    try {
      const url = new URL(href);
      return (
        url.hostname === "www.youtube.com" &&
        url.pathname === "/playlist" &&
        url.searchParams.get("list") === "WL"
      );
    } catch (e) {
      return false;
    }
  }

  const api = { videoIdFromHref, parseDuration, parseProgress, buildSnapshot, isWatchLaterUrl };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TLDWParse = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
