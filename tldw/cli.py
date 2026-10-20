"""Typer CLI: auth, list, run, import, serve; retry, migrate-wl are added in later phases."""

from __future__ import annotations

import json
import logging
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tldw import filter as flt
from tldw import intake, pipeline, render, youtube
from tldw.config import AppConfig, load_config
from tldw.serve import make_server
from tldw.state import Stage, State

app = typer.Typer(
    help="TLDW: turn the videos you saved but never watched into short summaries.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
err_console = Console(stderr=True)

ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", help="Path to config.yaml", show_default=True)
]


def _load(config_path: Path) -> AppConfig:
    try:
        return load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[red]config error:[/red] {e}")
        raise typer.Exit(2) from e


def _service(cfg: AppConfig):
    try:
        return youtube.require_service(cfg.secrets)
    except youtube.AuthError as e:
        err_console.print(f"[red]auth error:[/red] {e}")
        raise typer.Exit(3) from e


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@app.command()
def auth(
    config: ConfigOpt = Path("config.yaml"),
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the URL instead of opening a browser")
    ] = False,
) -> None:
    """Run the Google OAuth flow once and store the token (see .env.example)."""
    cfg = _load(config)
    try:
        creds = youtube.run_auth_flow(cfg.secrets, open_browser=not no_browser)
    except youtube.AuthError as e:
        err_console.print(f"[red]auth error:[/red] {e}")
        raise typer.Exit(3) from e
    yt = youtube.build_service(creds)
    me = yt.channels().list(part="snippet", mine=True).execute()
    name = me["items"][0]["snippet"]["title"] if me.get("items") else "(unknown channel)"
    console.print(f"[green]Authenticated[/green] as [bold]{name}[/bold]")
    console.print(f"Token saved to {cfg.secrets.google_token_file}")


@app.command("list")
def list_cmd(config: ConfigOpt = Path("config.yaml")) -> None:
    """Print the contents of the TLDW playlist with each video's pipeline stage."""
    cfg = _load(config)
    yt = _service(cfg)

    playlist_id = youtube.find_playlist_id(yt, cfg.config.playlist_name)
    if playlist_id is None:
        err_console.print(
            f"[red]playlist not found:[/red] no playlist named "
            f"'{cfg.config.playlist_name}' on your channel. Create it in YouTube "
            f"(or change playlist_name in {config})."
        )
        raise typer.Exit(4)

    entries = list(youtube.iter_playlist_items(yt, playlist_id))
    details = youtube.fetch_video_details(yt, [e.video_id for e in entries])

    table = Table(title=f"Playlist '{cfg.config.playlist_name}' ({len(entries)} videos)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("video id", style="cyan", no_wrap=True)
    table.add_column("title", max_width=60)
    table.add_column("channel", max_width=24)
    table.add_column("length", justify="right")
    table.add_column("stage")
    table.add_column("topic")

    with State(cfg.state_db) as state:
        for i, e in enumerate(entries, 1):
            d = details.get(e.video_id)
            rec = state.get(e.video_id)
            table.add_row(
                str(i),
                e.video_id,
                e.title,
                (d.channel if d else e.channel) or "-",
                fmt_duration(d.duration_s if d else None),
                rec.stage if rec else "[dim]untracked[/dim]",
                (rec.topic if rec and rec.topic else "-"),
            )
    console.print(table)
    console.print(f"[dim]API quota used this call: {youtube.quota.used} units[/dim]")


class Until(StrEnum):
    filtered = "filtered"
    transcribed = "transcribed"
    scripted = "scripted"


_VERDICT_STYLE = {
    "process": "green",
    "watched": "dim",
    "too_long": "yellow",
    "off_topic": "yellow",
    "error": "red",
}


@app.command()
def run(
    config: ConfigOpt = Path("config.yaml"),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show filter decisions without changing anything")
    ] = False,
    until: Annotated[
        Until, typer.Option(help="Stop after this stage (default: as far as built)")
    ] = Until.scripted,
    limit: Annotated[
        int | None, typer.Option(help="Max videos per step (default: max_shorts_per_run)")
    ] = None,
) -> None:
    """Intake -> filter -> transcript -> script. Each step picks up where the last run left off."""
    cfg = _load(config)
    limit = limit or cfg.config.max_shorts_per_run
    with State(cfg.state_db) as state:
        if cfg.config.source == "playlist":
            yt = _service(cfg)
            try:
                items = intake.from_playlist(yt, cfg.config.playlist_name)
            except intake.PlaylistNotFound as e:
                err_console.print(f"[red]playlist not found:[/red] '{e}'")
                raise typer.Exit(4) from e
            got = intake.ingest(state, items)
            console.print(f"Playlist: {got.total} videos ({got.new} new)")
        else:
            yt = pipeline.optional_service(cfg)
            warning = pipeline.snapshot_age_warning(state, cfg.config)
            if warning:
                err_console.print(f"[yellow]warning:[/yellow] {warning}")

        try:
            classifier = pipeline.make_classifier(cfg)
        except ValueError as e:
            err_console.print(f"[red]config error:[/red] {e}")
            raise typer.Exit(2) from e
        infos = {}
        if classifier is not None:
            infos = pipeline.video_infos(yt, [r.id for r in flt.pending(state, cfg.config)])
        decisions = flt.run_filter(state, cfg.config, classifier, infos=infos, dry_run=dry_run)
        _print_decisions(decisions, dry_run)
        if dry_run or until == Until.filtered:
            return

        steps = pipeline.transcribe_step(state, cfg, limit=limit)
        if until != Until.transcribed and state.list(Stage.TRANSCRIBED):
            try:
                client = pipeline.anthropic_client(cfg)
            except ValueError as e:
                _print_steps(steps)
                err_console.print(f"[red]config error:[/red] {e}")
                raise typer.Exit(2) from e
            steps += pipeline.script_step(state, cfg, client, limit=limit)
        _print_steps(steps)


