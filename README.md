# TLDW

Turns the videos you keep saving to Watch Later (and never watching) into short vertical
summary videos you can swipe through on your phone.

Pipeline: **Playlist → topic filter → transcript → summary script → render 9:16 → deliver
→ remove from playlist.** See `plans.md` for the full design and phase checklist.

## Status

Phase 0 (scaffold, config, auth, state) is done. `tldw auth` and `tldw list` work.
Phases 1–5 are in progress — see `plans.md`.

## One-time setup

### 1. Python env

```bash
uv sync            # creates .venv with all deps (Python 3.11+)
uv run tldw --help
```

### 2. YouTube playlist

In YouTube, create a playlist named **`TLDW`** (or change `playlist_name` in
`config.yaml`). Save videos there instead of Watch Later — the API cannot read Watch
Later. Your existing Watch Later backlog has to be moved by hand (or later via
`tldw migrate-wl` with a Google Takeout export).

### 3. Google OAuth client (do this on your own computer — it needs a browser)

1. Go to Google Cloud Console, create a project (any name).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen** → External, fill in app name + your email.
   Add your own Google account under *Test users*.
   After it works, set the app to **In production** — in *Testing* status refresh tokens
   expire every 7 days and nightly runs would break.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the
   JSON and save it as `client_secret.json` in this folder (it is git-ignored).
5. Run:

   ```bash
   uv run tldw auth
   ```

   A browser opens; sign in and allow the `youtube` + `youtube.upload` scopes (you'll
   see an "unverified app" warning — Advanced → continue is fine for your own account).
   The token is saved to `token.json` (git-ignored, mode 600).

### 4. Secrets

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY now; TELEGRAM_* when Phase 4 lands
```

## Check it works

```bash
uv run tldw list
```

prints every video in `TLDW` with its length and pipeline stage (`untracked` until the
first `tldw run`), plus the API quota the call used.

## Development

```bash
uv run pytest            # unit tests (no network, fake YouTube API)
uv run ruff check . && uv run ruff format .
uv run pre-commit install
```

Secrets never go in the repo: `.env`, `client_secret*.json`, `token.json`, `state.db`,
`cache/`, `out/`, `logs/` are all git-ignored.
