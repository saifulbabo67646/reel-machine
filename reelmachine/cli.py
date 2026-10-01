"""`reel` command line.

    reel doctor                 # environment / config / API key check
    reel doctor --selftest      # end-to-end synthetic run (no API key, no server)
    reel search 彼女             # what the corpus has, with timestamps
    reel plan   彼女             # search + select + align -> plan.json (quota only)
    reel build  彼女             # plan + cut + subtitle + render -> out/*.mp4
    reel align  --media <id> --ep 3   # debug one episode's timestamp mapping
    reel quota                  # monthly API usage
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, ffmpeg
from .align import align_episode, save_timeline
from .config import get_settings
from .core.errors import QuotaExceededError, ReelError
from .core.job import Job, JobRequest, JobState
from .core.registry import Registry
from .core.text import slugify
from .engine import Engine
from .engine.providers import provider_health
from .nadeshiko import NadeshikoClient, NadeshikoError, QuotaExceeded
from .recipes import nadeshiko_cut as reelmod
from .recipes.nadeshiko_cut import NadeshikoCutInputs, summarise
from .sources import SourceError, UnresolvedEpisode, get_provider

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turn Nadeshiko dialogue search results into subtitled video reels cut from your own library.",
)
console = Console()
err = Console(stderr=True)


def _client(cache: bool = True) -> NadeshikoClient:
    settings = get_settings()
    return NadeshikoClient(settings=settings, cache=cache)


def _fmt_ms(ms: int) -> str:
    seconds, millis = divmod(max(0, int(ms)), 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:d}:{seconds:02d}.{millis // 100:1d}"


class ConsoleJobProgress:
    """Prints a job's stage changes and detail lines once each, for a human."""

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self._stage: tuple[str, int, int] | None = None
        self._detail = ""

    def __call__(self, job: Job) -> None:
        if not self.verbose:
            return
        progress = job.progress
        key = (progress.stage, progress.stage_index, progress.stages_total)
        if key != self._stage and progress.stage:
            self._stage = key
            console.print(
                f"[dim]stage {progress.stage_index}/{progress.stages_total}: {progress.stage}[/dim]"
            )
        if progress.detail and progress.detail != self._detail:
            self._detail = progress.detail
            console.print(progress.detail)


def _job_failure(finished: Job) -> None:
    """Print a failed job's structured error and exit with the right code."""
    error = finished.error
    if error is not None and error.code == "QUOTA_EXCEEDED":
        err.print(f"[red]quota exhausted[/red] {error.message}")
        sys.exit(2)
    message = error.message if error is not None else "the job failed"
    hint = f"\n{error.hint}" if error is not None and error.hint else ""
    err.print(f"[red]{message}[/red]{hint}")
    sys.exit(1)


def _job_plan(finished: Job) -> dict | None:
    """The plan artifact of a job, if it got as far as composing one."""
    for artifact in (finished.result.artifacts if finished.result else []):
        if artifact.name == "plan.json" and Path(artifact.path).is_file():
            return json.loads(Path(artifact.path).read_text(encoding="utf-8"))
    return None


def _run_job(engine: Engine, inputs: NadeshikoCutInputs, *, verbose: bool) -> Job:
    try:
        job = engine.submit(
            JobRequest(recipe="nadeshiko-cut", inputs=inputs.model_dump(mode="json")),
            caller="local",
        )
    except QuotaExceededError as exc:
        err.print(f"[red]quota exhausted[/red] {exc.message}")
        sys.exit(2)
    except ReelError as exc:
        err.print(f"[red]{exc.message}[/red]" + (f"\n{exc.hint}" if exc.hint else ""))
        sys.exit(1)
    return engine.wait(job.id, caller="local", on_progress=ConsoleJobProgress(verbose))


def _corpus_param(raw: str | None) -> str | None:
    """CLI `--category anime,jdrama` → the recipe's one corpus string."""
    categories = _parse_categories(raw)
    if not categories:
        return None
    return "+".join(category.lower() for category in categories)


def _only_list(raw: str | None) -> list[str]:
    pairs = _parse_only(raw)
    return [f"{media_id}:{episode}" for media_id, episode in (pairs or [])]


# ------------------------------------------------------------------------ doctor


