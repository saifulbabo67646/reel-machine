"""dialoguereel stages: script → cast → narration → compose → preview → render.

`cast` mode stops at the preview with a still of the two characters, `storyboard` with a
sheet of stills (the hook, the listen pass, each line once its meaning shows, the recap and
the quiz); `full` renders the reel.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, ReelError, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, silence, synth_sfx
from ..explainer3d.stages import speak_line
from ..explainer3d.video import mux
from . import cast as casts
from . import compose, images, lint, post
from .keys import match_keys
from .models import (CastSpec, ComposeOutput, DialogueReelInputs, LineWindowModel, NarrationOutput, PreviewOutput,
                     RenderOutput, ScriptOutput)
from .ruby import parse_ruby, plain_text
from .schedule import HOOK_MS, SLOW_RATE, Cue, LineWindow, QuizWindow, Schedule, quiz_line_for, schedule

DROP_INS_MS = (150, 300)   # the two characters land: one pop each
COUNTDOWN_MS = (0, 1000, 2000)   # the quiz countdown: one pop per number, the first as the ring appears
CAST_MS = compose.CAST_SECONDS * 1000


def sfx_cues(plan: Schedule) -> list[tuple[int, str]]:
    """When each sound effect plays: a pop per character drop-in, a whoosh into the bridge and the recap, and in the
    quiz a pop on each countdown number (the first as the ring and its 3 appear, runtime RING_IN_MS) and a ding at
    the reveal."""
    cues = [(at, "pop") for at in DROP_INS_MS]
    cues += [(at, "whoosh") for at in (plan.bridge_ms, plan.recap_ms) if at is not None]
    if plan.quiz:
        cues += [(plan.quiz.start_ms + at, "pop") for at in COUNTDOWN_MS]
        cues.append((plan.quiz.reveal_ms, "ding"))
    return cues


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def finding_path(finding: lint.Finding) -> str:
    """Where in the inputs a lint finding points."""
    if finding.rule == "no-title":
        return "title"
    if finding.rule in ("line-count", "length", "key-count"):
        return "lines"
    if finding.rule == "quiz-line":
        return "quiz"
    if finding.line is None:
        return ""
    if finding.key is not None:
        return f"lines[{finding.line}].keys[{finding.key}]"
    field = {"unknown-speaker": "speaker", "same-speaker": "speaker", "ruby": "text", "long-line": "text",
             "no-reading": "reading"}.get(finding.rule)
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


def _one_line(text: str) -> str:
    """Runs of CR/LF (and so blank lines, which end an SRT cue) collapsed to one space; nothing else changes."""
    return re.sub(r"[\r\n]+", " ", text)


def caption_text(text: str, meaning: str) -> str:
    """A line's caption: its plain text over its meaning, each on exactly one line."""
    return _one_line(plain_text(parse_ruby(text)[0])) + "\n" + _one_line(meaning)


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
            spec = casts.load(root, inputs.cast) if isinstance(inputs.cast, str) else inputs.cast
            resolved = casts.resolve(spec)
            if not isinstance(inputs.cast, str):
                casts.save(root, spec)  # a checked inline cast becomes reusable by its id
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


