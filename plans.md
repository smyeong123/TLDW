# plans.md — TLDW (Too Long; Didn't Watch): YouTube backlog → short summaries

## Goal
TLDW is for people who are too busy to watch the videos they save. They stack them in
Watch Later and never come back. TLDW turns that backlog into short vertical summary
videos you can swipe through on your phone. Any kind of video counts, not only
educational ones. Each short must give the context of the source video and its key
points, not random clips.

Videos you have already mostly watched don't need a summary. If you have watched at
least `watched_threshold` of a video (default 50%), TLDW treats it as watched and skips it.

Pipeline: **Playlist → skip watched → topic filter → transcript → summary script →
render 9:16 video → deliver → remove from playlist.**

## Hard constraints (do not try to work around these)
- The YouTube Data API returns an empty list for the Watch Later (`WL`) playlist and
  cannot delete from it. It also has no watch-progress data. Therefore Watch Later (with
  each video's watched %) is read by the **TLDW browser extension** from the youtube.com
  page you are already signed in to, and sent to the local CLI (decided 2026-10-20, see
  Findings). The extension only reads pages you open; it never fetches with copied cookies.
  A user-created playlist read through the API (`source: playlist`) stays as an alternative.
- Do **not** download video/audio streams of other people's videos. Use captions/
  transcripts only. Shorts are generated from our own script, TTS, and slides.
- YouTube Data API default quota is 10,000 units/day; `videos.insert` costs ~1,600
  units → max ~6 uploads/day. Design for batching and make Telegram delivery an
  alternative that has no such limit.
- No secrets in the repo. Everything via `.env` / OAuth token files in `.gitignore`.

## Decisions (defaults — change only if the user says so)
| Concern | Choice |
|---|---|
| Language | Python 3.11+, `uv` for env/deps |
| Intake | Default `source: watch_later`: a Chrome MV3 extension (`extension/`) reads Watch Later + watched % on youtube.com and POSTs a snapshot to `tldw serve` (127.0.0.1 only, shared token), or exports a JSON file for `tldw import`. Alternative `source: playlist`: YouTube Data API v3 (OAuth, scopes: `youtube` + `youtube.upload`, also used for upload) |
| Watched skip | `watched_pct ≥ watched_threshold` (default 0.5) → stage `watched`, never summarized |
| Topic filter | Optional. Empty `topics` = summarize everything; otherwise classify and filter |
| Transcript | `youtube-transcript-api` → fallback `yt-dlp --write-auto-sub --skip-download` → fallback `faster-whisper` on audio only if the user explicitly enables it |
| LLM | Anthropic API (Claude). One call for topic classification, one for the script |
| TTS | `edge-tts` (free, has Korean + English voices). Abstract behind an interface so ElevenLabs/OpenAI can be swapped in |
| Render | Pillow for slide PNGs + `ffmpeg` to assemble 1080×1920, burned-in captions |
| Delivery | Primary: upload as **Private** to my channel into playlist `TLDW Shorts`. Secondary: Telegram bot posting to a private channel |
| State | SQLite (`state.db`) tracking every video's stage so reruns are idempotent |
| Scheduling | `cron` entry running `tldw run` nightly; also runnable manually |

## Output spec (every short)
- 9:16, 1080×1920, 60–150 s, H.264 + AAC
- Structure of the script (LLM must follow exactly):
  1. **Context** (≤2 sentences): what the video is, who made it, why it matters
  2. **3–5 key points**, each one claim + one supporting detail
  3. **Takeaway** (1 sentence): what to remember or do
- Script language = `config.output_language` (default: same as the video's language)
- Each point carries a source timestamp; show it on the slide and put all timestamps
  + the source URL in the upload description
- Title format: `[Topic] <short title> — TLDW of "<original title>"`

## Repo layout
```
tldw/
  cli.py            # typer CLI: auth, run, list, retry, migrate-wl
  config.py         # pydantic settings from config.yaml + .env
  youtube.py        # playlist list/delete, upload, OAuth
  classify.py       # topic filter
  transcript.py     # caption fetching with fallbacks
  script.py         # LLM summary → Script dataclass
  tts.py            # TTS interface + edge-tts impl
  render.py         # slides + ffmpeg assembly
  deliver/
    youtube.py
    telegram.py
  state.py          # SQLite stage tracking
config.yaml
.env.example
tests/
plans.md
```

## Phases — work in order, run end-to-end on ONE video before scaling

### Phase 0 — Scaffold & auth
- [x] `uv init`, deps, `pre-commit` with ruff
- [x] `config.yaml` schema: `playlist_name`, `topics: [...]`, `output_language`,
      `max_shorts_per_run`, `delivery: [youtube|telegram]`, `tts_voice`
- [x] Google Cloud project + OAuth client (desktop app). `tldw auth` stores token  _(code done; user must create the OAuth client and run `tldw auth` locally — see README)_
- [x] `state.py`: table `videos(id, title, stage, topic, error, created_at, updated_at)`
      stages: `new → filtered_in|filtered_out → transcribed → scripted → rendered → delivered → removed → failed`
- Done when: `tldw list` prints the TLDW playlist contents.  _(verified against a mocked API; real run pending user credentials)_

### Phase 1 — Intake + topic filter
- [x] Fetch playlist items with snippet + contentDetails; store new ones as `new` _(`intake.from_playlist`, `source: playlist`)_
- [x] `classify.py`: prompt with title, description, tags, chapters, channel →
      returns `{topic | null, confidence}` against `config.topics`
- [x] Below `confidence_threshold` (default 0.6) → `filtered_out`, but **do not**
      remove from playlist (user may want to watch it normally)
- [x] Skip videos > `max_source_minutes` (default 120) with a logged reason
- [x] Make `topics` optional: an empty list skips classification and keeps every video
- [x] Snapshot ingest (Python): `tldw import <file.json>` and `tldw serve`
      (127.0.0.1, `TLDW_EXTENSION_TOKEN` header) both call one `ingest_snapshot()` that
      upserts `{video_id, title, channel, duration_s, watched_pct}` and records `snapshot_at`
- [x] Skip videos watched ≥ `watched_threshold` (default 0.5) → new stage `watched`
      (reachable from `new`/`filtered_in`, re-checked on every snapshot)
- [x] `extension/`: MV3 content script on `youtube.com/playlist?list=WL` scrolls the list
      to load every item, reads id/title/channel/length/progress-bar %, then a popup
      button sends it to `tldw serve` (or downloads the JSON when serve isn't running)
- [x] `source: watch_later | playlist` in config; `tldw run` warns if the newest snapshot
      is older than `snapshot_max_age_hours` (default 48)
- Done when: `tldw run --dry-run` shows which videos would be processed and why
  (watched / too long / off-topic / ok).

### Phase 2 — Transcript + script
- [x] `transcript.py` with the three-tier fallback; store transcript to `cache/<id>.json`
      _(two tiers; Whisper tier dropped per the captions-only decision)_
- [x] `script.py`: Claude call that returns strict JSON matching `Script` schema
      (`context`, `points[{claim, detail, timestamp_s}]`, `takeaway`, `title`, `language`)
- [x] Validate: 3–5 points, each timestamp within video duration, total word count
      fits 60–150 s at ~150 wpm; retry once with feedback if invalid
- [x] Unit tests with a fixture transcript
- Done when: `tldw run --until scripted` produces readable JSON scripts.

### Phase 3 — Render
- [x] `tts.py`: synthesize each section separately; keep per-section durations
- [ ] `render.py`: one slide per section (title slide, N point slides, takeaway slide).
      Big readable text, high contrast, topic label top-left, timestamp bottom-right.
      No logos, thumbnails, or frames from the source video
- [ ] ffmpeg: concat slides with audio, burn captions (word-level if TTS provides
      timings, else per-section), output `out/<id>.mp4`
- [ ] `tldw preview <id>` opens the file locally
- Done when: a sample short plays correctly on a phone and is under 3 minutes.

### Phase 4 — Deliver + cleanup
- [x] `deliver/youtube.py`: `videos.insert` (privacyStatus=private, selfDeclaredMadeForKids=false),
      then `playlistItems.insert` into `TLDW Shorts`. Respect `max_shorts_per_run`
      and stop gracefully on quota errors
- [x] `deliver/telegram.py`: send video with caption = title + takeaway + source URL
- [x] On successful delivery: `playlistItems.delete` the source from `TLDW` → `removed`
      (`source: playlist` only; for Watch Later see "Open questions (later)")
- [x] Failures go to `failed` with error text; `tldw retry` reprocesses them
- Done when: a short appears in my YouTube `TLDW Shorts` playlist and the source is gone from `TLDW`.

### Phase 5 — Operate
- [ ] `tldw run` is idempotent; add `--limit`, `--topic`, `--since`
- [ ] `tldw migrate-wl <takeout_csv>`: adds videos from a Watch Later export into `TLDW`
- [ ] Cron example in README; log to `logs/` with rotation
- [ ] Cost/quota report at end of each run (API units used, LLM tokens, TTS seconds)

## Agent working rules
- Make one small commit per checklist item; keep `plans.md` checkboxes updated.
- Before Phase 3, ask me to approve the slide style with one rendered sample.
- If a dependency, API, or quota behaves differently from this plan, write what you
  found under "Findings" below and ask before redesigning.
- Prefer boring, debuggable code over clever abstractions.

## Open questions for the user — ANSWERED 2026-10-19
1. Topics: `ML / AI`, `Systems design`, `Finance`, `Korean history` (in `config.yaml`)
2. Output language: **match the source** (`output_language: match`)
3. Delivery: **both** YouTube private playlist and Telegram
4. **Captions only.** No Whisper fallback (`whisper_fallback: false`; the code path stays disabled)
5. (2026-10-20) Watch progress comes from a **browser extension**; the topic filter is
   **optional** (empty `topics` = everything)

## Open questions (later, not blocking)
- Removing delivered videos from Watch Later: the API can't do it. Proposed: in Phase 4
  the extension shows a "Remove N summarized videos" button that clicks YouTube's own
  menu. The user starts each removal; it never runs on its own. Confirm before building.

## Findings
- **2026-10-19 (Phase 0)** OAuth for a Desktop-app client needs a browser and a localhost
  redirect, so `tldw auth` must be run once on your own machine (not in a headless box).
  After that the saved refresh token is reused by cron runs. Gotcha: while the Google
  Cloud OAuth consent screen is in *Testing* status, refresh tokens expire after 7 days,
  which would break nightly cron runs weekly. Switch the consent screen to *In production*
  for personal use; Google then shows an "unverified app" warning during `tldw auth`
  (Advanced → continue), which is fine for your own account.
- **2026-10-19 (Phase 0)** `ruff-pre-commit` renamed its hook id from `ruff` to `ruff-check`;
  the config uses the new id with ruff 0.17.x.
- **2026-10-19 (Phase 0)** `videos.list` is 1 unit per 50 IDs, so `tldw list` costs ~3 units
  for a 100-video playlist. A `QuotaMeter` in `youtube.py` counts units per call for the
  Phase 5 report.
- **2026-10-19 (Phase 0)** `playlistItems.delete` needs the *playlist item id*, not the video
  id, so `state.videos` stores `playlist_item_id` (refreshed on every intake, since it changes
  if you remove and re-add a video).
- **2026-10-20** Project renamed from "Lazy Digest" to **TLDW**. The package/CLI is now `tldw`,
  and the default playlists are `TLDW` (input) and `TLDW Shorts` (output). TLDW covers any
  video, not only educational ones; the topic filter stays as a user setting.
- **2026-10-20 (watch-progress requirement)** The YouTube Data API v3 has **no watch-progress
  or watch-history data**. The `HL` history playlist has returned nothing since 2016, and no
  endpoint gives a resume position. **Decision (user, 2026-10-20): option 1 via a browser
  extension** that reads the page the user opens; no cookie-based fetching. Options considered:
  1. *YouTube web page data*: the Watch Later page (`/playlist?list=WL`) includes
     `percentDurationWatched` for each video in its embedded page data. This could be read
     by a small browser extension or userscript, or by fetching the page with the
     user's browser cookies. That would also make the Watch Later list readable, which
     the API can't do. Downsides: it's an undocumented internal format that may change, and cookie-based
     fetching is against YouTube's ToS. Removing videos from Watch Later would still be manual.
  2. *Google Takeout `watch-history.json`*: lists which videos were opened, but not how far
     they were watched. It can only give "opened at all", not the 50% threshold.
  3. *Manual marking*: the user marks videos as watched (e.g. `tldw skip <id>`).
     Simple, but it is extra effort for the user.
- **2026-10-20 (Phase 1, extension)** The content script reads the rendered Watch Later DOM
  (`ytd-playlist-video-renderer`, the resume bar `#progress` width = watched %). Content
  scripts run in an isolated world and can't see `ytInitialData`, so the DOM is the only
  reliable source. The selectors have **not been checked against live YouTube yet** (no
  browser in the dev loop). The first real run should check that `watched_pct` matches
  the red bars. If YouTube changes its markup, the popup reports "could not read them",
  and the fix goes in `SELECTORS` in `extension/content.js`. A video never opened has no
  bar, so it counts as 0% watched.
- **2026-10-20 (Phase 1)** Classifier: `anthropic` 1.15 `messages.parse(output_format=...)`
  at effort `low`. The server-side refusal `fallbacks` parameter isn't on the non-beta
  `parse`; a refused classification just counts as off-topic.
- **2026-10-20 (Phase 2, transcripts)** `youtube-transcript-api` 1.2 uses an instance
  API (`YouTubeTranscriptApi().list(id)` → tracks with `language_code`/`is_generated`).
  The track is chosen by spoken language: an auto-generated track (yt-dlp: the `<lang>-orig`
  key) tells us what's spoken. A manual track in that language beats the auto one, so
  uploader translations aren't picked by mistake. The yt-dlp tier always runs with
  `--skip-download` and writes only a json3 subtitle file, which is deleted after parsing.
  Both tools can get blocked from datacenter IPs (`RequestBlocked`/`IpBlocked`); on a home
  connection they normally work.
- **2026-10-20 (Phase 3 gate) — waiting for the user's approval.** Draft point slide:
  `uv run tldw sample-slide` (→ `out/sample_slide.png`) and `--lang ko`
  (→ `out/sample_slide_ko.png`). Draft style: slate-900 background, white bold claim
  (76 px), muted detail (50 px), amber topic pill + "2 / 4" top-left, `TLDW · <channel>`
  bottom-left, amber `▶ m:ss` source timestamp bottom-right. Text stays out of the
  bottom ~22% and the right edge, where the Shorts UI sits. Font: Apple SD Gothic Neo
  (Latin + Hangul) on macOS, Noto Sans CJK on Linux. Title/takeaway slides, captions,
  and ffmpeg assembly will **not** be built until the style is approved.
- **2026-10-20 (Phase 4)** Delivery records each channel in a `deliveries` table, so if
  Telegram fails after YouTube succeeded, the retry won't upload to YouTube again (that
  would cost 1,600 quota units). With `source: watch_later` the video stays `delivered`:
  it is not removed from Watch Later, which is still an open question. Uploads use
  category 22 (People & Blogs) because TLDW covers any kind of video, and titles are
  clipped to YouTube's 100-char limit by shortening the original title first. Phase 4's
  done-criterion can't be checked until rendering exists (style gate) and real
  credentials are set.