def _print_decisions(decisions: list[flt.Decision], dry_run: bool) -> None:
    title = "Dry run: nothing changed" if dry_run else "Filter decisions"
    table = Table(title=f"{title} ({len(decisions)} videos)")
    table.add_column("video id", style="cyan", no_wrap=True)
    table.add_column("title", max_width=50)
    table.add_column("decision")
    table.add_column("why", max_width=60)
    for d in decisions:
        style = _VERDICT_STYLE[d.verdict]
        table.add_row(d.video_id, d.title, f"[{style}]{d.verdict}[/{style}]", d.reason)
    console.print(table)
    counts = Counter(d.verdict for d in decisions)
    console.print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing pending")


def _print_steps(steps: list[pipeline.StepResult]) -> None:
    if not steps:
        return
    table = Table(title=f"Pipeline steps ({len(steps)})")
    table.add_column("video id", style="cyan", no_wrap=True)
    table.add_column("title", max_width=40)
    table.add_column("step")
    table.add_column("result", max_width=70)
    for r in steps:
        mark = "[green]ok[/green]" if r.ok else "[red]failed[/red]"
        table.add_row(r.video_id, r.title, r.step, f"{mark} {r.message}")
    console.print(table)


@app.command("import")
def import_cmd(
    snapshot: Annotated[Path, typer.Argument(help="JSON file exported by the TLDW extension")],
    config: ConfigOpt = Path("config.yaml"),
) -> None:
    """Load a Watch Later snapshot file (the extension's 'Download JSON' button)."""
    cfg = _load(config)
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        with State(cfg.state_db) as state:
            result = intake.ingest_snapshot(state, data)
    except (OSError, json.JSONDecodeError, intake.SnapshotError) as e:
        err_console.print(f"[red]import failed:[/red] {e}")
        raise typer.Exit(2) from e
    console.print(f"Imported {result.total} videos ({result.new} new, {result.updated} updated)")


@app.command()
def serve(
    config: ConfigOpt = Path("config.yaml"),
    port: Annotated[int | None, typer.Option(help="Override serve_port")] = None,
) -> None:
    """Receive Watch Later snapshots from the browser extension (127.0.0.1 only)."""
    cfg = _load(config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        srv = make_server(
            cfg.state_db, cfg.secrets.tldw_extension_token or "", port or cfg.config.serve_port
        )
    except (ValueError, OSError) as e:
        err_console.print(f"[red]serve error:[/red] {e}")
        raise typer.Exit(2) from e
    host, bound = srv.server_address[:2]
    console.print(f"Listening on http://{host}:{bound} — Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


@app.command("sample-slide")
def sample_slide(
    config: ConfigOpt = Path("config.yaml"),
    lang: Annotated[str, typer.Option(help="en or ko sample text")] = "en",
) -> None:
    """Render one sample point slide (draft style) to out/ for style approval."""
    cfg = _load(config)
    samples = {
        "en": dict(
            claim="Reading memory in order is far faster than chasing pointers.",
            detail="CPUs fetch 64-byte cache lines, so the next values come along for free.",
            topic="Systems design",
            channel="Core Dump",
        ),
        "ko": dict(
            claim="메모리를 순서대로 읽으면 포인터를 따라가는 것보다 훨씬 빠르다.",
            detail="CPU는 64바이트 캐시 라인 단위로 읽어서 다음 값이 함께 따라온다.",
            topic="시스템 설계",
            channel="코어 덤프",
        ),
    }
    if lang not in samples:
        err_console.print(f"[red]unknown --lang {lang}[/red]; use en or ko")
        raise typer.Exit(2)
    suffix = "" if lang == "en" else f"_{lang}"
    out = render.render_point_slide(
        cfg.out_dir / f"sample_slide{suffix}.png",
        index=2,
        total=4,
        timestamp="4:10",
        **samples[lang],
    )
    console.print(f"Wrote {out}")


if __name__ == "__main__":
    app()