class CastStage:
    """Image characters get their poses (drawn, fetched, or reused from disk); SVG characters pass through."""

    id = "cast"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> ScriptOutput:
        cast = payload.cast
        if all(c.kind != "image" for c in cast.characters):
            return payload
        root = casts.caller_root(ctx)
        spec = casts.load(root, payload.inputs.cast) if isinstance(payload.inputs.cast, str) else payload.inputs.cast
        allow_local = ctx.job.caller in ("", "local")
        characters, warnings = [], list(payload.warnings)
        for index, (char, source) in enumerate(zip(cast.characters, (c.source for c in spec.characters),
                                                   strict=True)):
            if char.kind != "image":
                characters.append(char)
                continue
            ctx.cancel.raise_if_cancelled()
            ledger = images.quota_ledger(root, "openai") if source.provider == "openai" else None
            try:
                made, problems = images.make_poses(source, casts.cast_folder(root, cast.id, char.id),
                                                   settings=ctx.config, allow_local=allow_local, ledger=ledger,
                                                   path=f"cast.characters[{index}].source")
            except ReelError as exc:
                exc.stage = self.id
                raise
            warnings += problems
            characters.append(char.model_copy(update={
                "poses": {pose: str(file.resolve()) for pose, file in made.items()},
                "parts": [pose for pose in images.POSES if pose in made]}))
        if payload.inputs.mode == "cast":
            for char in characters:
                for pose, file in char.poses.items():
                    shutil.copy2(file, _artifacts(ctx) / f"cast-{char.id}-{pose}.png")
        resolved = cast.model_copy(update={"characters": characters})
        return payload.model_copy(update={"cast": resolved, "warnings": warnings})


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
        quiz_line = quiz_line_for(inputs)
        plan = schedule([ms for _, ms, _ in spoken], slow=[line.slow for line in inputs.lines],
                        listen_first=inputs.flow == "listen_first", recap=any(line.keys for line in inputs.lines),
                        quiz_line=quiz_line if quiz_line is not None and 0 <= quiz_line < len(spoken) else None)
        parts = self._track(ctx, plan, spoken, work)
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
            lines=[_window_model(w) for w in plan.lines], listen=[_window_model(w) for w in plan.listen],
            bridge_ms=plan.bridge_ms, recap_ms=plan.recap_ms,
            quiz=dataclasses.asdict(plan.quiz) if plan.quiz else None,
            words=[words for _, _, words in spoken], provider=name)

    def _track(self, ctx: StageContext, plan: Schedule, spoken: list[tuple[Path, int, list[WordSpan]]],
               work: Path) -> list[Path]:
        """The voice track's pieces, walking the cues: silence up to each cue, then its clip; then the tail."""
        settings = ctx.config
        slow: dict[int, Path] = {}   # each slow line's 0.75x copy, made once
        parts: list[Path] = []
        cursor = 0
        for n, cue in enumerate(plan.cues):
            ctx.cancel.raise_if_cancelled()
            src, ms, _ = spoken[cue.line]
            if cue.rate != 1.0:
                if cue.line not in slow:
                    slow[cue.line] = work / f"{cue.line:03d}.slow.wav"
                    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src),
                                "-af", f"atempo={cue.rate}", str(slow[cue.line])], cancel=ctx.cancel)
                src, ms = slow[cue.line], round(ms / cue.rate)
            lead = cue.at_ms - cursor
            parts.append(pad_speech(src, work / f"cue{n:03d}.wav", lead_ms=lead, total_ms=lead + ms,
                                    settings=settings, cancel=ctx.cancel))
            cursor = cue.at_ms + ms
        if plan.duration_ms > cursor:
            parts.append(silence(work / "tail.wav", total_ms=plan.duration_ms - cursor, settings=settings,
                                 cancel=ctx.cancel))
        return parts


def _window_model(w: LineWindow) -> LineWindowModel:
    return LineWindowModel(start_ms=w.start_ms, speech_ms=w.speech_ms, end_ms=w.end_ms, meaning_ms=w.meaning_ms,
                           slow_start_ms=w.slow_start_ms, slow_ms=w.slow_ms)


def _window(w: LineWindowModel) -> LineWindow:
    return LineWindow(w.start_ms, w.speech_ms, w.end_ms, w.meaning_ms, w.slow_start_ms, w.slow_ms)


def plan_of(narration: NarrationOutput) -> Schedule:
    """The narration's `Schedule` again, cues included (they follow from the windows, in schedule order)."""
    lines = tuple(_window(w) for w in narration.lines)
    listen = tuple(_window(w) for w in narration.listen)
    quiz = QuizWindow(**narration.quiz) if narration.quiz else None
    cues = [Cue(w.start_ms, i, 1.0) for i, w in enumerate(listen)]
    for i, w in enumerate(lines):
        cues.append(Cue(w.start_ms, i, 1.0))
        if w.slow_start_ms is not None:
            cues.append(Cue(w.slow_start_ms, i, SLOW_RATE))
    if quiz:
        cues.append(Cue(quiz.reveal_ms, quiz.line, 1.0))
    return Schedule(HOOK_MS, lines, narration.duration_ms, listen, narration.bridge_ms, narration.recap_ms, quiz,
                    tuple(cues))