@app.command()
def doctor(
    selftest: bool = typer.Option(False, "--selftest", help="Build a synthetic episode and run the full pipeline on it."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Check ffmpeg, config, API key and quota."""
    settings = get_settings()
    console.print(Panel.fit(f"reel-machine {__version__}", style="bold"))

    table = Table(show_header=False, box=None)
    table.add_row("workdir", str(settings.workdir))
    table.add_row("outdir", str(settings.outdir))
    table.add_row("source", settings.source)
    table.add_row("aspect", settings.aspect)
    table.add_row("cache", str(settings.cache_dir))
    console.print(table)

    problems = settings.missing()
    if not problems:
        console.print("[green]configuration looks complete[/green]")
    else:
        for problem in problems:
            console.print(f"[yellow]warning[/yellow] {problem}")

    ffmpeg_path = shutil.which(settings.ffmpeg)
    console.print("ffmpeg: {0}".format(ffmpeg_path or "[red]not found[/red]"))
    if not ffmpeg_path:
        err.print("[red]ffmpeg is required; install it and re-run[/red]")
        sys.exit(1)

    for note in settings.warnings():
        console.print(f"[dim]note[/dim] {note}")

    if settings.api_key:
        with _client() as client:
            state = client.quota()
            console.print(
                f"quota {state.month}: {state.requests}/{state.limit} requests used "
                f"({state.remaining} left, this run's ledger)"
            )
    else:
        console.print("[yellow]no API key: search/plan/build will not run (selftest still will)[/yellow]")

    console.print()
    health = Table(header_style="bold", title="providers")
    health.add_column("group")
    health.add_column("provider")
    health.add_column("state")
    health.add_column("detail")
    for row in provider_health(Registry(), settings):
        colour = {"ok": "green", "missing": "yellow", "error": "red", "disabled": "dim"}[row["status"]]
        health.add_row(
            row["group"],
            row["name"],
            f"[{colour}]{row['status']}[/{colour}]",
            str(row["detail"])[:64],
        )
    console.print(health)

    if selftest:
        console.print()
        console.print("[bold]selftest[/bold] — synthetic episode, known offset, no API key required")
        _run_selftest(verbose=verbose)


def _run_selftest(*, verbose: bool) -> None:
    from .sources.mock import MockProvider

    settings = get_settings()
    provider = MockProvider(settings)
    media_id = "MOCKMEDIA001"
    episode = 1

    console.print("  building synthetic episode ...")
    asset = provider.resolve(None, episode)
    info = provider.probe(asset)
    console.print(f"  episode: {asset.url} ({info.duration_s:.1f}s, {info.width}x{info.height})")

    segments = provider.make_segments(media_id, episode, count=3)
    clips = provider.reference_clips(segments, client=None, asset=asset)
    console.print(f"  fabricated {len(segments)} segments, {len(clips)} reference clips")

    timeline = align_episode(
        asset.url,
        segments,
        clip_paths=clips,
        max_anchors=3,
        episode_duration_ms=asset.duration_ms,
        verbose=verbose,
    )
    expected = provider.expected_offset_ms()
    console.print(f"  alignment: {timeline.describe()}")
    console.print(f"  expected offset: {expected:+d} ms   measured: {timeline.offset_ms:+d} ms")

    ok = timeline.ok and abs(timeline.offset_ms - expected) <= 250
    if not ok:
        err.print("[red]selftest FAILED[/red] — alignment did not recover the injected offset")
        sys.exit(1)
    console.print("[green]selftest passed[/green] — alignment recovers the injected offset")

    plan = reelmod.Plan(word="彼女", source="mock")
    from .recipes.nadeshiko_cut import PlannedSegment

    for segment in segments:
        plan.items.append(
            PlannedSegment(
                segment=segment,
                media=None,
                asset=asset,
                timeline=timeline,
                local_start_ms=timeline.to_local_ms(segment.startTimeMs),
                local_end_ms=timeline.to_local_ms(segment.endTimeMs),
                status="ok",
                note="selftest",
            )
        )
    result = reelmod.render(plan, outdir=settings.outdir / "selftest", name="selftest", verbose=verbose)
    console.print(f"  reel: [bold]{result.video}[/bold] ({result.duration_ms / 1000:.1f}s)")
    console.print(f"  subs: {result.ass.name}, {result.srt.name}")

    # Guard the invariants that are easy to break and hard to notice: the reel
    # must be as long as the cues say, and its audio must match its video.
    info = ffmpeg.probe(result.video)
    expected_s = result.duration_ms / 1000.0
    drift_s = abs(info.duration_s - expected_s)
    console.print(
        f"  duration: container {info.duration_s:.3f}s vs cues {expected_s:.3f}s "
        f"(drift {drift_s * 1000:+.0f} ms)"
    )
    if drift_s > 0.20:
        err.print(f"[red]selftest FAILED[/red] — reel is {drift_s * 1000:.0f} ms off the cue timeline")
        sys.exit(1)

    deltas = []
    for stream, label in (("0:v:0", "video"), ("0:a:0", "audio")):
        proc = ffmpeg.run(
            [settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(result.video),
             "-map", stream, "-f", "null", "-"]
        )
        match = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
        deltas.append((label, float(match[-1][2]) if match else 0.0))
    (_, video_s), (_, audio_s) = deltas
    console.print(f"  streams:  video {video_s:.3f}s · audio {audio_s:.3f}s")
    if abs(video_s - audio_s) > 0.25:
        err.print(
            f"[red]selftest FAILED[/red] — audio and video differ by "
            f"{abs(video_s - audio_s) * 1000:.0f} ms; the concat will desync"
        )
        sys.exit(1)

    console.print("[green]selftest passed[/green] — alignment, cut, subtitle and mux all verified")

    _run_selftest_non_media(settings)


def _run_selftest_non_media(settings) -> None:
    """One recipe that touches no source media, end to end: quranic with the fake corpus."""
    console.print()
    console.print("[bold]selftest[/bold] — a non-media recipe: quranic, fake corpus, no source file")
    engine = Engine(settings, provider_overrides={"corpus": "quran-fake"})
    try:
        job = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 2,
                    "corpus": "quran-fake",
                    "name": "selftest-quranic",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()
    if finished.state is not JobState.SUCCEEDED:
        err.print(f"[red]selftest FAILED[/red] — quranic: {finished.error}")
        sys.exit(1)
    reel = next(a for a in finished.result.artifacts if a.name == "reel.mp4")
    info = ffmpeg.probe(reel.path)
    if not (info.has_video and info.has_audio):
        err.print("[red]selftest FAILED[/red] — the quranic reel has no video or audio stream")
        sys.exit(1)
    console.print(f"  quranic: {reel.path} ({info.duration_s:.1f}s · video+audio)")
    console.print("[green]selftest passed[/green] — one non-media recipe rendered end to end")


# ------------------------------------------------------------------------ search


@app.command()
def search(
    word: str = typer.Argument(..., help="Japanese word, romaji, or English."),
    take: int = typer.Option(10, "--take", "-n", help="How many segments to show."),
    exact: bool = typer.Option(False, "--exact", help="Exact phrase match."),
    rating: Optional[str] = typer.Option(None, "--rating", help="Comma list: SAFE,SUGGESTIVE,QUESTIONABLE,EXPLICIT"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Search the corpus and show segments with their timestamps."""
    ratings = [r.strip().upper() for r in rating.split(",")] if rating else None
    with _client() as client:
        try:
            page = client.search(word, take=take, exact_match=exact, content_rating=ratings)
        except QuotaExceeded as exc:
            err.print(f"[red]quota exhausted[/red] {exc}")
            sys.exit(2)
        except NadeshikoError as exc:
            err.print(f"[red]{exc}[/red]")
            sys.exit(2)

    if json_out:
        console.print_json(
            json.dumps([s.model_dump() for s in page.segments], ensure_ascii=False)
        )
        return

    console.print(
        f"[bold]{len(page.segments)}[/bold] segments · "
        f"~{page.pagination.estimatedTotalHits} total "
        f"({page.pagination.estimatedTotalHitsRelation or 'EXACT'})"
    )
    table = Table(show_lines=False, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("title")
    table.add_column("ep", justify="right")
    table.add_column("time", justify="right")
    table.add_column("ja")
    table.add_column("en")
    for index, segment in enumerate(page.segments, start=1):
        media = page.media.get(segment.mediaPublicId)
        title = (media.nameEn or media.nameRomaji) if media else segment.mediaPublicId
        table.add_row(
            str(index),
            (title or "")[:28],
            str(segment.episode),
            _fmt_ms(segment.startTimeMs),
            segment.japanese[:34],
            segment.english[:34],
        )
    console.print(table)


@app.command()
def words(
    terms: list[str] = typer.Argument(..., help="Words to triage (max 100)."),
) -> None:
    """Check which words exist in the corpus before spending renders on them."""
    with _client() as client:
        results = client.search_words(list(terms))
    table = Table(header_style="bold")
    table.add_column("word")
    table.add_column("found", justify="center")
    table.add_column("matches", justify="right")
    table.add_column("titles", justify="right")
    for match in results:
        table.add_row(
            match.word,
            "[green]yes[/green]" if match.isMatch else "[red]no[/red]",
            str(match.matchCount),
            str(len(match.media)),
        )
    console.print(table)


@app.command()
def mine(
    episodes: list[str] = typer.Argument(
        ..., help="Episodes you already have: <mediaPublicId>:<ep> ..."
    ),
    level: str = typer.Option(
        "", "--level", help="Only these JLPT levels, e.g. N5,N4. Default: all."
    ),
    limit: int = typer.Option(25, "--limit", "-n", help="How many words to show."),
    category: Optional[str] = typer.Option(
        None, "--category", help="Which corpora to search: anime, jdrama, or both."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find JLPT words inside episodes you already downloaded.

    One reel costs a download; this shows every *other* reel the same file can
    make, so the download is spent once.
    """
    from .recipes.nadeshiko_cut import mine_episodes

    targets = _parse_only(",".join(episodes))
    if not targets:
        err.print("[red]no episodes given; expected <mediaPublicId>:<ep>[/red]")
        raise typer.Exit(1)

    levels: list[int] | None = None
    if level.strip():
        level_map = {f"N{n}": n for n in (5, 4, 3, 2, 1)}
        levels = []
        for chunk in re.split(r"[,\s]+", level.strip()):
            key = chunk.upper()
            if key not in level_map:
                err.print(f"[red]unknown level {chunk!r}; expected N5..N1[/red]")
                raise typer.Exit(1)
            levels.append(level_map[key])

    settings = get_settings()
    with _client() as client:
        results = mine_episodes(
            client, targets, settings=settings, levels=levels, limit=limit,
            categories=_parse_categories(category), verbose=verbose,
        )
    if not results:
        err.print("no graded vocabulary found in those episodes")
        raise typer.Exit(1)

    table = Table(header_style="bold")
    table.add_column("word")
    table.add_column("level")
    table.add_column("reading")
    table.add_column("romaji")
    table.add_column("meaning")
    table.add_column("said", justify="right")
    for entry, count in results:
        table.add_row(
            entry.word,
            entry.level_label or "-",
            entry.kana,
            entry.romaji,
            (entry.meaning or "")[:38],
            str(count),
        )
    console.print(table)

    pinned = ",".join(f"{pid}:{ep}" for pid, ep in targets)
    console.print(
        f"\n[next] build any of these from the same files:\n"
        f"  uv run reel build <word> --only {pinned} --per-media 3"
    )


@app.command()
def titles(
    query: str = typer.Argument(..., help="Title name to look up."),
    take: int = typer.Option(10, "--take", "-n"),
) -> None:
    """Find media ids by name (the join key for your library)."""
    with _client() as client:
        results = client.search_media(query, take=take)
    table = Table(header_style="bold")
    table.add_column("publicId")
    table.add_column("en")
    table.add_column("romaji")
    table.add_column("slug")
    for item in results:
        table.add_row(item.publicId, item.nameEn or "", item.nameRomaji or "", item.slug)
    console.print(table)


# -------------------------------------------------------------------------- plan



def _parse_only(raw: str | None) -> list[tuple[str, int]] | None:
    """`<mediaPublicId>:<episode>,…` -> [("V1StGXR8_Z5d", 3), …]"""
    if not raw:
        return None
    pairs: list[tuple[str, int]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        media_id, _, episode = chunk.partition(":")
        if not media_id or not episode.strip().isdigit():
            err.print(f"[red]bad --only entry {chunk!r}; expected <mediaPublicId>:<episode>[/red]")
            sys.exit(2)
        pairs.append((media_id.strip(), int(episode)))
    return pairs or None


def _parse_categories(raw: str | None) -> list[str] | None:
    """`"anime,jdrama"` -> `["ANIME", "JDRAMA"]`; None means "use the setting"."""
    if not raw or not raw.strip():
        return None
    aliases = {"anime": "ANIME", "jdrama": "JDRAMA", "drama": "JDRAMA",
               "youtube": "YOUTUBE"}
    out: list[str] = []
    for chunk in re.split(r"[,\s]+", raw.strip()):
        if not chunk:
            continue
        key = aliases.get(chunk.lower(), chunk.upper())
        if key not in {"ANIME", "JDRAMA", "YOUTUBE"}:
            err.print(f"[red]unknown category {chunk!r}; expected anime, jdrama or both[/red]")
            sys.exit(2)
        if key not in out:
            out.append(key)
    return out or None


@app.command()
def plan(
    word: str = typer.Argument(...),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Where to write plan.json."),
    count: Optional[int] = typer.Option(None, "--count", "-c", help="Max segments in the reel."),
    per_media: int = typer.Option(1, "--per-media", help="Max segments taken from one title."),
    rating: Optional[str] = typer.Option(None, "--rating"),
    category: Optional[str] = typer.Option(
        None, "--category",
        help="Which corpora to search: anime, jdrama, or both. Default: both.",
    ),
    per_category: Optional[int] = typer.Option(
        None, "--per-category",
        help="Max clips from any one corpus — use it to make a mixed reel actually mix.",
    ),
    exact: bool = typer.Option(False, "--exact"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Search and select only, do not touch the video source."),
    only: Optional[str] = typer.Option(
        None, "--only", help="Pin to episodes you have: <mediaPublicId>:<ep>,<mediaPublicId>:<ep>"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Search, select and align — spends API quota, downloads nothing heavy."""
    settings = get_settings()
    ratings = [r.strip().upper() for r in rating.split(",")] if rating else None
    inputs = NadeshikoCutInputs(
        word=word,
        mode="plan",
        corpus=_corpus_param(category),
        count=count,
        per_media=per_media,
        per_category=per_category,
        content_rating=ratings,
        exact_match=exact,
        only=_only_list(only),
        dry_run=dry_run,
    )
    engine = Engine(settings)
    try:
        finished = _run_job(engine, inputs, verbose=verbose)
        plan = _job_plan(finished)
    finally:
        engine.close()
    if finished.state is not JobState.SUCCEEDED:
        if plan is not None:
            console.print(summarise(plan))
        _job_failure(finished)

    assert plan is not None
    target = out or (settings.workdir / f"plan-{slugify(word)}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(summarise(plan))
    console.print(f"\nplan: [bold]{target}[/bold]")


@app.command()
def build(
    word: str = typer.Argument(...),
    count: Optional[int] = typer.Option(None, "--count", "-c"),
    per_media: int = typer.Option(1, "--per-media"),
    rating: Optional[str] = typer.Option(None, "--rating"),
    category: Optional[str] = typer.Option(
        None, "--category",
        help="Which corpora to search: anime, jdrama, or both. Default: both.",
    ),
    per_category: Optional[int] = typer.Option(
        None, "--per-category",
        help="Max clips from any one corpus — use it to make a mixed reel actually mix.",
    ),
    exact: bool = typer.Option(False, "--exact"),
    aspect: Optional[str] = typer.Option(None, "--aspect", help="vertical | square | original"),
    pre: Optional[int] = typer.Option(None, "--pre", help="Pre-roll ms."),
    post: Optional[int] = typer.Option(None, "--post", help="Post-roll ms."),
    name: Optional[str] = typer.Option(None, "--name", help="Output file stem."),
    watermark: str = typer.Option("", "--watermark", help="Corner text for the whole reel."),
    only: Optional[str] = typer.Option(
        None, "--only", help="Pin to episodes you have: <mediaPublicId>:<ep>,<mediaPublicId>:<ep>"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Plan, cut, subtitle and render a reel."""
    settings = get_settings()
    ratings = [r.strip().upper() for r in rating.split(",")] if rating else None
    inputs = NadeshikoCutInputs(
        word=word,
        mode="build",
        corpus=_corpus_param(category),
        count=count,
        per_media=per_media,
        per_category=per_category,
        content_rating=ratings,
        exact_match=exact,
        only=_only_list(only),
        aspect=aspect,
        pre_roll_ms=pre,
        post_roll_ms=post,
        watermark=watermark,
        name=name,
        outdir=str(settings.outdir),
    )

    engine = Engine(settings)
    try:
        finished = _run_job(engine, inputs, verbose=verbose)
        plan = _job_plan(finished)
    finally:
        engine.close()

    if finished.state is not JobState.SUCCEEDED:
        if plan is not None:
            console.print(summarise(plan))
            if not (plan.get("stats") or {}).get("ok"):
                err.print("[red]nothing aligned; not rendering[/red]")
                sys.exit(1)
        _job_failure(finished)

    assert plan is not None
    console.print(summarise(plan))
    artifacts = {artifact.name: artifact for artifact in (finished.result.artifacts if finished.result else [])}
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8")) if finished.result else {}
    duration_ms = int((manifest.get("timeline") or {}).get("duration_ms") or 0)
    console.print()
    console.print(f"[green]reel[/green]  {artifacts['reel.mp4'].path}  ({duration_ms / 1000:.1f}s)")
    console.print(f"[green]subs[/green]  {artifacts['captions.ass'].path}")
    console.print(f"[green]srt [/green]  {artifacts['captions.srt'].path}")
    console.print(f"[green]meta[/green]  {artifacts['reel.json'].path}")


mcp_app = typer.Typer(add_completion=False, help="Expose the engine to agents over MCP.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio | streamable-http"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    tenants: Optional[Path] = typer.Option(
        None, "--tenants", help="JSON file of caller tokens and policies."
    ),
) -> None:
    """Serve the engine over MCP — stdio for a local agent, HTTP for a hosted one."""
    from .mcp.server import serve as serve_mcp

    serve_mcp(
        transport=transport,
        host=host,
        port=port,
        tenants_path=str(tenants) if tenants else None,
    )


@mcp_app.command("token-hash")
def mcp_token_hash(
    token: str = typer.Argument(..., help="Token to hash; use '-' to read it from stdin."),
) -> None:
    """Hash a caller token for a tenants file — servers never store the raw token."""
    from .mcp.tenants import TenantRegistry

    value = sys.stdin.read().strip() if token == "-" else token
    if not value:
        err.print("[red]empty token[/red]")
        raise typer.Exit(2)
    console.print(TenantRegistry.hash_token(value))


@app.command()
def recipes(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List what this installation can make."""
    engine = Engine(get_settings())
    try:
        rows = engine.list_recipes()
    finally:
        engine.close()
    if json_out:
        console.print_json(json.dumps(rows, ensure_ascii=False))
        return
    table = Table(header_style="bold")
    table.add_column("recipe")
    table.add_column("title")
    table.add_column("render modes")
    table.add_column("cost / unit")
    for row in rows:
        table.add_row(
            row["id"],
            row["title"],
            ", ".join(row["renderModes"]) or "-",
            row["cost"]["unit"] or "-",
        )
    console.print(table)


# ------------------------------------------------------------------------- debug


@app.command()
def align(
    media: Optional[str] = typer.Option(None, "--media", help="Nadeshiko mediaPublicId."),
    ep: int = typer.Option(1, "--ep", help="Episode number."),
    url: Optional[str] = typer.Option(None, "--url", help="Direct m3u8/mp4 URL to test instead of a provider."),
    word: Optional[str] = typer.Option(None, "--word", help="Use this word's segments as anchors."),
    segment: Optional[str] = typer.Option(None, "--segment", help="Use a single segment id as the anchor."),
    season: int = typer.Option(1, "--season"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", "-v/-q"),
) -> None:
    """Measure the timestamp mapping for one episode and print the drift."""
    settings = get_settings()
    # `--url` is by definition an HLS/direct stream, whatever REEL_SOURCE says.
    provider = get_provider("hls" if url else None, settings)

    if not (url or media or word or segment):
        err.print("[red]give one of --media, --url, --word or --segment[/red]")
        sys.exit(2)

    with _client() as client:
        model = None
        segments = []
        if media:
            try:
                model = client.get_media(media)
            except NadeshikoError as exc:
                err.print(f"[yellow]could not load media {media}: {exc}[/yellow]")
                model = None
        if segment:
            segments = [client.get_segment(segment)]
        elif word:
            page = client.search(word, take=10, include_media=True)
            if model is None and page.media:
                model = next(iter(page.media.values()))
            segments = [s for s in page.segments if s.episode == ep] or page.segments[:1]
            if segments:
                ep = segments[0].episode

        if url:
            asset = provider.resolve(None, ep, url=url, season=season)  # type: ignore[call-arg]
        else:
            try:
                asset = provider.resolve(model, ep, season=season)
            except SourceError as exc:
                err.print(f"[red]{exc}[/red]")
                sys.exit(1)

        info = provider.probe(asset)
        console.print(f"source: {asset.url}")
        console.print(f"        {info.duration_s:.1f}s · {info.width}x{info.height} · {info.vcodec}/{info.acodec}")

        if not segments:
            console.print("[yellow]no segments to anchor with; pass --word or --segment[/yellow]")
            sys.exit(0)

        clips = provider.reference_clips(segments, client=client, asset=asset)
        console.print(f"reference clips: {len(clips)}/{len(segments)}")
        if not clips:
            err.print("[red]no reference audio could be downloaded — cannot measure the offset[/red]")
            sys.exit(1)

        timeline = align_episode(
            asset.url,
            segments,
            clip_paths=clips,
            headers=asset.headers or None,
            max_anchors=len(segments),
            episode_duration_ms=asset.duration_ms,
            verbose=verbose,
        )

    console.print()
    table = Table(header_style="bold")
    table.add_column("segment")
    table.add_column("src ms", justify="right")
    table.add_column("local ms", justify="right")
    table.add_column("predicted", justify="right")
    table.add_column("residual", justify="right")
    table.add_column("ncc", justify="right")
    for anchor in timeline.anchors:
        table.add_row(
            anchor.segment_id,
            str(anchor.src_ms),
            str(anchor.found_ms),
            str(anchor.expected_ms or ""),
            f"{anchor.residual_ms:+.0f}",
            f"{anchor.score:.3f}",
        )
    console.print(table)
    status = "[green]aligned[/green]" if timeline.ok else "[red]NOT aligned[/red]"
    console.print(f"{status} — {timeline.describe()}")
    if timeline.ok:
        console.print(
            f"mapping: local_ms = {timeline.a:.6f} * src_ms {timeline.b:+.0f}  "
            f"(constant offset {timeline.offset_ms:+d} ms)"
        )
    out = settings.workdir / "timelines" / f"{slugify(str(media or url or 'adhoc'))}-ep{ep}.json"
    save_timeline(out, timeline, meta={"asset": str(asset.url), "episode": ep})
    console.print(f"timeline: {out}")
    if not timeline.ok:
        sys.exit(1)


@app.command()
def probe(
    media: Optional[str] = typer.Option(None, "--media", help="Nadeshiko mediaPublicId."),
    ep: int = typer.Option(1, "--ep", help="Episode number."),
    url: Optional[str] = typer.Option(None, "--url", help="Test this exact URL instead."),
    season: int = typer.Option(1, "--season"),
    title: Optional[str] = typer.Option(None, "--title", help="Look the title up by name first."),
    inspect: bool = typer.Option(
        True, "--inspect/--no-inspect", help="Fetch the playlist and list renditions."
    ),
) -> None:
    """Resolve one episode's stream and report it — no media downloaded.

    Checks the parts that break first: the URL template, the site's opaque ids,
    whether the signed token is still valid, and which rendition would be cut.
    """
    settings = get_settings()
    provider = get_provider("hls" if url else None, settings)

    model = None
    if title:
        with _client() as client:
            matches = client.search_media(title, take=1)
        if not matches:
            err.print(f"[red]no title matched {title!r}[/red]")
            sys.exit(1)
        summary = matches[0]
        console.print(f"title: {summary.nameEn or summary.nameRomaji}  [dim]{summary.publicId}[/dim]")
        media = summary.publicId
    if media and not url:
        with _client() as client:
            try:
                model = client.get_media(media)
            except NadeshikoError as exc:
                err.print(f"[yellow]could not load media {media}: {exc}[/yellow]")

    # Providers that can describe an episode without fetching it do so, so that
    # `probe` never triggers a multi-hundred-megabyte download.
    if not url and hasattr(provider, "check"):
        try:
            report = provider.check(model, ep, season=season)
        except (SourceError, UnresolvedEpisode) as exc:
            err.print(f"[red]{exc}[/red]")
            sys.exit(1)
        table = Table(show_header=False, box=None)
        for key in ("label", "tmdb", "mediaType", "season", "matchReason", "matchScore",
                    "selected", "wouldDownloadTo", "headers", "warning"):
            if report.get(key) not in (None, "", []):
                table.add_row(key, str(report[key]))
        console.print(table)
        streams = report.get("streams") or []
        if streams:
            grid = Table(header_style="bold")
            grid.add_column("res", justify="right")
            grid.add_column("size", justify="right")
            grid.add_column("audio")
            grid.add_column("link age", justify="right")
            grid.add_column("state")
            for stream in streams:
                age = stream.get("ageS")
                language = stream.get("language") or "?"
                grid.add_row(
                    f"{stream.get('resolution')}p",
                    f"{(stream.get('size') or 0) / 1e6:.0f} MB",
                    # The alignment needs the original Japanese audio, so a dub
                    # is worth flagging before a multi-hundred-MB download.
                    f"[green]{language}[/green]" if language.lower().startswith("jap") or language.lower() in {"ja", "jpn"}
                    else f"[yellow]{language}[/yellow]",
                    f"{age / 60:.0f} min" if age is not None else "?",
                    "[red]vip-locked[/red]" if stream.get("vip") else "[green]free[/green]",
                )
            console.print(grid)
        size = report.get("sizeBytes")
        if size:
            console.print(f"[green]ok[/green] {report.get('selected')} · {size / 1e6:.0f} MB to download")
        return

    try:
        asset = provider.resolve(model, ep, season=season, url=url, inspect=inspect)
    except (SourceError, UnresolvedEpisode) as exc:
        err.print(f"[red]{exc}[/red]")
        sys.exit(1)

    table = Table(show_header=False, box=None)
    table.add_row("provider", provider.name)
    table.add_row("episode", str(ep))
    table.add_row("master", str(asset.meta.get("url", asset.url)))
    if asset.meta.get("variants"):
        table.add_row("renditions", ", ".join(asset.meta["variants"]))
        table.add_row("selected", str(asset.meta.get("chosen", "")))
    table.add_row("playable", asset.url)
    table.add_row("headers", ", ".join(sorted(asset.headers)) or "(none)")
    console.print(table)

    if not inspect:
        console.print("[dim]--no-inspect: playlist not fetched, rendition not verified[/dim]")
        return

    try:
        info = provider.probe(asset)
    except ffmpeg.FFmpegError as exc:
        err.print(f"[red]ffmpeg could not open the stream:[/red]\n{exc}")
        sys.exit(1)
    console.print(
        f"[green]ok[/green] {info.duration_s:.1f}s · {info.width}x{info.height} · "
        f"{info.vcodec}/{info.acodec} · {info.audio_tracks} audio track(s)"
    )


@app.command()
def quota() -> None:
    """Show this month's API request count."""
    settings = get_settings()
    with _client(cache=False) as client:
        state = client.quota()
    console.print(
        f"{state.month}: [bold]{state.requests}[/bold]/{state.limit} requests "
        f"({state.remaining} remaining, counted by this tool and cached responses excluded)"
    )


@app.command()
def cache(
    clear: bool = typer.Option(False, "--clear", help="Delete cached responses and envelopes."),
) -> None:
    """Inspect or clear the local cache."""
    settings = get_settings()
    if clear:
        import shutil

        removed = 0
        for path in settings.cache_dir.rglob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
        console.print(f"removed {removed} cached file(s)")
        return
    total = sum(1 for p in settings.cache_dir.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in settings.cache_dir.rglob("*") if p.is_file())
    console.print(f"{total} cached file(s), {size / 1e6:.1f} MB in {settings.cache_dir}")
    console.print("re-running a plan/build with identical requests costs no API quota")


def main() -> None:
    from .observability import configure_logging

    configure_logging()
    try:
        app()
    except KeyboardInterrupt:
        err.print("\ninterrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
