"""dialoguereel stages: script → narration → compose → preview → render.

`cast` mode stops at the preview with a still of the two characters, `storyboard` with a
sheet of stills (the hook and each line once its meaning shows); `full` renders the reel.
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, silence, synth_sfx
from ..explainer3d.stages import speak_line
from ..explainer3d.video import mux
from . import cast as casts
from . import compose, lint
from .models import (CastSpec, ComposeOutput, DialogueReelInputs, LineWindowModel, NarrationOutput, PreviewOutput,
                     RenderOutput, ScriptOutput)
from .ruby import parse_ruby, plain_text
from .schedule import GAP_MS, HOOK_MS, TAIL_MS, LineWindow, Schedule, schedule

DROP_INS_MS = (150, 300)   # the two characters land: one pop each
CAST_MS = compose.CAST_SECONDS * 1000


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def finding_path(finding: lint.Finding) -> str:
    """Where in the inputs a lint finding points."""
    if finding.rule == "no-title":
        return "title"
    if finding.rule == "line-count":
        return "lines"
    if finding.line is None:
        return ""
    field = {"unknown-speaker": "speaker", "ruby": "text"}.get(finding.rule)
    return f"lines[{finding.line}].{field}" if field else f"lines[{finding.line}]"


def lint_findings(inputs: DialogueReelInputs, speakers: set[str]) -> list[dict[str, Any]]:
    """The lint's findings as data, each with the path it points at."""
    out = []
    for finding in lint.check(inputs, speakers):
        item = {"rule": finding.rule, "severity": finding.severity, "message": finding.message,
                "hint": finding.hint, "line": finding.line, "path": finding_path(finding)}
        out.append(item)
    return out


def lint_blocks(inputs: DialogueReelInputs) -> bool:
    """Lint errors stop only a strict full build; anywhere else they are suggestions."""
    return inputs.mode == "full" and inputs.lint == "strict"


def check_voices(spec: CastSpec | Any, bound: str) -> list[str]:
    """The characters whose voice needs a narration provider other than the bound one."""
    return [f"{c.id}: {c.voice!r} needs {provider_for(c.voice)!r}" for c in spec.characters
            if provider_for(c.voice) != bound]


def credits(script: ScriptOutput) -> list[str]:
    return [f"Voice of {c.name}: {c.voice}" for c in script.cast.characters]


def _caller_cache(ctx: StageContext) -> Path:
    """Rendered reels are kept per caller (beside its casts), so a re-run reuses one."""
    return casts.caller_root(ctx) / "cache" / "dialoguereel"


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = DialogueReelInputs.model_validate(payload)
        root = casts.caller_root(ctx)
        try:
            if isinstance(inputs.cast, str):
                spec = casts.load(root, inputs.cast)
            else:
                spec = inputs.cast
                casts.save(root, spec)  # an inline cast becomes reusable by its id
            resolved = casts.resolve(spec)
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        found = lint_findings(inputs, {c.id for c in resolved.characters})
        if lint_blocks(inputs):
            for finding in found:
                if finding["severity"] == "error":
                    raise InvalidInput(finding["message"], hint=finding["hint"], stage=self.id,
                                       details={"path": finding["path"], "rule": finding["rule"]})
        warnings = [f"{f['rule']}: {f['message']}" for f in found]
        return ScriptOutput(inputs=inputs, cast=resolved, lint=found, warnings=warnings)


