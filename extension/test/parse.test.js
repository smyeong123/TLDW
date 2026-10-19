"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../parse.js");

test("videoIdFromHref", () => {
  assert.equal(P.videoIdFromHref("/watch?v=dQw4w9WgXcQ&list=WL&index=3"), "dQw4w9WgXcQ");
  assert.equal(P.videoIdFromHref("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ");
  assert.equal(P.videoIdFromHref("/watch?v=short"), null);
  assert.equal(P.videoIdFromHref("/shorts/abc"), null);
  assert.equal(P.videoIdFromHref(null), null);
});

test("parseDuration", () => {
  assert.equal(P.parseDuration(" 12:34 "), 754);
  assert.equal(P.parseDuration("1:02:03"), 3723);
  assert.equal(P.parseDuration("0:45"), 45);
  assert.equal(P.parseDuration("LIVE"), null);
  assert.equal(P.parseDuration(""), null);
});

test("parseProgress", () => {
  assert.equal(P.parseProgress("45%"), 0.45);
  assert.equal(P.parseProgress("100%"), 1);
  assert.equal(P.parseProgress("150%"), 1);
  assert.equal(P.parseProgress(""), 0);
  assert.equal(P.parseProgress("auto"), 0);
});

test("buildSnapshot matches the tldw snapshot format", () => {
  const rows = [
    {
      href: "/watch?v=aaaaaaaaaaa&list=WL",
      title: " Video A ",
      channel: "Chan",
      durationText: "10:00",
      progressWidth: "60%",
    },
    { href: "/watch?v=aaaaaaaaaaa&list=WL", title: "dup" },
    { href: "/watch?v=bbbbbbbbbbb", title: "", channel: "", durationText: "LIVE" },
    { href: null, title: "no link" },
  ];
  const snap = P.buildSnapshot(rows, new Date("2026-10-20T01:00:00Z"));
  assert.deepEqual(snap, {
    version: 1,
    source: "watch_later",
    taken_at: "2026-10-20T01:00:00.000Z",
    items: [
      {
        video_id: "aaaaaaaaaaa",
        title: "Video A",
        channel: "Chan",
        duration_s: 600,
        watched_pct: 0.6,
      },
      {
        video_id: "bbbbbbbbbbb",
        title: "bbbbbbbbbbb",
        channel: null,
        duration_s: null,
        watched_pct: 0,
      },
    ],
  });
});

test("isWatchLaterUrl", () => {
  assert.ok(P.isWatchLaterUrl("https://www.youtube.com/playlist?list=WL"));
  assert.ok(!P.isWatchLaterUrl("https://www.youtube.com/playlist?list=PL123"));
  assert.ok(!P.isWatchLaterUrl("https://evil.example/playlist?list=WL"));
});