def _camel(quiz: QuizWindow | None) -> dict[str, int] | None:
    if quiz is None:
        return None
    return {"line": quiz.line, "startMs": quiz.start_ms, "revealMs": quiz.reveal_ms, "endMs": quiz.end_ms}


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ComposeOutput:
        script = payload.script
        inputs, cast = script.inputs, script.cast
        plan = plan_of(payload)
        if inputs.mode == "cast":
            doc = compose.cast_document(cast, inputs.look)
        else:
            doc = compose.reel_document(inputs, cast, plan, payload.words)
        project = compose.write_project(doc, cast, ctx.workdir / "project")
        lines = []
        for index, line in enumerate(doc["lines"]):   # a cast sheet has none
            source = inputs.lines[index]
            keys = [{"index": k.index, "start": k.start, "end": k.end} if k else None
                    for k in match_keys(source.text, source.keys)]
            lines.append({"speaker": line["speaker"], "startMs": line["startMs"], "endMs": line["endMs"],
                          "meaningMs": line["meaningMs"], "words": line["words"], "keys": keys})
        resolved = {
            "cast": cast.id, "durationMs": payload.duration_ms, "lines": lines,
            "sections": {"listen": [{"line": i, "startMs": w.start_ms, "endMs": w.end_ms}
                                    for i, w in enumerate(plan.listen)],
                         "bridgeMs": plan.bridge_ms, "recapMs": plan.recap_ms, "quiz": _camel(plan.quiz)},
            "warnings": script.warnings,
        }
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8")
        if inputs.mode != "cast":
            (_artifacts(ctx) / "post.md").write_text(post.post_markdown(inputs, cast), encoding="utf-8")
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
        plan = plan_of(payload.narration)
        times = [HOOK_MS * 0.8]
        if plan.listen:
            times.append((plan.listen[0].start_ms + plan.listen[-1].end_ms) / 2)
        times += [w.meaning_ms + 300 for w in plan.lines]
        if plan.recap_ms is not None:
            times.append(plan.recap_ms + 1500)
        if plan.quiz:
            times += [plan.quiz.start_ms + 1500, plan.quiz.reveal_ms + 300]
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

        cached = _caller_cache(ctx) / "reels" / renderer.name / composed.key[:2] / f"{composed.key}.mp4"
        if not cached.is_file():
            partial = cached.with_name(f"{composed.key}.partial.mp4")
            partial.parent.mkdir(parents=True, exist_ok=True)
            renderer.render(Path(composed.project), partial, fps=compose.FPS, seconds=total_ms / 1000,
                            cancel=ctx.cancel)
            partial.replace(cached)
        plan = plan_of(narration)
        sfx = {n: synth_sfx(n, work / "sfx" / f"{n}.wav", settings=settings, designed=True)
               for n in ("pop", "whoosh", "ding")}
        cues = [(at, sfx[name]) for at, name in sfx_cues(plan)]
        audio = mix(ctx.assets.get(narration.audio_asset), cues, None,
                    work / "audio.wav", total_ms=total_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(cached, audio, outdir / f"{name}.mp4", total_ms=total_ms, settings=settings, cancel=ctx.cancel)

        def caption(line: Any, start_ms: int, end_ms: int) -> Caption:
            return Caption(start_ms=start_ms, end_ms=end_ms, text=caption_text(line.text, line.meaning),
                           lang=inputs.language, style_ref="dialoguereel.caption", meta={"speaker": line.speaker})

        # each dialogue line (through its slow replay), then the quiz answer from the reveal; the listen pass has none
        captions = [caption(line, w.start_ms, w.slow_start_ms + w.slow_ms if w.slow_start_ms is not None else w.end_ms)
                    for line, w in zip(inputs.lines, plan.lines, strict=True)]
        if plan.quiz:
            captions.append(caption(inputs.lines[plan.quiz.line], plan.quiz.reveal_ms, plan.quiz.end_ms))
        srt = write_srt(captions, work / f"{name}.srt")
        manifest = {"reel": str(video), "recipe": "dialoguereel", "mode": inputs.mode,
                    "created": datetime.now(timezone.utc).isoformat(), "durationMs": total_ms,
                    "cast": script.cast.id, "look": inputs.look, "renderer": getattr(renderer, "name", ""),
                    "credits": credits(script)}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path), duration_ms=total_ms)
