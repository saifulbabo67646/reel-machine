"""techreel stages: script → narration → compose → preview → render.

`storyboard` stops at the preview with a sheet of stills (each beat's middle and its last visual
landing); `full` renders the reel, mixes the voice with the sound effects and writes the captions.
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, WordSpan
from ...render.captions import write_srt
from ..dialoguereel.verify import parse_silences
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, silence, synth_sfx
from ..explainer3d.stages import speak_line
from ..explainer3d.video import mux
from . import compose, lint
from .markup import is_unspaced, units
from .models import (
    BeatSlotModel,
    ComposeOutput,
    LineSlotModel,
    NarrationOutput,
    PreviewOutput,
    RenderOutput,
    ScriptOutput,
    TechReelInputs,
)
from .schedule import BeatSlot, LineSlot, Plan, schedule
from .words import unit_times

COUNTER_MS = 900          # runtime/pure.mjs COUNTER_MS: a counter lands this long after it enters
TYPE_CPS = 28             # runtime/pure.mjs TYPE_CPS
TERM_CMD_GAP_MS = 250     # runtime/pure.mjs: the pause before the next command starts typing
MAX_SFX = 80
# what goes first when a reel has too many cues: the pops are the most and the least missed
_DROP_ORDER = ("pop", "tick", "ding", "thud", "whoosh")
MAX_STILLS = 24
STILL_AFTER_MS = 400      # a visual's enter animation has played by then (a counter's count by COUNTER_MS)
TRIM_DB = -45             # quieter than this at a clip's ends is padding, not speech
TRIM_MIN_S = 0.05         # silencedetect's shortest silence: a lead shorter than this stays


def trim_clip(src: Path, dest: Path, *, settings: Settings, keep_ms: int = 60,
              cancel: Any = None) -> tuple[Path, int, int]:
    """`src` without its leading and trailing silence, `keep_ms` of it kept at each end: (the clip, the ms cut
    from its start, its new length). TTS voices pad a line with half a second or more each side, which piles up
    as dead air between lines. A clip with no silence at either end (or nothing but silence) is copied as it is."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = round(ffmpeg.probe(src).duration_s * 1000)
    proc = ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(src), "-af",
                       f"silencedetect=n={TRIM_DB}dB:d={TRIM_MIN_S}", "-f", "null", "-"], cancel=cancel)
    silences = parse_silences(proc.stderr or "", total)
    first = next((e for s, e in silences if s <= 10), 0)               # sound starts where a silence at 0 ends
    last = next((s for s, e in reversed(silences) if e >= total - 10), total)
    start, end = max(0, first - keep_ms), min(total, last + keep_ms)
    if total <= 2 * keep_ms or end <= start or (start == 0 and end == total):
        shutil.copy2(src, dest)
        return dest, 0, total
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src), "-af",
                f"atrim=start={start / 1000:.3f}:end={end / 1000:.3f},asetpts=PTS-STARTPTS", str(dest)],
               cancel=cancel)
    return dest, start, end - start


def shift_words(words: list[WordSpan], lead_ms: int, total_ms: int) -> list[WordSpan]:
    """Word spans moved `lead_ms` earlier (the trimmed lead) and held inside the trimmed clip."""
    def clamp(ms: int) -> int:
        return min(max(ms - lead_ms, 0), total_ms)

    return [w.model_copy(update={"start_ms": clamp(w.start_ms), "end_ms": clamp(w.end_ms)}) for w in words]


def terminal_starts(v: dict[str, Any]) -> list[int]:
    """When each terminal line appears, as runtime/pure.mjs `terminalStarts` decides it: the first command at the
    visual's enter, each later one 250 ms after the previous finished typing, an `out` line once the command before
    it finished (at the enter when none did). Lengths in code points, like the runtime's `[...cmd]`."""
    out: list[float] = []
    typed_at: float | None = None
    for line in v.get("lines") or []:
        if line.get("cmd") is not None:
            start = v["enterMs"] if typed_at is None else typed_at + TERM_CMD_GAP_MS
            typed_at = start + len(line["cmd"]) * 1000 / TYPE_CPS
            out.append(start)
        else:
            out.append(v["enterMs"] if typed_at is None else typed_at)
    return [round(ms) for ms in out]


