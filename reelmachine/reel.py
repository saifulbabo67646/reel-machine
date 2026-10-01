"""Plan + render: Nadeshiko segment -> aligned cut -> subtitled reel.

`build_plan` decides *what* to cut (search, filter, pick, align against your
copy).  `render` decides *how* it looks (cut, normalise, subtitle, join, burn).
Splitting them means `reel plan` costs API quota only, and re-rendering a reel
with a different aspect ratio or font costs none.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import ffmpeg, subtitles
from .align import TimelineMap, align_episode, cached_envelope
from .config import Settings, get_settings, normalise_aspect
from .matching import describe as describe_match, is_word_match, matched_forms
from .models import Media, Segment
from .nadeshiko import NadeshikoClient
from .sources import EpisodeAsset, SourceError, SourceProvider, get_provider

IDEAL_MIN_MS = 1200
IDEAL_MAX_MS = 9000

#: How much material to line up for a `target_ms` reel.  A tight cut is short
#: and a clip or two may fail to align, so selecting exactly the target tends
#: to land under it; a fifth again usually absorbs the losses.
TARGET_OVERSHOOT = 1.2


def compute_scene_window(
    word_start_ms: int,
    word_end_ms: int,
    context: Iterable[Segment] = (),
    *,
    max_ms: int = 14_000,
    min_ms: int = 3_000,
) -> tuple[int, int]:
    """The window to cut, in source time, including the surrounding dialogue.

    A single segment is often under a second — `やっぱり!` alone teaches a viewer
    nothing about how the word is used.  `Segment.startTimeMs` only ever
    describes one line, so the scene has to be widened with
    `GET /v1/media/segments/{id}/context`, which returns the neighbouring
    segments of the same episode with their own exact times.

    The window spans those neighbours and is then clamped: centred on the word
    when the exchange is longer than `max_ms`, padded out when it is shorter than
    `min_ms`.  The word always stays inside the result.
    """
    neighbours = list(context)
    low = min([word_start_ms, *(s.startTimeMs for s in neighbours)])
    high = max([word_end_ms, *(s.endTimeMs for s in neighbours)])

    if max_ms > 0 and high - low > max_ms:
        centre = (word_start_ms + word_end_ms) // 2
        low = max(low, centre - max_ms // 2)
        high = min(high, low + max_ms)
        low = max(0, high - max_ms)
    elif min_ms > 0 and high - low < min_ms:
        deficit = min_ms - (high - low)
        low = max(0, low - deficit // 2)
        high = low + min_ms
    return low, high


def compute_cut_window(
    word_start_ms: int,
    word_end_ms: int,
    *,
    pad_ms: int = 220,
    min_ms: int = 0,
    max_ms: int = 14_000,
) -> tuple[int, int]:
    """The window to actually *cut*, which is narrower than the scene.

    A scene is widened with neighbouring lines so the alignment has something
    to lock onto, but the caption only ever describes the one line the word is
    in.  Cutting the whole scene therefore shows several seconds of video with
    a caption that has already gone — the word is over before the clip is.

    Cutting just the line, padded by `pad_ms` on each side, keeps picture and
    caption together.  The cost is that clips get short, which is why the
    planner keeps adding them until the reel reaches `target_ms`.
    """
    low = max(0, word_start_ms - max(0, pad_ms))
    high = word_end_ms + max(0, pad_ms)
    if high <= low:
        return low, high
    if min_ms and high - low < min_ms:
        deficit = min_ms - (high - low)
        low = max(0, low - deficit // 2)
        high = low + min_ms
    if max_ms and high - low > max_ms:
        centre = (word_start_ms + word_end_ms) // 2
        low = max(0, centre - max_ms // 2)
        high = low + max_ms
    return low, high


def select_segments(
    usable: Sequence[Segment],
    media_by_id: dict[str, Media],
    settings: Settings,
    *,
    max_segments: int,
    min_segments: int = 1,
    per_media: int = 1,
    per_category: int | None = None,
) -> list[Segment]:
    """Pick the clips a reel is built from, best-scoring first.

    Three limits apply at once, and each exists for a different reason:

    * `per_media` keeps one title from filling the reel;
    * `per_category` does the same for a whole corpus — anime outnumbers
      J-Drama in the index by roughly 15 to 1, so without it a "mixed" reel is
      simply an anime reel with the occasional drama line;
    * `max_segments` is a hard ceiling, while `settings.target_ms` is the goal:
      a tight cut is only a few seconds, so the count is driven by how much
      running time has been lined up rather than by a fixed number.

    The overshoot on the target leaves room for a clip or two failing to align
    without the finished reel dropping under it.
    """
    ranked = sorted(usable, key=score_segment, reverse=True)
    cap = settings.per_category if per_category is None else per_category
    goal_ms = int(settings.target_ms * TARGET_OVERSHOOT) if settings.target_ms > 0 else 0
    per_media_count: dict[str, int] = {}
    per_category_count: dict[str, int] = {}
    selected: list[Segment] = []
    planned_ms = 0
    for segment in ranked:
        if per_media_count.get(segment.mediaPublicId, 0) >= per_media:
            continue
        kind = _category_of(segment, media_by_id)
        if cap and per_category_count.get(kind, 0) >= cap:
            continue
        per_media_count[segment.mediaPublicId] = per_media_count.get(segment.mediaPublicId, 0) + 1
        per_category_count[kind] = per_category_count.get(kind, 0) + 1
        selected.append(segment)
        planned_ms += expected_clip_ms(segment, settings)
        if len(selected) >= max_segments:
            break
        if goal_ms and len(selected) >= min_segments and planned_ms >= goal_ms:
            break
    return selected


def _category_of(segment: Segment, media_by_id: dict[str, Media]) -> str:
    """`ANIME` / `JDRAMA` / … for a segment, from the media it belongs to."""
    media = media_by_id.get(segment.mediaPublicId)
    return getattr(media, "category", "") or "ANIME"


def expected_clip_ms(segment: Segment, settings: Settings) -> int:
    """How long this candidate's clip will be, before any alignment is spent.

    The planner needs this to decide how many clips a reel needs, and the
    answer is available from the search result alone: the line's own duration
    plus the padding and roll that will be added around it.
    """
    pad = max(0, settings.cut_pad_ms) if settings.cut_mode == "line" else 0
    base = segment.duration_ms + 2 * pad
    if settings.cut_mode != "line":
        base = max(base, settings.min_clip_ms)
    return base + max(0, settings.pre_roll_ms) + max(0, settings.post_roll_ms)


def slugify(text: str, *, fallback: str = "reel") -> str:
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = re.sub(r"[^\w\s\-]+", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_]+", "-", text)
    return (text[:60] or fallback).strip("-") or fallback


# ----------------------------------------------------------------------- planning


@dataclass(slots=True)
class PlannedSegment:
    segment: Segment
    media: Media | None = None
    asset: EpisodeAsset | None = None
    timeline: TimelineMap | None = None
    local_start_ms: int = 0
    local_end_ms: int = 0
    scene_start_ms: int = 0   # source-time window, including the neighbouring lines
    scene_end_ms: int = 0
    context_count: int = 0
    word: str = ""            # the word this clip is meant to teach
    category: str = "ANIME"   # ANIME | JDRAMA — which corpus it came from
    status: str = "pending"  # ok | unaligned | unresolved | out-of-range
    note: str = ""

    @property
    def key(self) -> str:
        return self.segment.publicId

    @property
    def media_name(self) -> str:
        if self.media is None:
            return self.segment.mediaPublicId
        return self.media.nameEn or self.media.nameRomaji or self.media.nameJa

    @property
    def source_label(self) -> str:
        return f"{self.media_name} · ep {self.segment.episode}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "segmentId": self.segment.publicId,
            "mediaPublicId": self.segment.mediaPublicId,
            "media": self.media_name,
            "category": self.category,
            "episode": self.segment.episode,
            "srcStartMs": self.segment.startTimeMs,
            "srcEndMs": self.segment.endTimeMs,
            "sceneStartMs": self.scene_start_ms,
            "sceneEndMs": self.scene_end_ms,
            "contextSegments": self.context_count,
            "matchedForm": describe_match(self.segment, self.word),
            "localStartMs": self.local_start_ms,
            "localEndMs": self.local_end_ms,
            "status": self.status,
            "note": self.note,
            "textJa": self.segment.japanese,
            "textEn": self.segment.english,
            "contentRating": self.segment.contentRating,
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "asset": self.asset.url if self.asset else None,
            "audioTrack": self.asset.audio_track if self.asset else None,
        }


@dataclass(slots=True)
class Plan:
    word: str
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    items: list[PlannedSegment] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok_items(self) -> list[PlannedSegment]:
        return [i for i in self.items if i.status == "ok"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "created": self.created,
            "source": self.source,
            "stats": self.stats,
            "items": [i.to_dict() for i in self.items],
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def score_segment(segment: Segment) -> float:
    """Rank candidates: prefer human translations, SAFE, and comfortable length."""
    score = 0.0
    score += 3.0 if not segment.textEn.isMachineTranslated else 0.0
    score += 2.0 if segment.contentRating == "SAFE" else 0.0
    duration = segment.duration_ms
    if IDEAL_MIN_MS <= duration <= IDEAL_MAX_MS:
        score += 2.0
    elif duration < IDEAL_MIN_MS:
        score -= 1.5
    elif duration > 20000:
        score -= 2.5
    elif duration > IDEAL_MAX_MS:
        score -= 0.5
    score += min(len(segment.japanese), 40) / 40.0
    if segment.textJa.tokens:
        score += 0.5  # token data means we can highlight the word precisely
    return score


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

    # ---- 1. search ------------------------------------------------------
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

    # ---- 2. filter ------------------------------------------------------
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

    if verbose and dropped_variants:
        forms = sorted({f for s in dropped_variants for f in matched_forms(s)})
        print(
            f"  x skipped {len(dropped_variants)} clip(s) whose audio says "
            f"{', '.join(forms) or 'a variant'} — not {word!r} "
            f"(REEL_MATCH_MODE={settings.match_mode})"
        )

    # ---- 3. select (spread across titles and corpora) --------------------
    selected = select_segments(
        usable,
        media_by_id,
        settings,
        max_segments=max_segments,
        min_segments=min_segments,
        per_media=per_media,
        per_category=per_category,
    )

    # ---- 3b. widen each pick to its scene --------------------------------
    # `Segment.startTimeMs` describes one line; a line can be under a second and
    # teaches nothing on its own.  `/context` supplies the neighbouring lines of
    # the same episode, with their own exact times.
    scenes: dict[str, tuple[int, int, list[Segment]]] = {}
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
                if verbose:
                    print(f"  ! context lookup failed for {segment.publicId}: {exc}")
        low, high = compute_scene_window(
            segment.startTimeMs,
            segment.endTimeMs,
            neighbours,
            max_ms=settings.max_clip_ms,
            min_ms=settings.min_clip_ms,
        )
        scene_ms = high - low
        # `scenes` feeds two things: the window that gets cut, and the
        # neighbouring lines used as alignment anchors.  The anchors want the
        # whole exchange; the cut wants only the line the caption describes.
        if settings.cut_mode == "line":
            low, high = compute_cut_window(
                segment.startTimeMs,
                segment.endTimeMs,
                pad_ms=settings.cut_pad_ms,
                min_ms=settings.min_clip_ms,
                max_ms=settings.max_clip_ms,
            )
        scenes[segment.publicId] = (low, high, neighbours)
        if verbose and neighbours:
            print(
                f"  ~ {segment.publicId}: {segment.duration_ms} ms of line -> "
                f"{(high - low) / 1000:.1f}s cut from a {scene_ms / 1000:.1f}s scene "
                f"with {len(neighbours)} neighbouring line(s)"
            )

    plan = Plan(word=word, source=provider.name)
    plan.stats = {
        "totalHits": total_hits,
        "usableCandidates": len(usable),
        "selected": len(selected),
        "titles": len({s.mediaPublicId for s in selected}),
        "categories": wanted or ["ANIME", "JDRAMA"],
        "byCategory": {
            k: sum(1 for s in selected if _category_of(s, media_by_id) == k)
            for k in sorted({_category_of(s, media_by_id) for s in selected})
        },
        "contextSegments": sum(len(n) for _, _, n in scenes.values()),
        "matchMode": settings.match_mode,
        "droppedVariants": len(dropped_variants),
    }

    # ---- 4. resolve + align per episode ---------------------------------
    by_episode: dict[tuple[str, int], list[Segment]] = {}
    for segment in selected:
        by_episode.setdefault((segment.mediaPublicId, segment.episode), []).append(segment)

    for (media_public_id, episode), group in by_episode.items():
        media = media_by_id.get(media_public_id)
        label = (media.nameEn if media else media_public_id) or media_public_id
        items = []
        for group_segment in group:
            low, high, neighbours = scenes.get(
                group_segment.publicId,
                (group_segment.startTimeMs, group_segment.endTimeMs, []),
            )
            items.append(
                PlannedSegment(
                    segment=group_segment,
                    media=media,
                    scene_start_ms=low,
                    scene_end_ms=high,
                    context_count=len(neighbours),
                    word=word,
                    category=_category_of(group_segment, media_by_id),
                )
            )
        plan.items.extend(items)

        if dry_run:
            for item in items:
                item.status = "pending"
                item.note = "dry run: not resolved"
            continue

        try:
            asset = provider.resolve(media, episode)
            provider.probe(asset)
        except (SourceError, ffmpeg.FFmpegError) as exc:
            for item in items:
                item.status = "unresolved"
                item.note = f"{label} ep{episode}: {exc}"
            if verbose:
                print(f"  ! {label} ep{episode}: {exc}")
            continue

        for item in items:
            item.asset = asset

        # Anchor the alignment on every line of the scene, not just the word.
        # A single short clip searched across a whole episode frequently has a
        # near-tie; three clips from one exchange pin the offset between them.
        anchor_segments: list[Segment] = []
        seen_anchor_ids: set[str] = set()
        for item in items:
            candidates = [item.segment, *scenes.get(item.segment.publicId, (0, 0, []))[2]]
            for candidate in candidates:
                if candidate.publicId not in seen_anchor_ids:
                    seen_anchor_ids.add(candidate.publicId)
                    anchor_segments.append(candidate)

        clips = provider.reference_clips(anchor_segments, client=client, asset=asset)

        if verbose:
            print(
                f"  · {label} ep{episode}: {len(clips)} reference clip(s) for "
                f"{len(items)} segment(s) + {len(anchor_segments) - len(items)} scene line(s)"
            )

        timeline = align_episode(
            asset.url,
            anchor_segments,
            clip_paths=clips,
            headers=asset.headers or None,
            audio_track=asset.audio_track,
            max_anchors=max_anchors,
            episode_duration_ms=asset.duration_ms,
            # Three clips that disagree are worse than one that is merely weak.
            min_anchors=max(min_anchors, 2) if len(anchor_segments) >= 3 else min_anchors,
            verbose=verbose,
        )
        if verbose:
            print(f"    -> {timeline.describe()}")

        for item in items:
            item.timeline = timeline
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
                item.note = f"mapped start {item.local_start_ms} ms past end of file ({asset.duration_ms} ms)"
                continue
            item.status = "ok"
            drift = timeline.to_local_ms(item.segment.startTimeMs) - item.segment.startTimeMs
            item.note = (
                f"drift {drift:+d} ms, conf {timeline.confidence:.2f}, "
                f"scene {(item.scene_end_ms - item.scene_start_ms) / 1000:.1f}s"
            )

    plan.stats["ok"] = len(plan.ok_items)
    plan.stats["failed"] = len(plan.items) - len(plan.ok_items)
    return plan


# ------------------------------------------------------------------------ mine


def mine_episodes(
    client: NadeshikoClient,
    targets: Sequence[tuple[str, int]],
    *,
    settings: Settings | None = None,
    levels: Sequence[int] | None = None,
    limit: int = 25,
    per_episode_cap: int = 900,
    categories: Sequence[str] | None = None,
    dictionary: Any = None,
    verbose: bool = False,
) -> list[tuple[Any, int]]:
    """Every JLPT word spoken across `targets`, ranked by how often it is said.

    This is what makes an already-downloaded episode worth more than the single
    reel it was fetched for: pull all of its dialogue once and it yields dozens
    of graded words, each of which can become its own reel *from the same file*
    — no further downloading.

    Note the deliberate inversion of `build_plan`: that searches for a word and
    asks which episodes have it, this asks which words an episode has.  The API
    only offers the first, so it is replayed per episode behind a media filter.
    """
    from .vocab import JishoDictionary, mine_tokens

    settings = settings or get_settings()
    ratings = settings.content_rating or None
    tokens: list[Any] = []
    for media_public_id, episode in targets:
        filters: dict[str, Any] = {
            "media": {"include": [{"mediaPublicId": media_public_id, "episodes": [episode]}]}
        }
        if categories:
            filters["category"] = [c.upper() for c in categories]
        found = 0
        for segment in client.iter_search(
            None,
            max_results=per_episode_cap,
            pages=max(1, per_episode_cap // 50 + 1),
            filters=filters,
            content_rating=ratings,
            include_media=True,
        ):
            if segment.textJa.tokens:
                tokens.extend(segment.textJa.tokens)
                found += 1
        if verbose:
            print(f"  · {media_public_id} ep{episode}: {found} line(s) of dialogue")
    if not tokens:
        return []

    if dictionary is None and not settings.dictionary_offline:
        dictionary = JishoDictionary(settings.cache_dir / "dictionary.json")
    return mine_tokens(
        tokens,
        dictionary=dictionary,
        levels=list(levels) if levels else None,
        limit=limit,
    )


# ----------------------------------------------------------------------- render


@dataclass(slots=True)
class RenderResult:
    video: Path
    ass: Path
    srt: Path
    manifest: Path
    cues: list[subtitles.Cue]
    duration_ms: int
    clips: list[Path] = field(default_factory=list)


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

    height = 1080
    fps = 30
    width = 1080
    play_y = 1920 if aspect == "vertical" else (1080 if aspect == "square" else 1080)
    if aspect == "original":
        width, play_y = 1920, 1080
    layout = subtitles.Layout.for_aspect(aspect, play_y=play_y)

    # The card is built once for the whole reel: it is the word being taught,
    # not a per-clip caption.  Meanings come from the bundled JLPT list plus an
    # on-demand dictionary lookup, so this costs nothing when cached and is
    # skipped entirely when offline.
    from .vocab import JishoDictionary, describe

    card: Any = None
    if plan.word:
        dictionary = None
        if not settings.dictionary_offline:
            dictionary = JishoDictionary(settings.cache_dir / "dictionary.json")
        card = describe(plan.word, dictionary=dictionary)
        if verbose:
            detail = card.level_label or "no JLPT level"
            print(f"  card: {card.word} · {card.romaji} · {card.meaning or '(no meaning)'} · {detail}")

    # Group by asset so each episode's neighbours can be de-overlapped.
    clips: list[Path] = []
    cues: list[subtitles.Cue] = []
    manifest_items: list[dict[str, Any]] = []
    cursor_ms = 0
    last_end_by_asset: dict[str, int] = {}

    for index, item in enumerate(items, start=1):
        segment = item.segment
        asset = item.asset
        assert asset is not None

        asset_key = str(asset.url)
        start = item.local_start_ms - pre_roll
        end = item.local_end_ms + post_roll

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
            manifest_items.append({**item.to_dict(), "status": "skipped", "note": "trimmed away"})
            continue

        # Land on a whole number of frames so the cue timings, the clip
        # durations and the concatenated timeline agree exactly.
        frames = max(1, round(clip_duration_ms * fps / 1000))
        clip_duration_ms = round(frames * 1000 / fps)

        clip_path = workdir / f"clip_{index:02d}_{segment.publicId}.mp4"
        ffmpeg.cut_clip(
            asset.url,
            clip_path,
            start_s=start / 1000.0,
            duration_s=clip_duration_ms / 1000.0,
            height=height,
            fps=fps,
            crf=settings.crf,
            preset=settings.preset,
            headers=asset.headers or None,
            audio_track=asset.audio_track,
        )
        clips.append(clip_path)
        last_end_by_asset[asset_key] = end

        # The card is the lesson; the caption is this clip's line.  The reading
        # shown under the caption is the whole line in romaji, which is what a
        # learner would otherwise have to sound out themselves.
        line_reading = subtitles.line_romaji(segment.japanese, segment.textJa.tokens)
        cues.append(
            subtitles.Cue(
                start_ms=cursor_ms,
                end_ms=cursor_ms + clip_duration_ms,
                japanese=segment.japanese,
                english=segment.english,
                word=plan.word,
                tokens=segment.textJa.tokens,
                marked=segment.textJa.highlight,
                source=item.source_label,
                index_label=f"{len(clips)}/{len(items)}",
                reading=line_reading,
                vocab=card,
            )
        )
        cursor_ms += clip_duration_ms
        manifest_items.append({**item.to_dict(), "status": "rendered", "clip": str(clip_path)})
        if verbose:
            print(f"  [{len(clips)}/{len(items)}] {item.source_label} {start}-{end} ms -> {clip_path.name}")

    if not clips:
        raise RuntimeError("all clips were trimmed away; check pre/post roll and alignment")

    # Furigana is positioned from the font's real advance widths, which are
    # measured once here: a CJK glyph is not one em wide in every font
    # (Hiragino Sans renders at 0.80, YuGothic at 0.63), and guessing puts the
    # readings progressively further from the characters they annotate.
    metrics = subtitles.measure_metrics(settings.font_ja, layout.ja_size)
    if verbose:
        print(f"  metrics: {settings.font_ja} @{layout.ja_size} -> cjk {metrics.cjk:.3f} em")

    ass_path = subtitles.write_ass(
        workdir / f"{reel_name}.ass",
        cues,
        layout=layout,
        metrics=metrics,
        font_ja=settings.font_ja,
        font_en=settings.font_en,
        font_card=settings.font_card,
        watermark=watermark,
        total_ms=cursor_ms,
    )
    srt_path = subtitles.write_srt(workdir / f"{reel_name}.srt", cues)

    video_path = outdir / f"{reel_name}.mp4"
    ffmpeg.build_reel_video(
        clips,
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
    )

    manifest = {
        "reel": str(video_path),
        "word": plan.word,
        "created": datetime.now(timezone.utc).isoformat(),
        "source": plan.source,
        "aspect": aspect,
        "durationMs": cursor_ms,
        "segments": manifest_items,
        "stats": plan.stats,
        "credits": sorted({i.source_label for i in items}),
    }
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
        cues=cues,
        duration_ms=cursor_ms,
        clips=clips if keep_clips else [],
    )


# ------------------------------------------------------------------------ verify


def verify_plan(
    plan: Plan,
    *,
    provider: SourceProvider | None = None,
    settings: Settings | None = None,
    client: NadeshikoClient | None = None,
    margin_ms: int = 4000,
) -> list[dict[str, Any]]:
    """Re-measure each mapped window against a tight search around the prediction.

    A second, independent look: if the first pass locked onto the wrong repeated
    line, this low-margin re-check usually drops its score and flags it.
    """
    settings = settings or get_settings()
    provider = provider or get_provider(settings=settings)
    report: list[dict[str, Any]] = []

    for item in plan.ok_items:
        if item.asset is None:
            continue
        entry: dict[str, Any] = {"segmentId": item.key, "media": item.media_name, "episode": item.segment.episode}
        try:
            clips = provider.reference_clips([item.segment], client=client, asset=item.asset)
            clip = clips.get(item.key)
            if clip is None:
                entry.update({"status": "no-clip"})
                report.append(entry)
                continue
            episode_env = cached_envelope(item.asset.url, headers=item.asset.headers or None)
            from .align import envelope_of, locate

            match = locate(
                episode_env,
                envelope_of(clip),
                search_start_ms=item.local_start_ms,
                margin_ms=margin_ms,
            )
            entry.update(
                {
                    "status": "ok" if match.score >= 0.3 else "suspect",
                    "score": round(match.score, 4),
                    "predictedMs": item.local_start_ms,
                    "remeasuredMs": match.offset_ms,
                    "deltaMs": match.offset_ms - item.local_start_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, don't abort a batch
            entry.update({"status": "error", "error": str(exc)})
        report.append(entry)
    return report


def load_plan(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarise(plan: Plan) -> str:
    lines = [
        f"word: {plan.word}",
        f"source: {plan.source}",
        f"hits: {plan.stats.get('totalHits')}  usable: {plan.stats.get('usableCandidates')}  "
        f"selected: {plan.stats.get('selected')}  aligned: {plan.stats.get('ok')}",
    ]
    # A mixed reel has to show its mix, or "why is this all anime?" is unanswerable.
    by_category = plan.stats.get("byCategory") or {}
    if by_category:
        lines.append(
            "corpus: " + "  ".join(f"{k.lower()} {v}" for k, v in sorted(by_category.items()))
        )
    lines.append("")
    for index, item in enumerate(plan.items, start=1):
        flag = {"ok": "OK ", "unaligned": "ALN", "unresolved": "SRC", "out-of-range": "RNG"}.get(item.status, "?  ")
        extra = f" (+{item.context_count} context)" if item.context_count else ""
        lines.append(
            f"{index:>2}. [{flag}] {item.category.lower():6s} {item.media_name} "
            f"ep{item.segment.episode} "
            f"scene {item.scene_start_ms}-{item.scene_end_ms} ms -> "
            f"local {item.local_start_ms}-{item.local_end_ms} ms{extra}"
        )
        lines.append(f"      {item.segment.japanese}")
        lines.append(f"      {item.segment.english}")
        if item.note:
            lines.append(f"      note: {item.note}")
    return "\n".join(lines)
