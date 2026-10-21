# TLDW — agent notes

Read `plans.md` first. It is the source of truth: goals, hard constraints, decisions,
output spec, phase checklist, working rules, and a running Findings log.

## Where we are
- Phases 0–1 done: intake (extension snapshot or API playlist), watched-% / length / optional
  topic filter, `tldw run --dry-run`, `tldw serve`/`import`, Chrome extension in `extension/`.
  Phases 2, 4, 5 done (captions, Claude script, TTS, YouTube/Telegram delivery, retry,
  migrate-wl, logs + usage report). **Blocked:** Phase 3 render/ffmpeg waits for the user to
  approve the draft slide (`uv run tldw sample-slide`); Watch Later removal is an open question.
- The user's answers to the open questions are recorded in `plans.md` and `config.yaml`:
  topics = ML / AI, Systems design, Finance, Korean history; output language = match
  source; delivery = YouTube private playlist + Telegram; captions only (no Whisper).
- `tldw list` has only been verified against the fake API in `tests/`; the first real
  run needs `client_secret.json` + `uv run tldw auth` on the user's machine (README).

## Working rules (from plans.md)
- One small commit per checklist item; tick the box in `plans.md` in the same commit.
- Before Phase 3, show the user one rendered sample slide and get approval of the style.
- If an API/dependency/quota behaves differently from the plan, append to Findings in
  `plans.md` and ask before redesigning.
- Boring, debuggable code. No secrets in the repo.
- Never download other people's video/audio streams; captions only.

## Commands
```bash
uv sync
uv run pytest -q
node --test extension/test/*.test.js
uv run ruff check . && uv run ruff format .
uv run tldw --help
```

## Layout
- `tldw/config.py` — config.yaml + .env (pydantic)
- `tldw/state.py` — SQLite stage machine; use `set_stage`, `fail`, `reset`
- `tldw/youtube.py` — OAuth, playlists, `fetch_video_details`, `quota` meter, `QuotaExceeded`
- `tldw/intake.py` — IntakeItem, snapshot parsing (`ingest_snapshot`), `from_playlist`
- `tldw/serve.py` — 127.0.0.1 receiver for the extension; `tldw/classify.py`, `tldw/filter.py`
- `tldw/transcript.py`, `tldw/script.py`, `tldw/tts.py`, `tldw/render.py` (draft), `tldw/deliver/`
- `tldw/pipeline.py` — glue for `tldw run` (steps + `Selection`); `tldw/cli.py` — typer app
- `tldw/usage.py` — LLM/TTS meters, run report, rotating log; `tldw/migrate.py` — Takeout CSV
- `extension/` — Chrome MV3; pure helpers in `parse.js` (node tests), DOM in `content.js`
- Tests use a fake YouTube resource (see `tests/test_youtube.py`); keep tests network-free.
