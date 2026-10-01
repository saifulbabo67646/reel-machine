"""The quranic recipe: verse reels with no source media and no Nadeshiko.

Surah/Ayah range, reciter audio, Arabic text with word-level highlight sync, translation
overlays, style presets, a background clip or a procedural gradient, and audio mastering
profiles — built from a corpus provider and a style pack, and rendered with one ffmpeg
pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from ...core.stage import StageContext
from ...core.style import StyleNotFound, get_style
from .models import FileBackground, QuranicInputs
from .stages import ComposeStage, PrepStage, RenderStage, SelectStage


class QuranicRecipe:
    spec = RecipeSpec(
        id="quranic",
        title="Quranic verse reel",
        summary=(
            "A Surah/Ayah range becomes a vertical reel: reciter audio, Arabic text with "
            "word-level highlight sync, a translation overlay and a background — no source "
            "video, no Nadeshiko and no alignment."
        ),
        input_model=QuranicInputs,
        version=1,
        stages=(
            StageSpec(stage=SelectStage(), uses=("corpus",)),
            StageSpec(stage=PrepStage(), feeds="select", uses=("corpus",)),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
        providers={
            "corpus": ProviderReq(
                group="corpora",
                default="quran_com",
                description="Verse text, translation, recitation audio and word timings",
            )
        },
        cost=CostNote(
            unit="corpus_requests",
            estimate="2-4 per job",
            notes="the Quran corpus APIs are free; no Nadeshiko quota is touched",
            providers=("quran_com", "alquran_cloud"),
        ),
        artifacts=("reel.mp4", "captions.ass", "captions.srt", "timeline.json", "manifest.json"),
        render_modes=("quranic",),
        example={
            "surah": 1,
            "ayah_start": 1,
            "ayah_end": 3,
            "reciter": "alafasy",
            "translation": "en.sahih",
            "style": "quranic.parchment",
        },
    )

    def probe(self, inputs: QuranicInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        start, end = inputs.ayah_range()
        range_ok = 1 <= start <= end <= 286
        checks.append(
            CheckResult(
                name="ayah_range",
                ok=range_ok,
                detail=f"{inputs.surah}:{start}-{end}" if range_ok else "ayah numbers run 1..286",
            )
        )
        translation_ok = inputs.show_translation is False or bool(inputs.translation)
        checks.append(
            CheckResult(
                name="translation",
                ok=translation_ok,
                detail=inputs.translation or "none requested",
            )
        )
        try:
            style = get_style(inputs.style)
            checks.append(CheckResult(name="style", ok=True, detail=style.id))
        except StyleNotFound as exc:
            checks.append(CheckResult(name="style", ok=False, detail=str(exc)))

        background_detail = "procedural gradient"
        background_ok = True
        if isinstance(inputs.background, FileBackground):
            path = Path(inputs.background.path).expanduser()
            background_ok = path.is_file()
            background_detail = str(path) if background_ok else f"missing: {path}"
        checks.append(CheckResult(name="background", ok=background_ok, detail=background_detail))

        corpus = ctx.providers.get("corpus") if ctx.providers else None
        checks.append(
            CheckResult(
                name="corpus",
                ok=corpus is not None,
                detail=getattr(corpus, "name", "") or "none configured",
            )
        )
        return ProbeReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            quota_free=True,
            details={
                "surah": inputs.surah,
                "ayahs": [start, end],
                "corpus": inputs.corpus,
                "requested_corpus": inputs.corpus,
            },
        )

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport | None:
        compose = result.get("compose")
        prep = result.get("prep")
        render = result.get("render")
        if compose is None or prep is None or render is None:
            return None
        timeline = compose.timeline
        checks: list[CheckResult] = []
        ayah_captions = [caption for caption in timeline.captions if caption.lang == "ar"]
        checks.append(
            CheckResult(
                name="ayahs",
                ok=len(ayah_captions) == len(prep.ayah_windows),
                detail=f"{len(ayah_captions)} Arabic caption(s) for {len(prep.ayah_windows)} ayah(s)",
            )
        )
        if compose.inputs.word_highlight:
            missing = [caption.meta.get("source") for caption in ayah_captions if not caption.words]
            checks.append(
                CheckResult(
                    name="word_highlight",
                    ok=not missing,
                    detail=(
                        f"all ayahs carry word spans ({prep.timings_source})"
                        if not missing
                        else f"no word spans for {', '.join(str(m) for m in missing)}"
                    ),
                )
            )
        checks.append(
            CheckResult(
                name="render_mode",
                ok=render.render_mode == "quranic" == timeline.render_mode,
                detail=render.render_mode,
            )
        )
        video = Path(render.video)
        checks.append(
            CheckResult(
                name="output",
                ok=video.is_file() and video.stat().st_size > 0,
                detail=str(video),
            )
        )
        checks.append(
            CheckResult(
                name="duration",
                ok=abs(render.duration_ms - prep.audio_duration_ms) <= 1000,
                detail=f"{render.duration_ms} ms vs {prep.audio_duration_ms} ms of recitation",
            )
        )
        degradations: list[str] = []
        if prep.timings_source == "proportional":
            degradations.append("word timings are proportional (the provider served none)")
        return VerificationReport(
            ok=all(check.ok for check in checks),
            checks=checks,
            render_mode="quranic",
            visible_degradation=degradations,
        )
