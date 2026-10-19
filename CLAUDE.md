# TLDW — agent notes

Read `plans.md` first. It is the source of truth: goals, hard constraints, decisions,
output spec, phase checklist, working rules, and a running Findings log.

## Where we are
- Phase 0 done (scaffold, config, OAuth, state). Next: Phase 1 (intake + topic filter). Watch-progress skip is blocked on a data-source decision (plans.md Findings 2026-10-20).
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
uv run ruff check . && uv run ruff format .
uv run tldw --help
```

## Layout
- `tldw/config.py` — config.yaml + .env (pydantic)
- `tldw/state.py` — SQLite stage machine; use `set_stage`, `fail`, `reset`
- `tldw/youtube.py` — OAuth, playlists, `fetch_video_details`, `quota` meter, `QuotaExceeded`
- `tldw/cli.py` — typer app; add `run`, `retry`, `migrate-wl` here in later phases
- Tests use a fake YouTube resource (see `tests/test_youtube.py`); keep tests network-free.