def _cap(cues: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """At most MAX_SFX cues: drop by `_DROP_ORDER`, within a kind the one closest to another cue first (it is the
    one least heard on its own)."""
    cues = sorted(cues)

    def crowding(i: int) -> tuple[float, float, int]:
        # nearest neighbour first, then both neighbours (so an even run thins out evenly, not from one end)
        before = cues[i][0] - cues[i - 1][0] if i > 0 else math.inf
        after = cues[i + 1][0] - cues[i][0] if i + 1 < len(cues) else math.inf
        return min(before, after), before + after, cues[i][0]

    for name in _DROP_ORDER:
        while len(cues) > MAX_SFX:
            candidates = [i for i, (_, n) in enumerate(cues) if n == name]
            if not candidates:
                break
            del cues[min(candidates, key=crowding)]
    return cues


def sfx_cues(doc: dict[str, Any]) -> list[tuple[int, str]]:
    """When each sound effect plays, from the reel document: a whoosh into every beat but the first, a pop as each
    block visual enters, a thud per stamp, a ding as each counter lands and a tick as each terminal command starts
    typing. None when the reel turns sound effects off."""
    if not doc.get("sfx", True):
        return []
    cues: list[tuple[int, str]] = []
    for b, beat in enumerate(doc["beats"]):
        if b:
            cues.append((beat["startMs"], "whoosh"))
        for v in beat["visuals"]:
            if v["class"] == "block":
                cues.append((v["enterMs"], "pop"))
            if v["kind"] == "stamp":
                cues.append((v["enterMs"], "thud"))
            elif v["kind"] == "counter":
                cues.append((v["enterMs"] + COUNTER_MS, "ding"))
            elif v["kind"] == "terminal":
                cues += [(at, "tick") for at, line in zip(terminal_starts(v), v["lines"], strict=True)
                         if line.get("cmd") is not None]
    return _cap(cues)


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _caller_cache(ctx: StageContext) -> Path:
    """Rendered reels are kept per caller, beside the engine's stage cache, so a re-run reuses one; outside the
    engine, under the cache dir."""
    settings: Settings = ctx.config
    job_id = getattr(ctx.job, "id", "")
    root = settings.workdir.parent if job_id and settings.workdir.name == job_id else settings.cache_dir
    return root / "cache" / "techreel"


def lint_findings(inputs: TechReelInputs, bound: str | None = None) -> list[dict[str, Any]]:
    """The lint's findings as data."""
    return [{"rule": f.rule, "severity": f.severity, "message": f.message, "hint": f.hint, "path": f.path}
            for f in lint.check(inputs, bound)]


def bound_provider(ctx: Any, role: str) -> Any:
    """The provider bound to `role` on a stage or probe context, None when nothing is."""
    try:
        return ctx.providers.get(role) if ctx.providers else None
    except KeyError:
        return None


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = TechReelInputs.model_validate(payload)
        found = lint_findings(inputs, getattr(bound_provider(ctx, "narration"), "name", None))
        if lint.blocks(inputs):
            for finding in found:
                if finding["severity"] == "error":
                    raise InvalidInput(finding["message"], hint=finding["hint"], stage=self.id,
                                       details={"path": finding["path"], "rule": finding["rule"]})
        warnings = [f"{f['rule']}: {f['message']}" for f in found]
        return ScriptOutput(inputs=inputs, lint=found, warnings=warnings)


def plan_of(narration: NarrationOutput) -> Plan:
    """The narration's `Plan` again."""
    beats = tuple(BeatSlot(b.start_ms, b.end_ms, tuple(LineSlot(ln.start_ms, ln.speech_ms, ln.end_ms)
                                                       for ln in b.lines))
                  for b in narration.beats)
    return Plan(beats, narration.duration_ms)


def document(narration: NarrationOutput) -> dict[str, Any]:
    """The reel document the narration's timings give (compose, the storyboard and the mix all read it)."""
    times = [[list(line) for line in beat] for beat in narration.times]
    return compose.reel_document(narration.script.inputs, plan_of(narration), times)


class NarrationStage:
    """One TTS call per line in the reel's voice, laid on the beat schedule as one continuous track."""

    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> NarrationOutput:
        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if provider_for(inputs.voice) != name:
            raise VoiceUnavailable(
                f"the voice {inputs.voice!r} needs the {provider_for(inputs.voice)!r} narration provider, "
                f"but this deployment serves {name!r}",
                hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment", stage=self.id)
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        clips: list[list[Path]] = []
        speech: list[list[int]] = []
        times: list[list[list[tuple[int, int]]]] = []
        licence = None
        for b, beat in enumerate(inputs.beats):
            clips.append([])
            speech.append([])
            times.append([])
            for n, line in enumerate(beat.lines):
                ctx.cancel.raise_if_cancelled()
                path, _, words, line_licence = speak_line(ctx, provider, line.say, inputs.voice, inputs.language)
                # the cached clip stays as spoken; the reel gets it without the voice's padding
                local, lead, ms = trim_clip(path, work / f"{b:02d}-{n:02d}.wav", settings=settings,
                                            cancel=ctx.cancel)
                words = shift_words(list(words), lead, ms)
                clips[-1].append(local)
                speech[-1].append(ms)
                times[-1].append(unit_times(list(words), units(line.caption, inputs.language), ms))
                licence = line_licence or licence
        plan = schedule(inputs.beats, speech)
        parts: list[Path] = []
        cursor = 0
        for b, slot in enumerate(plan.beats):
            for n, line_slot in enumerate(slot.lines):
                ctx.cancel.raise_if_cancelled()
                lead = line_slot.start_ms - cursor
                parts.append(pad_speech(clips[b][n], work / f"part{b:02d}-{n:02d}.wav", lead_ms=lead,
                                        total_ms=lead + line_slot.speech_ms, settings=settings, cancel=ctx.cancel))
                cursor = line_slot.end_ms
        if plan.duration_ms > cursor:
            parts.append(silence(work / "tail.wav", total_ms=plan.duration_ms - cursor, settings=settings,
                                 cancel=ctx.cancel))
        joined = ctx.workdir / "narration.wav"
        if len(parts) == 1:
            shutil.copy2(parts[0], joined)
        else:
            ffmpeg.concat_audio(parts, joined, cancel=ctx.cancel)
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=inputs.voice), ext=".wav")
        beats = [BeatSlotModel(start_ms=s.start_ms, end_ms=s.end_ms,
                               lines=[LineSlotModel(start_ms=ln.start_ms, speech_ms=ln.speech_ms, end_ms=ln.end_ms)
                                      for ln in s.lines])
                 for s in plan.beats]
        return NarrationOutput(script=payload, audio_asset=asset.id, duration_ms=plan.duration_ms, beats=beats,
                               times=times, provider=name)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ComposeOutput:
        doc = document(payload)
        project = compose.write_project(doc, ctx.workdir / "project")
        resolved = {
            "durationMs": payload.duration_ms,
            "beats": [{"id": b["id"], "startMs": b["startMs"], "endMs": b["endMs"],
                       "visuals": [{"kind": v["kind"], "enterMs": v["enterMs"], "exitMs": v["exitMs"]}
                                   for v in b["visuals"]]}
                      for b in doc["beats"]],
            "warnings": payload.script.warnings,
        }
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8")
        return ComposeOutput(narration=payload, project=str(project), key=compose.project_key(doc))