class NarrationStage:
    """One TTS call per line, in its speaker's voice, laid on the reel's schedule."""

    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> NarrationOutput:
        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if inputs.mode == "cast":
            return NarrationOutput(script=payload, duration_ms=CAST_MS, provider=name)
        # a speaker not in the cast is a lint error that only a strict full build refuses; elsewhere the
        # line is read in the first character's voice (the runtime shows it on the left, with nobody talking)
        fallback = payload.cast.characters[0].voice
        voices = {line.speaker: fallback for line in inputs.lines} | {c.id: c.voice for c in payload.cast.characters}
        for line in inputs.lines:
            voice = voices[line.speaker]
            if provider_for(voice) != name:
                raise VoiceUnavailable(
                    f"the voice {voice!r} of {line.speaker!r} needs the {provider_for(voice)!r} narration provider, "
                    f"but this deployment serves {name!r}",
                    hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment", stage=self.id)
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        spoken: list[tuple[Path, int, list[WordSpan]]] = []
        licence = None
        for index, line in enumerate(inputs.lines):
            ctx.cancel.raise_if_cancelled()
            text = plain_text(parse_ruby(line.text)[0])
            path, ms, words, line_licence = speak_line(ctx, provider, text, voices[line.speaker], inputs.language)
            local = work / f"{index:03d}.wav"
            shutil.copy2(path, local)
            spoken.append((local, ms, list(words)))
            licence = line_licence or licence
        plan = schedule([ms for _, ms, _ in spoken])
        parts = [silence(work / "hook.wav", total_ms=plan.hook_ms, settings=settings, cancel=ctx.cancel)]
        for index, (path, ms, _) in enumerate(spoken):
            last = index == len(spoken) - 1
            parts.append(pad_speech(path, work / f"{index:03d}.padded.wav", lead_ms=0,
                                    total_ms=ms + (TAIL_MS if last else GAP_MS), settings=settings,
                                    cancel=ctx.cancel))
        joined = ctx.workdir / "narration.wav"
        if len(parts) == 1:
            shutil.copy2(parts[0], joined)
        else:
            ffmpeg.concat_audio(parts, joined, cancel=ctx.cancel)
        voices_used = ",".join(sorted({voices[line.speaker] for line in inputs.lines}))
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=voices_used), ext=".wav")
        return NarrationOutput(
            script=payload, audio_asset=asset.id, duration_ms=plan.duration_ms,
            lines=[LineWindowModel(start_ms=w.start_ms, speech_ms=w.speech_ms, end_ms=w.end_ms,
                                   meaning_ms=w.meaning_ms) for w in plan.lines],
            words=[words for _, _, words in spoken], provider=name)


def plan_of(narration: NarrationOutput) -> Schedule:
    return Schedule(HOOK_MS, tuple(LineWindow(w.start_ms, w.speech_ms, w.end_ms, w.meaning_ms)
                                   for w in narration.lines), narration.duration_ms)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ComposeOutput:
        script = payload.script
        inputs, cast = script.inputs, script.cast
        if inputs.mode == "cast":
            doc = compose.cast_document(cast, inputs.look)
        else:
            doc = compose.reel_document(inputs, cast, plan_of(payload), payload.words)
        project = compose.write_project(doc, cast, ctx.workdir / "project")
        resolved = {
            "cast": cast.id, "durationMs": payload.duration_ms,
            "lines": [{"speaker": line["speaker"], "startMs": line["startMs"], "endMs": line["endMs"],
                       "meaningMs": line["meaningMs"], "words": line["words"]} for line in doc["lines"]],
            "warnings": script.warnings,
        }
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8")
        return ComposeOutput(narration=payload, project=str(project), key=compose.project_key(doc, cast))


def _renderer(ctx: StageContext, stage: str) -> Any:
    renderer = ctx.providers.get("renderer")
    problems = renderer.missing()
    if problems:
        raise RendererUnavailable("the reel renderer is not usable on this deployment",
                                  hint="; ".join(problems), stage=stage)
    return renderer


