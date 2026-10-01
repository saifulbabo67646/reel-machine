"""The nadeshiko-cut stages: select → acquire → compose → prep → render.

Alignment is not one of them: it is the strategy `compose` uses to build the video
elements, which is why nothing outside this recipe ever sees it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ... import subtitles
from ...config import Settings, normalise_aspect
from ...core.assets import AssetKind, Provenance
from ...core.errors import RenderFailed
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, SourceCut, Timeline, timeline_digest
from . import pipeline as P
from .models import (
    AcquireOutput,
    ClipRecord,
    ComposeOutput,
    CueData,
    ItemRecord,
    NadeshikoCutInputs,
    PrepOutput,
    RenderOutput,
    SelectOutput,
)


def build_timeline(
    items: list[ItemRecord],
    *,
    source_name: str,
    aspect: str,
) -> Timeline:
    """The declarative timeline: one source-cut element per aligned segment."""
    video = []
    sources: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.status != "ok" or item.asset is None:
            continue
        source_id = item.asset.source_id(source_name)
        video.append(
            SourceCut(
                element_id=item.key,
                asset=source_id,
                src_in_ms=item.local_start_ms,
                src_out_ms=item.local_end_ms,
                start_ms=0,  # layout is decided when prep materialises the clips
                keep_audio=True,
            )
        )
        sources[source_id] = {
            "url": item.asset.url,
            "headers": item.asset.headers,
            "audio_track": item.asset.audio_track,
            "duration_ms": item.asset.duration_ms,
            "label": item.source_label,
        }
    duration = sum(element.duration_ms for element in video)
    return Timeline(
        duration_ms=duration,
        aspect=aspect,  # type: ignore[arg-type]  (normalised below)
        render_mode="cut",
        video=video,
        meta={"sources": sources, "prepared": False},
    )


def _timeline_aspect(aspect: str) -> str:
    return "landscape" if aspect == "original" else aspect


class SelectStage:
    """Search the corpus, filter, select, and widen each pick to its scene."""

    id = "select"
    revision = 1
    cacheable = True
    cache_fields = (
        "word",
        "corpus",
        "count",
        "per_media",
        "per_category",
        "content_rating",
        "exact_match",
        "only",
        "dry_run",
    )
    output_model = SelectOutput

    def run(self, ctx: StageContext, payload: Any) -> SelectOutput:
        inputs = NadeshikoCutInputs.model_validate(payload)
        settings: Settings = ctx.config
        client = ctx.providers.get("corpus")
        provider = ctx.providers.get("source")
        items, stats = P.search_and_select(
            client,
            inputs.word,
            settings=settings,
            categories=inputs.categories(),
            per_category=inputs.per_category,
            content_rating=inputs.content_rating,
            exact_match=inputs.exact_match,
            max_segments=inputs.count or settings.max_segments,
            per_media=inputs.per_media,
            only=inputs.only_pairs() or None,
            on_detail=ctx.progress.detail,
        )
        return SelectOutput(
            inputs=inputs,
            source=getattr(provider, "name", "") or "",
            items=items,
            stats=stats,
        )


class AcquireStage:
    """Resolve each episode and fetch the reference audio alignment needs."""

    id = "acquire"
    revision = 1
    cacheable = False  # signed URLs expire; a cached resolve would be a trap
    output_model = AcquireOutput

    def run(self, ctx: StageContext, payload: SelectOutput) -> AcquireOutput:
        if payload.inputs.dry_run:
            return AcquireOutput(
                inputs=payload.inputs,
                items=payload.items,
                stats=payload.stats,
                source=payload.source,
            )
        provider = ctx.providers.get("source")
        client = ctx.providers.get("corpus")
        groups = P.group_items(payload.items)
        episodes, failures = P.acquire_episodes(
            client, provider, groups, on_detail=ctx.progress.detail
        )
        for item in payload.items:
            record = episodes.get(f"{item.segment.mediaPublicId}:{item.segment.episode}")
            if record is not None:
                item.asset = record.asset
        return AcquireOutput(
            inputs=payload.inputs,
            items=payload.items,
            episodes=list(episodes.values()),
            stats=payload.stats,
            source=payload.source,
            failures=failures,
        )


class ComposeStage:
    """Run the alignment strategy and build the timing + timeline declaration."""

    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: AcquireOutput) -> ComposeOutput:
        settings: Settings = ctx.config
        inputs = payload.inputs
        groups = P.group_items(payload.items)
        episodes = {f"{record.media_public_id}:{record.episode}": record for record in payload.episodes}
        items = P.compose_items(
            groups=groups,
            episodes=episodes,
            failures=payload.failures,
            settings=settings,
            dry_run=inputs.dry_run,
            on_detail=ctx.progress.detail,
        )
        plan = P.plan_from_items(inputs.word, payload.source, items, payload.stats)
        aspect = normalise_aspect(inputs.aspect or settings.aspect)
        reel_name = inputs.name or ctx.job.id
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        render_options = {
            "word": inputs.word,
            "source": payload.source,
            "stats": plan.stats,
            "aspect": aspect,
            "watermark": inputs.watermark,
            "outdir": str(outdir),
            "reel_name": reel_name,
            "render_workdir": str(settings.workdir / "render" / reel_name),
            "pre_roll_ms": settings.pre_roll_ms if inputs.pre_roll_ms is None else inputs.pre_roll_ms,
            "post_roll_ms": settings.post_roll_ms if inputs.post_roll_ms is None else inputs.post_roll_ms,
            "credits": sorted({item.source_label for item in items if item.status == "ok"}),
        }
        return ComposeOutput(
            inputs=inputs,
            word=inputs.word,
            source=payload.source,
            plan=plan.to_dict(),
            items=items,
            episodes=payload.episodes,
            timeline=build_timeline(items, source_name=payload.source, aspect=_timeline_aspect(aspect)),
            render_options=render_options,
            halt_pipeline=inputs.mode == "plan",
        )


class PrepStage:
    """Materialise every declarative element: cut, normalise, register as an asset."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> PrepOutput:
        settings: Settings = ctx.config
        options = payload.render_options
        workdir = Path(options["render_workdir"])
        workdir.mkdir(parents=True, exist_ok=True)
        outdir = Path(options["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        items = [item for item in payload.items if item.status == "ok"]
        if not items:
            raise RenderFailed(
                "nothing to render: no segment aligned successfully",
                hint=(
                    "run `reel plan` verbosely to see why — usually the source provider "
                    "cannot find the episode, or the reference clips do not match your copy"
                ),
                stage=self.id,
            )

        steps = P.plan_cuts(
            items,
            settings=settings,
            pre_roll_ms=int(options["pre_roll_ms"]),
            post_roll_ms=int(options["post_roll_ms"]),
            fps=30,
        )
        clip_paths = P.cut_clips(
            steps,
            workdir,
            settings=settings,
            total_items=len(items),
            on_detail=ctx.progress.detail,
        )
        if not clip_paths:
            raise RenderFailed(
                "all clips were trimmed away",
                hint="check pre/post roll and alignment; the mapped windows are too short",
                stage=self.id,
            )

        layouts, cursor_ms, manifest_items = P.build_layout(
            steps, clip_paths, word=options["word"], total_items=len(items)
        )

        replacements: dict[str, Clip] = {}
        cue_payloads: list[CueData] = []
        captions: list[Caption] = []
        for layout in layouts:
            asset = ctx.assets.put(
                layout.clip,
                kind=AssetKind.SOURCE_CUT,
                provenance=Provenance(
                    provider=options["source"],
                    source_id=layout.item.segment.publicId,
                    notes="aligned cut",
                ),
                ext=".mp4",
            )
            replacements[layout.item.segment.publicId] = Clip(
                element_id=layout.item.segment.publicId,
                asset=asset.id,
                start_ms=layout.start_ms,
                duration_ms=layout.duration_ms,
                origin="cut",
                keep_audio=True,
            )
            end_ms = layout.start_ms + layout.duration_ms
            captions.append(
                Caption(
                    start_ms=layout.start_ms,
                    end_ms=end_ms,
                    text=layout.english,
                    lang="en",
                    style_ref="caption.en",
                    meta={"segmentId": layout.item.segment.publicId},
                )
            )
            captions.append(
                Caption(
                    start_ms=layout.start_ms,
                    end_ms=end_ms,
                    text=layout.japanese,
                    lang="ja",
                    style_ref="caption.ja",
                    meta={"segmentId": layout.item.segment.publicId},
                )
            )
            cue_payloads.append(
                CueData(
                    start_ms=layout.start_ms,
                    end_ms=end_ms,
                    japanese=layout.japanese,
                    english=layout.english,
                    word=options["word"],
                    tokens=layout.tokens,
                    marked=layout.marked,
                    source=layout.source_label,
                    index_label=layout.index_label,
                    reading=layout.reading,
                )
            )

        prepared = payload.timeline.materialise(replacements)
        prepared = prepared.model_copy(
            update={
                "captions": captions,
                "duration_ms": cursor_ms,
                "meta": {**prepared.meta, "prepared": True},
            }
        )
        records = [
            ClipRecord(
                index=layout.index,
                item_index=layout.item_index,
                segment_id=layout.item.segment.publicId,
                clip=str(layout.clip),
                clip_asset=replacements[layout.item.segment.publicId].asset,
                duration_ms=layout.duration_ms,
                start_ms=layout.start_ms,
                cue=cue,
            )
            for layout, cue in zip(layouts, cue_payloads)
        ]
        skipped = [item for item in manifest_items if item.get("status") == "skipped"]
        return PrepOutput(
            timeline=prepared,
            clips=records,
            duration_ms=cursor_ms,
            skipped=skipped,
            render_options=options,
            manifest_items=manifest_items,
        )


class RenderStage:
    """One ASS, one SRT, one ffmpeg pass, one manifest."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> RenderOutput:
        settings: Settings = ctx.config
        options = payload.render_options
        aspect = normalise_aspect(options["aspect"] or settings.aspect)
        word = options["word"]
        card = P.build_card(word, settings, ctx.progress.detail)
        cues = P.cues_from_records(payload.clips, word=word, card=card)
        _, _, _, layout = P.render_geometry(aspect)
        metrics = subtitles.measure_metrics(settings.font_ja, layout.ja_size)
        ctx.progress.detail(
            f"  metrics: {settings.font_ja} @{layout.ja_size} -> cjk {metrics.cjk:.3f} em"
        )
        outdir = Path(options["outdir"])
        workdir = Path(options["render_workdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        video = outdir / f"{options['reel_name']}.mp4"
        manifest = P.legacy_manifest(
            video=video,
            word=word,
            source=options["source"],
            aspect=aspect,
            duration_ms=payload.duration_ms,
            manifest_items=payload.manifest_items,
            stats=options["stats"],
            credits=options["credits"],
        )
        result = P.render_reel(
            [Path(record.clip) for record in payload.clips],
            cues,
            settings=settings,
            workdir=workdir,
            outdir=outdir,
            reel_name=options["reel_name"],
            aspect=aspect,
            layout=layout,
            metrics=metrics,
            total_ms=payload.duration_ms,
            watermark=options.get("watermark", ""),
            manifest=manifest,
            keep_clips=False,
        )
        return RenderOutput(
            video=str(result.video),
            ass=str(result.ass),
            srt=str(result.srt),
            manifest=str(result.manifest),
            duration_ms=result.duration_ms,
            render_mode="cut",
            timeline_digest=timeline_digest(payload.timeline),
        )


def ffmpeg_available(settings: Settings) -> bool:
    return shutil.which(settings.ffmpeg) is not None
