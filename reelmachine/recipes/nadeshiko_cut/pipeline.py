"""The nadeshiko-cut pipeline as step functions.

The recipe's stages orchestrate these steps; the functional API (`build_plan`,
`render`) that the test suite and the demo script use is a thin composition of the same
steps, so there is exactly one implementation of the behaviour.

Behaviour is preserved from the previous single-pipeline module: only the seams changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ... import ffmpeg, subtitles
from ...align import align_episode
from ...config import Settings, get_settings, normalise_aspect
from ...core.text import slugify
from ...matching import is_word_match, matched_forms
from ...models import Media, Segment
from ...nadeshiko import NadeshikoClient, NadeshikoError
from ...sources import SourceError, SourceProvider, get_provider
from .models import (
    AssetHandle,
    EpisodeRecord,
    ItemRecord,
    Plan,
)
from .selection import _category_of, compute_cut_window, compute_scene_window, select_segments

Detail = Callable[[str], None]


def _noop(_: str) -> None:
    return None


# ---------------------------------------------------------------- 1. search + select


def search_and_select(
    client: NadeshikoClient,
    word: str,
    *,
    settings: Settings,
    categories: Sequence[str] | None = None,
    per_category: int | None = None,
    content_rating: list[str] | None = None,
    exact_match: bool = False,
    max_segments: int = 12,
    min_segments: int = 1,
    per_media: int = 1,
    max_candidates: int = 150,
    max_pages: int = 5,
    min_ms: int = 600,
    max_ms: int = 30000,
    require_english: bool = True,
    only: Sequence[tuple[str, int]] | None = None,
    on_detail: Detail | None = None,
) -> tuple[list[ItemRecord], dict[str, Any]]:
    """Search the corpus, filter, select, and widen each pick to its scene."""
    detail = on_detail or _noop
    ratings = content_rating if content_rating is not None else settings.content_rating

    # `--only` pins the reels to episodes you actually have, using the API's own
    # media filter rather than filtering client-side after spending the request.
    filters: dict[str, Any] = {}
    wanted = [c.upper() for c in (categories if categories is not None else settings.categories)]
    if wanted:
        # Nadeshiko indexes anime and J-Drama together; omitting this returns
        # both, which is why drama never showed up unless asked for by name.
        filters["category"] = wanted
    if only:
        filters["media"] = {
            "include": [
                {"mediaPublicId": pid, "episodes": [ep]} for pid, ep in only
            ]
        }

    candidates: list[Segment] = []
    media_by_id: dict[str, Media] = {}
    cursor: str | None = None
    for _ in range(max(1, max_pages)):
        page = client.search(
            word,
            take=50,
            cursor=cursor,
            exact_match=exact_match,
            content_rating=ratings or None,
            include_media=True,
            filters=filters or None,
        )
        media_by_id.update(page.media)
        candidates.extend(page.segments)
        if len(candidates) >= max_candidates or not page.pagination.hasMore or not page.pagination.cursor:
            break
        cursor = page.pagination.cursor

    total_hits = len(candidates)

    usable: list[Segment] = []
    dropped_variants: list[Segment] = []
    for segment in candidates:
        if segment.status != "ACTIVE":
            continue
        if not segment.japanese:
            continue
        if require_english and not segment.english:
            continue
        if not is_word_match(segment, word, settings.match_mode):
            dropped_variants.append(segment)
            continue
        if not (min_ms <= segment.duration_ms <= max_ms):
            continue
        if ratings and segment.contentRating not in ratings:
            continue
        usable.append(segment)

    if dropped_variants:
        forms = sorted({f for s in dropped_variants for f in matched_forms(s)})
        detail(
            f"  x skipped {len(dropped_variants)} clip(s) whose audio says "
            f"{', '.join(forms) or 'a variant'} — not {word!r} "
            f"(REEL_MATCH_MODE={settings.match_mode})"
        )

    selected = select_segments(
        usable,
        media_by_id,
        settings,
        max_segments=max_segments,
        min_segments=min_segments,
        per_media=per_media,
        per_category=per_category,
    )

    # `Segment.startTimeMs` describes one line; a line can be under a second and
    # teaches nothing on its own.  `/context` supplies the neighbouring lines of
    # the same episode, with their own exact times.
    items: list[ItemRecord] = []
    context_segments = 0
    for segment in selected:
        neighbours: list[Segment] = []
        if settings.context_enabled:
            try:
                fetched = client.segment_context(
                    segment.publicId,
                    take=max(1, settings.context_take),
                    content_rating=ratings or None,
                )
                neighbours = [n for n in fetched if n.publicId != segment.publicId]
            except NadeshikoError as exc:
                detail(f"  ! context lookup failed for {segment.publicId}: {exc}")
        scene_low, scene_high = compute_scene_window(
            segment.startTimeMs,
            segment.endTimeMs,
            neighbours,
            max_ms=settings.max_clip_ms,
            min_ms=settings.min_clip_ms,
        )
        scene_ms = scene_high - scene_low
        # The scene feeds two things: the window that gets cut, and the
        # neighbouring lines used as alignment anchors.  The anchors want the
        # whole exchange; the cut wants only the line the caption describes.
        low, high = scene_low, scene_high
        if settings.cut_mode == "line":
            low, high = compute_cut_window(
                segment.startTimeMs,
                segment.endTimeMs,
                pad_ms=settings.cut_pad_ms,
                min_ms=settings.min_clip_ms,
                max_ms=settings.max_clip_ms,
            )
        context_segments += len(neighbours)
        items.append(
            ItemRecord(
                segment=segment,
                media=media_by_id.get(segment.mediaPublicId),
                category=_category_of(segment, media_by_id),
                word=word,
                scene_start_ms=low,
                scene_end_ms=high,
                context_count=len(neighbours),
                context=neighbours,
            )
        )
        if neighbours:
            detail(
                f"  ~ {segment.publicId}: {segment.duration_ms} ms of line -> "
                f"{(high - low) / 1000:.1f}s cut from a {scene_ms / 1000:.1f}s scene "
                f"with {len(neighbours)} neighbouring line(s)"
            )

    stats = {
        "totalHits": total_hits,
        "usableCandidates": len(usable),
        "selected": len(selected),
        "titles": len({s.mediaPublicId for s in selected}),
        "categories": wanted or ["ANIME", "JDRAMA"],
        "byCategory": {
            k: sum(1 for s in selected if _category_of(s, media_by_id) == k)
            for k in sorted({_category_of(s, media_by_id) for s in selected})
        },
        "contextSegments": context_segments,
        "matchMode": settings.match_mode,
        "droppedVariants": len(dropped_variants),
    }
    return items, stats


# ------------------------------------------------------------------- 2. acquisition


def group_items(items: Sequence[ItemRecord]) -> list[tuple[tuple[str, int], list[ItemRecord]]]:
    """Group by `(mediaPublicId, episode)`, preserving selection order."""
    by_episode: dict[tuple[str, int], list[ItemRecord]] = {}
    for item in items:
        by_episode.setdefault((item.segment.mediaPublicId, item.segment.episode), []).append(item)
    return list(by_episode.items())


def acquire_episodes(
    client: NadeshikoClient | None,
    provider: SourceProvider,
    groups: Sequence[tuple[tuple[str, int], list[ItemRecord]]],
    *,
    on_detail: Detail | None = None,
    cancel: Any = None,
) -> tuple[dict[str, EpisodeRecord], dict[str, str]]:
    """Resolve and probe each episode, and fetch its alignment reference clips."""
    detail = on_detail or _noop
    episodes: dict[str, EpisodeRecord] = {}
    failures: dict[str, str] = {}
    for (media_public_id, episode), group in groups:
        if cancel is not None:
            cancel.raise_if_cancelled()
        media = group[0].media
        label = (media.nameEn if media else media_public_id) or media_public_id
        key = f"{media_public_id}:{episode}"
        try:
            asset = provider.resolve(media, episode)
            provider.probe(asset)
        except (SourceError, ffmpeg.FFmpegError) as exc:
            failures[key] = f"{label} ep{episode}: {exc}"
            detail(f"  ! {label} ep{episode}: {exc}")
            continue

        # Anchor the alignment on every line of the scene, not just the word.
        # A single short clip searched across a whole episode frequently has a
        # near-tie; three clips from one exchange pin the offset between them.
        anchor_segments: list[Segment] = []
        seen_anchor_ids: set[str] = set()
        for item in group:
            for candidate in [item.segment, *item.context]:
                if candidate.publicId not in seen_anchor_ids:
                    seen_anchor_ids.add(candidate.publicId)
                    anchor_segments.append(candidate)

        clips = provider.reference_clips(anchor_segments, client=client, asset=asset)
        detail(
            f"  · {label} ep{episode}: {len(clips)} reference clip(s) for "
            f"{len(group)} segment(s) + {len(anchor_segments) - len(group)} scene line(s)"
        )
        handle = AssetHandle.from_asset(asset)
        episodes[key] = EpisodeRecord(
            media_public_id=media_public_id,
            episode=episode,
            label=label,
            asset=handle,
            source_id=handle.source_id(provider.name),
            clip_paths={sid: str(path) for sid, path in clips.items()},
        )
    return episodes, failures


# --------------------------------------------------------------------- 3. composition


def compose_items(
    *,
    groups: Sequence[tuple[tuple[str, int], list[ItemRecord]]],
    episodes: dict[str, EpisodeRecord],
    failures: dict[str, str],
    settings: Settings,
    min_confidence: float = 0.35,
    max_anchors: int = 6,
    min_anchors: int = 1,
    dry_run: bool = False,
    on_detail: Detail | None = None,
    cancel: Any = None,
) -> list[ItemRecord]:
    """The alignment strategy: map each item's source window onto the local copy.

    This is where a recipe that cuts real footage earns its timestamps; recipes with no
    source file never call this at all.
    """
    detail = on_detail or _noop
    composed: list[ItemRecord] = []
    for (media_public_id, episode), group in groups:
        if cancel is not None:
            cancel.raise_if_cancelled()
        key = f"{media_public_id}:{episode}"
        for item in group:
            if dry_run:
                item.status = "pending"
                item.note = "dry run: not resolved"
            composed.append(item)

        if dry_run:
            continue

        failure = failures.get(key)
        if failure:
            for item in group:
                item.status = "unresolved"
                item.note = failure
            continue

        record = episodes.get(key)
        if record is None:
            for item in group:
                item.status = "unresolved"
                item.note = "episode was never acquired"
            continue

        asset = record.asset.to_asset()
        anchor_segments: list[Segment] = []
        seen_anchor_ids: set[str] = set()
        for item in group:
            for candidate in [item.segment, *item.context]:
                if candidate.publicId not in seen_anchor_ids:
                    seen_anchor_ids.add(candidate.publicId)
                    anchor_segments.append(candidate)

        for item in group:
            item.asset = record.asset

        clip_paths = {sid: Path(path) for sid, path in record.clip_paths.items()}
        timeline = align_episode(
            asset.url,
            anchor_segments,
            clip_paths=clip_paths,
            headers=asset.headers or None,
            audio_track=asset.audio_track,
            max_anchors=max_anchors,
            episode_duration_ms=asset.duration_ms,
            # Three clips that disagree are worse than one that is merely weak.
            min_anchors=max(min_anchors, 2) if len(anchor_segments) >= 3 else min_anchors,
            verbose=False,
        )
        detail(f"    -> {timeline.describe()}")

        for item in group:
            item.timeline = timeline.to_dict()
            if not timeline.ok or timeline.confidence < min_confidence:
                item.status = "unaligned"
                item.note = timeline.reason or f"confidence {timeline.confidence:.2f}"
                continue
            item.local_start_ms = timeline.to_local_ms(item.scene_start_ms)
            item.local_end_ms = timeline.to_local_ms(item.scene_end_ms)
            if item.local_end_ms <= item.local_start_ms:
                item.status = "out-of-range"
                item.note = "mapped window is empty"
                continue
            if asset.duration_ms and item.local_start_ms >= asset.duration_ms:
                item.status = "out-of-range"
                item.note = (
                    f"mapped start {item.local_start_ms} ms past end of file "
                    f"({asset.duration_ms} ms)"
                )
                continue
            item.status = "ok"
            drift = timeline.to_local_ms(item.segment.startTimeMs) - item.segment.startTimeMs
            item.note = (
                f"drift {drift:+d} ms, conf {timeline.confidence:.2f}, "
                f"scene {(item.scene_end_ms - item.scene_start_ms) / 1000:.1f}s"
            )
    return composed


def plan_from_items(word: str, source: str, items: Sequence[ItemRecord], stats: dict[str, Any]) -> Plan:
    """The legacy `Plan` view of a composed pipeline: plan.json and `reel summarise`."""
    merged = dict(stats)
    ok = sum(1 for item in items if item.status == "ok")
    merged["ok"] = ok
    merged["failed"] = len(items) - ok
    return Plan(
        word=word,
        source=source,
        items=[item.to_planned() for item in items],
        stats=merged,
    )


# --------------------------------------------------------------------- 4. media prep


@dataclass(slots=True)
class CutStep:
    index: int
    item: ItemRecord
    start_ms: int
    end_ms: int
    clip_duration_ms: int = 0
    frames: int = 0
    skipped: bool = False
    skip_note: str = ""


def plan_cuts(
    items: Sequence[ItemRecord],
    *,
    settings: Settings,
    pre_roll_ms: int,
    post_roll_ms: int,
    fps: int = 30,
) -> list[CutStep]:
    """Turn every aligned item into a window to cut (pure maths, no I/O)."""
    steps: list[CutStep] = []
    last_end_by_asset: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        assert item.asset is not None
        asset = item.asset
        asset_key = str(asset.url)
        start = item.local_start_ms - pre_roll_ms
        end = item.local_end_ms + post_roll_ms

        if start < 0:
            end -= start
            start = 0
        previous_end = last_end_by_asset.get(asset_key)
        if previous_end is not None and start < previous_end:
            start = previous_end  # never re-use the same footage twice
        if asset.duration_ms:
            limit = asset.duration_ms - 120
            if end > limit:
                end = limit
            if start >= end:
                start = max(0, end - max(500, item.local_end_ms - item.local_start_ms))

        clip_duration_ms = end - start
        if clip_duration_ms < 400:
            steps.append(CutStep(index=index, item=item, start_ms=start, end_ms=end,
                                 skipped=True, skip_note="trimmed away"))
            continue

        # Land on a whole number of frames so the cue timings, the clip
        # durations and the concatenated timeline agree exactly.
        frames = max(1, round(clip_duration_ms * fps / 1000))
        clip_duration_ms = round(frames * 1000 / fps)
        steps.append(
            CutStep(index=index, item=item, start_ms=start, end_ms=end,
                    clip_duration_ms=clip_duration_ms, frames=frames)
        )
        last_end_by_asset[asset_key] = end
    return steps


def cut_clips(
    steps: Sequence[CutStep],
    workdir: Path,
    *,
    settings: Settings,
    height: int = 1080,
    fps: int = 30,
    total_items: int = 0,
    on_detail: Detail | None = None,
    cancel: Any = None,
) -> dict[int, Path]:
    """Cut every planned step; returns `{step index: clip path}`.

    A cancel token stops the loop between clips and terminates an in-flight ffmpeg
    process, so a cancelled job stops within one clip rather than finishing the reel.
    """
    detail = on_detail or _noop
    paths: dict[int, Path] = {}
    for step in steps:
        if step.skipped:
            continue
        if cancel is not None:
            cancel.raise_if_cancelled()
        item = step.item
        assert item.asset is not None
        asset = item.asset
        clip_path = workdir / f"clip_{step.index:02d}_{item.segment.publicId}.mp4"
        ffmpeg.cut_clip(
            asset.url,
            clip_path,
            start_s=step.start_ms / 1000.0,
            duration_s=step.clip_duration_ms / 1000.0,
            height=height,
            fps=fps,
            crf=settings.crf,
            preset=settings.preset,
            headers=asset.headers or None,
            audio_track=asset.audio_track,
            cancel=cancel,
        )
        paths[step.index] = clip_path
        detail(
            f"  [{len(paths)}/{total_items}] {item.source_label} "
            f"{step.start_ms}-{step.end_ms} ms -> {clip_path.name}"
        )
    return paths


# ------------------------------------------------------------------------ 5. render


def build_card(word: str, settings: Settings, on_detail: Detail | None = None) -> Any:
    """The vocabulary card: bundled JLPT data, plus an on-demand meaning lookup."""
    from ...vocab import JishoDictionary, describe

    if not word:
        return None
    dictionary = None
    if not settings.dictionary_offline:
        dictionary = JishoDictionary(settings.cache_dir / "dictionary.json")
    card = describe(word, dictionary=dictionary)
    if on_detail:
        detail = card.level_label or "no JLPT level"
        on_detail(f"  card: {card.word} · {card.romaji} · {card.meaning or '(no meaning)'} · {detail}")
    return card


@dataclass(slots=True)
class ClipLayout:
    """One rendered clip placed on the reel timeline."""

    index: int
    item_index: int
    item: ItemRecord
    clip: Path
    duration_ms: int
    start_ms: int
    japanese: str
    english: str
    tokens: Any
    marked: str | None
    source_label: str
    index_label: str
    reading: str


def build_layout(
    steps: Sequence[CutStep],
    clip_paths: dict[int, Path],
    *,
    word: str,
    total_items: int,
) -> tuple[list[ClipLayout], int, list[dict[str, Any]]]:
    """Lay the reel out sequentially and build its captions and manifest entries."""
    layouts: list[ClipLayout] = []
    manifest_items: list[dict[str, Any]] = []
    cursor_ms = 0
    clip_no = 0
    for step in steps:
        item = step.item
        if step.skipped:
            manifest_items.append(
                {**item.to_dict(), "status": "skipped", "note": step.skip_note}
            )
            continue
        clip_no += 1
        segment = item.segment
        line_reading = subtitles.line_romaji(segment.japanese, segment.textJa.tokens)
        layouts.append(
            ClipLayout(
                index=clip_no,
                item_index=step.index,
                item=item,
                clip=clip_paths[step.index],
                duration_ms=step.clip_duration_ms,
                start_ms=cursor_ms,
                japanese=segment.japanese,
                english=segment.english,
                tokens=segment.textJa.tokens,
                marked=segment.textJa.highlight,
                source_label=item.source_label,
                index_label=f"{clip_no}/{total_items}",
                reading=line_reading,
            )
        )
        cursor_ms += step.clip_duration_ms
        manifest_items.append(
            {**item.to_dict(), "status": "rendered", "clip": str(clip_paths[step.index])}
        )
    return layouts, cursor_ms, manifest_items


def cues_from_layout(layouts: Sequence[ClipLayout], *, word: str, card: Any) -> list[subtitles.Cue]:
    return [
        subtitles.Cue(
            start_ms=layout.start_ms,
            end_ms=layout.start_ms + layout.duration_ms,
            japanese=layout.japanese,
            english=layout.english,
            word=word,
            tokens=layout.tokens,
            marked=layout.marked,
            source=layout.source_label,
            index_label=layout.index_label,
            reading=layout.reading,
            vocab=card,
        )
        for layout in layouts
    ]


def cues_from_records(records: Sequence[Any], *, word: str, card: Any) -> list[subtitles.Cue]:
    """The stage path: build the ASS cues from serialized clip records."""
    return [
        subtitles.Cue(
            start_ms=record.cue.start_ms,
            end_ms=record.cue.end_ms,
            japanese=record.cue.japanese,
            english=record.cue.english,
            word=word,
            tokens=record.cue.tokens,
            marked=record.cue.marked,
            source=record.cue.source,
            index_label=record.cue.index_label,
            reading=record.cue.reading,
            vocab=card,
        )
        for record in records
    ]


def render_geometry(aspect: str) -> tuple[int, int, int, subtitles.Layout]:
    """`(width, play_y, fps, layout)` for an aspect, matching the previous renderer."""
    fps = 30
    width = 1080
    play_y = 1920 if aspect == "vertical" else 1080
    if aspect == "original":
        width, play_y = 1920, 1080
    return width, play_y, fps, subtitles.Layout.for_aspect(aspect, play_y=play_y)


def render_reel(
    clips: Sequence[Path],
    cues: Sequence[subtitles.Cue],
    *,
    settings: Settings,
    workdir: Path,
    outdir: Path,
    reel_name: str,
    aspect: str,
    layout: subtitles.Layout,
    metrics: subtitles.Metrics,
    total_ms: int,
    watermark: str,
    manifest: dict[str, Any],
    keep_clips: bool,
    cancel: Any = None,
) -> "RenderResult":
    """One ASS, one SRT, one ffmpeg pass, one legacy manifest next to the video."""
    if cancel is not None:
        cancel.raise_if_cancelled()
    ass_path = subtitles.write_ass(
        workdir / f"{reel_name}.ass",
        list(cues),
        layout=layout,
        metrics=metrics,
        font_ja=settings.font_ja,
        font_en=settings.font_en,
        font_card=settings.font_card,
        watermark=watermark,
        total_ms=total_ms,
    )
    srt_path = subtitles.write_srt(workdir / f"{reel_name}.srt", list(cues))

    width, play_y, fps, _ = render_geometry(aspect)
    video_path = outdir / f"{reel_name}.mp4"
    ffmpeg.build_reel_video(
        list(clips),
        video_path,
        ass_path=ass_path,
        aspect=aspect,
        width=width,
        height=play_y,
        fps=fps,
        crf=settings.crf,
        preset=settings.preset,
        # The sharp video lives between the card and the caption.
        band_y=layout.video_y if aspect == "vertical" else None,
        band_h=layout.video_h if aspect == "vertical" else None,
        cancel=cancel,
    )

    manifest_path = outdir / f"{reel_name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if not keep_clips:
        for clip in clips:
            clip.unlink(missing_ok=True)

    return RenderResult(
        video=video_path,
        ass=ass_path,
        srt=srt_path,
        manifest=manifest_path,
        cues=list(cues),
        duration_ms=total_ms,
        clips=list(clips) if keep_clips else [],
    )


def legacy_manifest(
    *,
    video: Path,
    word: str,
    source: str,
    aspect: str,
    duration_ms: int,
    manifest_items: list[dict[str, Any]],
    stats: dict[str, Any],
    credits: Iterable[str],
) -> dict[str, Any]:
    return {
        "reel": str(video),
        "word": word,
        "created": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "aspect": aspect,
        "durationMs": duration_ms,
        "segments": manifest_items,
        "stats": stats,
        "credits": sorted(set(credits)),
    }


# --------------------------------------------------------------- functional API


@dataclass(slots=True)
class RenderResult:
    video: Path
    ass: Path
    srt: Path
    manifest: Path
    cues: list[subtitles.Cue]
    duration_ms: int
    clips: list[Path] = field(default_factory=list)


def build_plan(
    client: NadeshikoClient,
    word: str,
    *,
    provider: SourceProvider | None = None,
    settings: Settings | None = None,
    max_segments: int = 12,
    min_segments: int = 1,
    per_media: int = 1,
    categories: Sequence[str] | None = None,
    per_category: int | None = None,
    content_rating: list[str] | None = None,
    exact_match: bool = False,
    max_candidates: int = 150,
    max_pages: int = 5,
    min_ms: int = 600,
    max_ms: int = 30000,
    require_english: bool = True,
    min_confidence: float = 0.35,
    max_anchors: int = 6,
    min_anchors: int = 1,
    verbose: bool = False,
    dry_run: bool = False,
    only: Sequence[tuple[str, int]] | None = None,
) -> Plan:
    """Search, select and align — everything except the actual cutting."""
    settings = settings or get_settings()
    provider = provider or get_provider(settings=settings)
    detail: Detail = print if verbose else _noop

    items, stats = search_and_select(
        client,
        word,
        settings=settings,
        categories=categories,
        per_category=per_category,
        content_rating=content_rating,
        exact_match=exact_match,
        max_segments=max_segments,
        min_segments=min_segments,
        per_media=per_media,
        max_candidates=max_candidates,
        max_pages=max_pages,
        min_ms=min_ms,
        max_ms=max_ms,
        require_english=require_english,
        only=only,
        on_detail=detail,
    )
    groups = group_items(items)
    episodes: dict[str, EpisodeRecord] = {}
    failures: dict[str, str] = {}
    if not dry_run:
        episodes, failures = acquire_episodes(client, provider, groups, on_detail=detail)
    composed = compose_items(
        groups=groups,
        episodes=episodes,
        failures=failures,
        settings=settings,
        min_confidence=min_confidence,
        max_anchors=max_anchors,
        min_anchors=min_anchors,
        dry_run=dry_run,
        on_detail=detail,
    )
    return plan_from_items(word, provider.name, composed, stats)


def render(
    plan: Plan,
    *,
    settings: Settings | None = None,
    outdir: Path | None = None,
    name: str | None = None,
    pre_roll_ms: int | None = None,
    post_roll_ms: int | None = None,
    aspect: str | None = None,
    keep_clips: bool = False,
    watermark: str = "",
    verbose: bool = False,
) -> RenderResult:
    """Cut every aligned segment and render one reel."""
    settings = settings or get_settings()
    outdir = (outdir or settings.outdir).expanduser()
    pre_roll = settings.pre_roll_ms if pre_roll_ms is None else pre_roll_ms
    post_roll = settings.post_roll_ms if post_roll_ms is None else post_roll_ms
    aspect = normalise_aspect(aspect or settings.aspect)

    items = plan.ok_items
    if not items:
        raise RuntimeError(
            "nothing to render: no segment aligned successfully. "
            "Run `reel plan` verbosely to see why (usually the source provider can't "
            "find the episode, or the reference clips don't match your copy)."
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    reel_name = name or f"{slugify(plan.word)}-{stamp}"
    workdir = settings.workdir / "render" / reel_name
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    width, play_y, fps, layout = render_geometry(aspect)
    detail: Detail = print if verbose else _noop
    card = build_card(plan.word, settings, detail)

    records = [item.to_record() for item in items]
    steps = plan_cuts(records, settings=settings, pre_roll_ms=pre_roll, post_roll_ms=post_roll, fps=fps)
    clip_paths = cut_clips(
        steps, workdir, settings=settings, height=1080, fps=fps,
        total_items=len(items), on_detail=detail,
    )
    if not clip_paths:
        raise RuntimeError("all clips were trimmed away; check pre/post roll and alignment")

    layouts, cursor_ms, manifest_items = build_layout(
        steps, clip_paths, word=plan.word, total_items=len(items)
    )
    cues = cues_from_layout(layouts, word=plan.word, card=card)

    # Furigana is positioned from the font's real advance widths, which are
    # measured once here: a CJK glyph is not one em wide in every font
    # (Hiragino Sans renders at 0.80, YuGothic at 0.63), and guessing puts the
    # readings progressively further from the characters they annotate.
    metrics = subtitles.measure_metrics(settings.font_ja, layout.ja_size)
    detail(f"  metrics: {settings.font_ja} @{layout.ja_size} -> cjk {metrics.cjk:.3f} em")

    clips = [clip_paths[step.index] for step in steps if not step.skipped]
    manifest = legacy_manifest(
        video=outdir / f"{reel_name}.mp4",
        word=plan.word,
        source=plan.source,
        aspect=aspect,
        duration_ms=cursor_ms,
        manifest_items=manifest_items,
        stats=plan.stats,
        credits=(item.source_label for item in items),
    )
    return render_reel(
        clips,
        cues,
        settings=settings,
        workdir=workdir,
        outdir=outdir,
        reel_name=reel_name,
        aspect=aspect,
        layout=layout,
        metrics=metrics,
        total_ms=cursor_ms,
        watermark=watermark,
        manifest=manifest,
        keep_clips=keep_clips,
    )


def summarise(plan: dict[str, Any]) -> str:
    """Human-readable plan summary (takes the plan.json payload)."""
    stats = plan.get("stats") or {}
    lines = [
        f"word: {plan.get('word')}",
        f"source: {plan.get('source')}",
        f"hits: {stats.get('totalHits')}  usable: {stats.get('usableCandidates')}  "
        f"selected: {stats.get('selected')}  aligned: {stats.get('ok')}",
    ]
    # A mixed reel has to show its mix, or "why is this all anime?" is unanswerable.
    by_category = stats.get("byCategory") or {}
    if by_category:
        lines.append(
            "corpus: " + "  ".join(f"{k.lower()} {v}" for k, v in sorted(by_category.items()))
        )
    lines.append("")
    for index, item in enumerate(plan.get("items") or [], start=1):
        status = item.get("status", "")
        flag = {"ok": "OK ", "unaligned": "ALN", "unresolved": "SRC", "out-of-range": "RNG"}.get(status, "?  ")
        context = item.get("contextSegments") or 0
        extra = f" (+{context} context)" if context else ""
        lines.append(
            f"{index:>2}. [{flag}] {item.get('category', '').lower():6s} {item.get('media')} "
            f"ep{item.get('episode')} "
            f"scene {item.get('sceneStartMs')}-{item.get('sceneEndMs')} ms -> "
            f"local {item.get('localStartMs')}-{item.get('localEndMs')} ms{extra}"
        )
        lines.append(f"      {item.get('textJa')}")
        lines.append(f"      {item.get('textEn')}")
        if item.get("note"):
            lines.append(f"      note: {item['note']}")
    return "\n".join(lines)
