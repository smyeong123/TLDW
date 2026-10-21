# TLDW

Turns the videos you keep saving to Watch Later (and never watching) into short vertical
summary videos you can swipe through on your phone. Videos you've already watched past a
threshold (default 50%) are skipped.

Pipeline: **Watch Later → skip watched → (optional) topic filter → transcript → summary
script → render 9:16 → deliver.** See `plans.md` for the full design and phase checklist.

## Status

Done: intake (extension or API playlist), watched/length/topic filter, captions, Claude
script, TTS, delivery (YouTube private + Telegram), `tldw retry`. Waiting: slide style
approval (`uv run tldw sample-slide`) before video rendering is built. See `plans.md`.

## One-time setup

### 1. Python env

```bash
uv sync            # creates .venv with all deps (Python 3.11+)
uv run tldw --help
```

### 2. Secrets

```bash
cp .env.example .env
openssl rand -hex 24      # paste as TLDW_EXTENSION_TOKEN in .env
# ANTHROPIC_API_KEY: needed for topic filtering (if `topics` is set) and for scripts
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID: for Telegram delivery (see below)
```

**Telegram:** talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
Make a private channel, add the bot as an admin, post anything, then open
`https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` (starts with `-100`).

### 3. Browser extension (reads Watch Later + how much you watched)

The YouTube API can't read Watch Later or watch progress, so a small Chrome extension
reads the Watch Later page you're signed in to and sends it to the local CLI.

1. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** →
   pick the `extension/` folder of this repo.
2. Click the TLDW icon → **Settings**: paste the same token as `TLDW_EXTENSION_TOKEN`;
   the port defaults to 8765 (`serve_port` in `config.yaml`).
3. Start the receiver and leave it running:

   ```bash
   uv run tldw serve
   ```

4. Open <https://www.youtube.com/playlist?list=WL>, click the TLDW icon →
   **Send Watch Later to TLDW**. It scrolls the page to load every video, then sends
   the list. If `tldw serve` isn't running, use **Download JSON instead** and run
   `uv run tldw import <file>`.

Send a fresh snapshot every day or two. `tldw run` warns when the newest one is older than
`snapshot_max_age_hours` (default 48).

### 4. Google OAuth (needed for uploading shorts; optional for reading Watch Later)

Do this on your own computer, since it needs a browser.

1. Go to Google Cloud Console, create a project (any name).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen** → External, fill in app name + your email.
   Add your own Google account under *Test users*.
   After it works, set the app to **In production** — in *Testing* status refresh tokens
   expire every 7 days and nightly runs would break.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the
   JSON and save it as `client_secret.json` in this folder (it is git-ignored).
5. Run `uv run tldw auth`. A browser opens; allow the `youtube` + `youtube.upload` scopes
   ("unverified app" warning → Advanced → continue is fine for your own account).

With OAuth set up, the topic classifier also gets each video's description and tags
(1 quota unit per 50 videos); without it, it classifies by title and channel only.

**Alternative source:** set `source: playlist` in `config.yaml` to read a playlist you
created (`playlist_name`, default `TLDW`) through the API instead of Watch Later. Watch
progress isn't available that way, so nothing is skipped as watched.

## Check it works

```bash
uv run tldw run --dry-run
```

shows every pending video and what would happen to it (`process`, `watched`, `too_long`,
`off_topic`) and why, without changing anything. Drop `--dry-run` to record the decisions
and go on to captions and scripts (`--until filtered|transcribed|scripted|delivered`,
`--limit N`). Failed videos: `uv run tldw retry` lists them, `tldw retry --all` requeues.

## Run it nightly

Every run appends to `logs/tldw.log` (rotated at 1 MB, 5 files kept) and ends with a
usage line: YouTube API units, Claude calls/tokens, TTS seconds.

**cron** (`crontab -e`). Use full paths, since cron has a minimal `PATH`:

```cron
# 03:30 every night: filter, captions, scripts, deliver (up to max_shorts_per_run)
30 3 * * * cd /path/to/TLDW && /opt/homebrew/bin/uv run tldw run --until delivered >> logs/cron.log 2>&1
```

On macOS, a sleeping laptop skips cron jobs. A `launchd` agent with
`StartCalendarInterval` runs the job when the Mac wakes up instead. Keep sending a
fresh Watch Later snapshot from the extension every day or two so watched-% is current.

## Development

```bash
uv run pytest            # unit tests (no network; fake YouTube/Claude clients)
node --test extension/test/*.test.js
uv run ruff check . && uv run ruff format .
uv run pre-commit install
```

Secrets never go in the repo: `.env`, `client_secret*.json`, `token.json`, `state.db`,
`cache/`, `out/`, `logs/` are all git-ignored.