def _renderer(ctx: StageContext, stage: str) -> Any:
    renderer = ctx.providers.get("renderer")
    problems = renderer.missing()
    if problems:
        raise RendererUnavailable("the reel renderer is not usable on this deployment",
                                  hint="; ".join(problems), stage=stage)
    return renderer


def storyboard_times(doc: dict[str, Any], duration_ms: int) -> list[int]:
    """Each beat's midpoint and the moment its last visual has landed (a counter on its final value), one per
    frame, in order; at most MAX_STILLS."""
    frame = 1000 / compose.FPS
    last = max(0, duration_ms - math.ceil(frame))
    wanted = []
    for beat in doc["beats"]:
        wanted.append((beat["startMs"] + beat["endMs"]) // 2)
        if beat["visuals"]:
            wanted.append(max(v["enterMs"] + (COUNTER_MS if v["kind"] == "counter" else STILL_AFTER_MS)
                              for v in beat["visuals"]))
    by_frame = {round(min(max(ms, 0), last) / frame): min(max(ms, 0), last) for ms in wanted}
    times = [by_frame[k] for k in sorted(by_frame)]
    if len(times) > MAX_STILLS:   # spread the keep evenly, first and last included
        times = [times[round(i * (len(times) - 1) / (MAX_STILLS - 1))] for i in range(MAX_STILLS)]
    return times


class PreviewStage:
    """The storyboard: a sheet of stills before any render. A full build passes through."""

    id = "preview"
    revision = 1
    cacheable = False
    output_model = PreviewOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> PreviewOutput:
        if payload.narration.script.inputs.mode == "full":
            return PreviewOutput(compose=payload)
        renderer = _renderer(ctx, self.id)
        sheet = self._storyboard(ctx, payload, renderer, ctx.config)
        return PreviewOutput(compose=payload, sheet=str(sheet), halt_pipeline=True)

    def _storyboard(self, ctx: StageContext, payload: ComposeOutput, renderer: Any, settings: Settings) -> Path:
        project = Path(payload.project)
        times = storyboard_times(document(payload.narration), payload.narration.duration_ms) or [0]
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


def caption_cues(doc: dict[str, Any]) -> list[tuple[int, int, str]]:
    """One (start, end, plain text) per caption chunk, as the reel shows them."""
    joiner = "" if is_unspaced(doc.get("language") or "en") else " "
    return [(c["startMs"], c["endMs"], joiner.join(w["t"] for w in c["words"])) for c in doc["captions"]]


class RenderStage:
    """The full reel: the project rendered once (cached by its key), the voice, the sound effects and captions."""

    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: PreviewOutput) -> RenderOutput:
        settings: Settings = ctx.config
        composed = payload.compose
        narration = composed.narration
        inputs = narration.script.inputs
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
        doc = document(narration)
        planned = sfx_cues(doc)
        sfx = {n: synth_sfx(n, work / "sfx" / f"{n}.wav", settings=settings, designed=True)
               for n in sorted({n for _, n in planned})}
        audio = mix(ctx.assets.get(narration.audio_asset), [(at, sfx[n]) for at, n in planned], None,
                    work / "audio.wav", total_ms=total_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(cached, audio, outdir / f"{name}.mp4", total_ms=total_ms, settings=settings, cancel=ctx.cancel)
        captions = [Caption(start_ms=start, end_ms=end, text=text, lang=inputs.language,
                            style_ref="techreel.caption")
                    for start, end, text in caption_cues(doc)]
        srt = write_srt(captions, work / f"{name}.srt")
        manifest = {"reel": str(video), "recipe": "techreel", "mode": inputs.mode,
                    "created": datetime.now(UTC).isoformat(), "durationMs": total_ms,
                    "look": inputs.look, "voice": inputs.voice, "renderer": getattr(renderer, "name", ""),
                    "sources": list(inputs.sources)}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path), duration_ms=total_ms)