class PreviewStage:
    """Stills before any render: the cast sheet, or the storyboard. A full build passes through."""

    id = "preview"
    revision = 1
    cacheable = False
    output_model = PreviewOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> PreviewOutput:
        mode = payload.narration.script.inputs.mode
        if mode == "full":
            return PreviewOutput(compose=payload)
        renderer = _renderer(ctx, self.id)
        project = Path(payload.project)
        if mode == "cast":
            sheet = renderer.snapshot(project, 1.0, _artifacts(ctx) / "cast.png", cancel=ctx.cancel)
        else:
            sheet = self._storyboard(ctx, payload, renderer, ctx.config)
        return PreviewOutput(compose=payload, sheet=str(sheet), halt_pipeline=True)

    def _storyboard(self, ctx: StageContext, payload: ComposeOutput, renderer: Any, settings: Settings) -> Path:
        project = Path(payload.project)
        times = [HOOK_MS * 0.8] + [w.meaning_ms + 300 for w in payload.narration.lines]
        stills = ctx.workdir / "stills"
        frames = [renderer.snapshot(project, ms / 1000, stills / f"{index:03d}.png", cancel=ctx.cancel)
                  for index, ms in enumerate(times)]
        cols = min(4, len(frames))
        rows = math.ceil(len(frames) / cols)
        # number the stills 1..n and fill the grid with white cells so `tile` emits it once
        sheet_in = ctx.workdir / "sheet"
        sheet_in.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames, start=1):
            shutil.copy2(frame, sheet_in / f"{index:03d}.png")
        for index in range(len(frames) + 1, cols * rows + 1):
            ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                        "-i", f"color=white:size={compose.WIDTH}x{compose.HEIGHT}", "-frames:v", "1",
                        str(sheet_in / f"{index:03d}.png")], cancel=ctx.cancel)
        sheet = _artifacts(ctx) / "storyboard.jpg"
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-framerate", "1", "-start_number", "1",
                    "-i", str(sheet_in / "%03d.png"), "-vf", f"scale=360:640,tile={cols}x{rows}:padding=8:color=white",
                    "-frames:v", "1", "-q:v", "3", str(sheet)], cancel=ctx.cancel)
        return sheet


class RenderStage:
    """The full reel: the project rendered once (cached by its key), the voices, pops and captions."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: PreviewOutput) -> RenderOutput:
        settings: Settings = ctx.config
        composed = payload.compose
        narration = composed.narration
        script = narration.script
        inputs = script.inputs
        renderer = _renderer(ctx, self.id)
        total_ms = narration.duration_ms
        work = ctx.workdir / "work"
        work.mkdir(parents=True, exist_ok=True)
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        outdir.mkdir(parents=True, exist_ok=True)
        name = inputs.name or ctx.job.id

        cached = _caller_cache(ctx) / "reels" / composed.key[:2] / f"{composed.key}.mp4"
        if not cached.is_file():
            partial = cached.with_name(f"{composed.key}.partial.mp4")
            partial.parent.mkdir(parents=True, exist_ok=True)
            renderer.render(Path(composed.project), partial, fps=compose.FPS, seconds=total_ms / 1000,
                            cancel=ctx.cancel)
            partial.replace(cached)
        pop = synth_sfx("pop", work / "sfx" / "pop.wav", settings=settings, designed=True)
        audio = mix(ctx.assets.get(narration.audio_asset), [(at, pop) for at in DROP_INS_MS], None,
                    work / "audio.wav", total_ms=total_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(cached, audio, outdir / f"{name}.mp4", total_ms=total_ms, settings=settings, cancel=ctx.cancel)

        captions = [Caption(start_ms=w.start_ms, end_ms=w.end_ms,
                            text=plain_text(parse_ruby(line.text)[0]) + "\n" + line.meaning,
                            lang=inputs.language, style_ref="dialoguereel.caption",
                            meta={"speaker": line.speaker})
                    for line, w in zip(inputs.lines, narration.lines, strict=True)]
        srt = write_srt(captions, work / f"{name}.srt")
        manifest = {"reel": str(video), "recipe": "dialoguereel", "mode": inputs.mode,
                    "created": datetime.now(timezone.utc).isoformat(), "durationMs": total_ms,
                    "cast": script.cast.id, "look": inputs.look, "renderer": getattr(renderer, "name", ""),
                    "credits": credits(script)}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path), duration_ms=total_ms)
