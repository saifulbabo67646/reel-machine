"""explainer3d stages: script → assets → narration → shots → prep → compose → render."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Licence, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from .audio import mix, pad_speech, silence, synth_sfx
from .compile import FPS, load_look, look_timing, project_key, project_links, shot_document, write_project
from .music import synth_music
from .markup import display_text, display_words, parse_markup, spoken_text
from .assets import resolve_cast, set_pack
from .models import (
    AssetsOutput, ComposeOutput, EnvAssets, Explainer3DInputs, NarrationOutput, PrepOutput, RenderOutput,
    ScriptOutput, ShotProject, ShotsOutput, Track, Window,
)
from .state import build_ctx, plan_shots
from .timing import XFADE_MS, ShotWindow, fit_windows, shot_windows
from .sets import reel_sets
from .validate import resolve_script
from .video import join_shots, mux



def _workers() -> int:
    try:
        return max(1, int(os.environ.get("REEL_EXPLAINER3D_WORKERS", "2")))
    except ValueError:
        return 2


def _caller_cache(ctx: StageContext) -> Path:
    """Rendered shots and spoken lines are kept per caller, beside the engine's own stage
    cache (`jobs/<caller>/cache/`), so a later job — a build after its storyboard — reuses
    them instead of rendering or paying for them again. Outside the engine (a bare
    StageContext) they fall back to the configured cache dir."""
    settings: Settings = ctx.config
    job_id = getattr(ctx.job, "id", "")
    if job_id and settings.workdir.name == job_id:
        return settings.workdir.parent / "cache" / "explainer3d"
    return settings.cache_dir / "explainer3d"


def _speak(ctx: StageContext, provider: Any, text: str, voice: str, language: str) -> tuple[Path, int, list[WordSpan], Licence | None]:
    """One line, spoken once per (voice, language, text): cloud voices bill per character."""
    key = hashlib.sha256(json.dumps([getattr(provider, "name", ""), voice, language, text]).encode()).hexdigest()
    folder = _caller_cache(ctx) / "lines" / key[:2]
    audio, meta = folder / f"{key}.wav", folder / f"{key}.json"
    if audio.is_file() and meta.is_file():
        data = json.loads(meta.read_text(encoding="utf-8"))
        licence = Licence.model_validate(data["licence"]) if data.get("licence") else None
        return audio, data["ms"], [WordSpan.model_validate(w) for w in data["words"]], licence
    speech = provider.synthesize(text, voice=voice, language=language)
    ms = speech.duration_ms or int(ffmpeg.probe(speech.path).duration_s * 1000)
    words = list(speech.segments[0].words) if speech.segments else []
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(speech.path, audio)
    meta.write_text(json.dumps({
        "ms": ms, "words": [w.model_dump() for w in words],
        "licence": speech.licence.model_dump() if speech.licence else None,
    }), encoding="utf-8")
    return audio, ms, words, speech.licence


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = Explainer3DInputs.model_validate(payload)
        try:
            shots = resolve_script(inputs)
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        _, shot_sets = reel_sets(inputs)
        return ScriptOutput(inputs=inputs, shots=shots, sets={s.key: s for s in shot_sets})


def asset_cache(settings: Settings) -> Path:
    """CC0 models, sky images and textures: public, so shared by every caller."""
    return settings.cache_dir / "explainer3d" / "assets"


class AssetsStage:
    """Shapes are built in the browser; models, sky images and textures are fetched once
    into the shared asset cache and measured, so later stages know every size exactly."""

    id = "assets"
    revision = 2
    cacheable = False
    output_model = AssetsOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> AssetsOutput:
        cache = asset_cache(ctx.config)
        try:
            models = resolve_cast(payload.inputs, cache)
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        envs = {}
        for key, resolved in payload.sets.items():
            pack = set_pack(resolved, cache)
            if pack is None:
                continue
            envs[key] = EnvAssets(
                files={rel: str(path) for rel, path in pack.files.items()},
                dressing=[{"root": str(d.model.root), "main": d.model.main, "files": list(d.model.files),
                           "source": d.model.source, "dims": list(d.dims), "at": d.at, "yaw": d.yaw}
                          for d in pack.dressing],
                licences=pack.licences)
        return AssetsOutput(inputs=payload.inputs, shots=payload.shots, sets=payload.sets, models=models, envs=envs)


class NarrationStage:
    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: AssetsOutput) -> NarrationOutput:
        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if provider_for(inputs.voice) != name:
            raise VoiceUnavailable(
                f"the voice {inputs.voice!r} needs the {provider_for(inputs.voice)!r} narration provider, "
                f"but this deployment serves {name!r}",
                hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment",
                stage=self.id,
            )
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        speeches, licence = [], None
        for shot in payload.shots:
            ctx.cancel.raise_if_cancelled()
            tokens = parse_markup(shot.narration)
            said = spoken_text(tokens)
            if not said:  # a beat shot: no voice, its own duration
                speeches.append((None, 0, [], tokens))
                continue
            spoken, ms, words, line_licence = _speak(ctx, provider, said, inputs.voice, inputs.language)
            local = work / f"{shot.id}.wav"
            shutil.copy2(spoken, local)
            speeches.append((local, ms, words, tokens))
            licence = line_licence or licence

        timing = look_timing(inputs.look)
        windows = shot_windows([ms for _, ms, _, _ in speeches], durations=[s.duration_ms for s in payload.shots],
                               **timing)
        if inputs.length:
            windows = fit_windows(windows, inputs.length * 1000)
        padded, all_words, all_hot = [], [], []
        for shot, (path, ms, words, tokens), window in zip(payload.shots, speeches, windows):
            target = work / f"{shot.id}.padded.wav"
            if path is None:
                padded.append(silence(target, total_ms=window.duration_ms, settings=settings, cancel=ctx.cancel))
            else:
                padded.append(pad_speech(path, target, lead_ms=window.speech_offset_ms,
                                         total_ms=window.duration_ms, settings=settings, cancel=ctx.cancel))
            base = window.start_ms + window.speech_offset_ms
            shown = display_words(tokens, words, total_ms=ms)
            all_words.append([WordSpan(text=w.text, start_ms=base + w.start_ms, end_ms=base + w.end_ms) for w in shown])
            all_hot.append([w.hot for w in shown])
        joined = ctx.workdir / "narration.wav"
        if len(padded) == 1:
            shutil.copy2(padded[0], joined)
        else:
            ffmpeg.concat_audio(padded, joined, cancel=ctx.cancel)
        total = windows[-1].end_ms
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=inputs.voice), ext=".wav")
        return NarrationOutput(
            inputs=inputs, shots=payload.shots, sets=payload.sets, models=payload.models, envs=payload.envs,
            audio_asset=asset.id,
            duration_ms=total,
            windows=[Window(start_ms=w.start_ms, duration_ms=w.duration_ms, speech_offset_ms=w.speech_offset_ms,
                            speech_ms=w.speech_ms) for w in windows],
            words=all_words, hot=all_hot, voice=inputs.voice, provider=name, licence=licence,
        )


class ShotsStage:
    id = "shots"
    revision = 1
    cacheable = False
    output_model = ShotsOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> ShotsOutput:
        inputs = payload.inputs
        windows = [ShotWindow(i, w.start_ms, w.duration_ms, w.speech_offset_ms, w.speech_ms)
                   for i, w in enumerate(payload.windows)]
        plans = plan_shots(inputs, payload.shots, windows, payload.models)
        look = load_look(inputs.look)
        if look.get("join") == "cut":  # the cinematic look cuts hard; no crossfades
            for plan in plans[1:]:
                plan.join = "cut"
        scene_ctx = build_ctx(inputs, payload.models)
        projects, cues = [], []
        for i, (plan, window) in enumerate(zip(plans, windows)):
            tail = XFADE_MS if i + 1 < len(plans) and plans[i + 1].join == "xfade" else 0
            words = [{"text": w.text, "start": (w.start_ms - window.start_ms) / 1000,
                      "end": (w.end_ms - window.start_ms) / 1000} for w in payload.words[i]]
            shot = payload.shots[i]
            hot = payload.hot[i] if i < len(payload.hot) else []
            env = payload.envs.get(shot.set_key)
            doc = shot_document(inputs, plan, window, words, scene_ctx, render_ms=window.duration_ms + tail,
                                models=payload.models, env=env, resolved_set=payload.sets.get(shot.set_key),
                                card=shot.card, hot=hot, total_ms=payload.duration_ms)
            directory = write_project(doc, ctx.workdir / "projects" / plan.shot_id,
                                      links=project_links(payload.models, shot.set_key, env))
            projects.append(ShotProject(shot_id=plan.shot_id, index=i, dir=str(directory), key=project_key(doc),
                                        nominal_ms=window.duration_ms, render_ms=window.duration_ms + tail,
                                        join=plan.join))
            cues.extend(plan.cues)
            if i > 0 and look.get("cutWhoosh"):
                cues.append({"at_ms": max(0, window.start_ms - 150), "sfx": "whoosh"})
        resolved = {
            "shots": [shot.model_dump() for shot in payload.shots],
            "plans": [plan.model_dump() for plan in plans],
            "windows": [w.model_dump() for w in payload.windows],
        }
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8")
        return ShotsOutput(narration=payload, projects=projects, cues=cues)


class PrepStage:
    """Render every shot (cached by content) or, in storyboard mode, one still per shot."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def halt_for(self, payload: ShotsOutput) -> bool:
        return payload.narration.inputs.mode == "storyboard"

    def run(self, ctx: StageContext, payload: ShotsOutput) -> PrepOutput:
        settings: Settings = ctx.config
        renderer = ctx.providers.get("renderer")
        problems = renderer.missing()
        if problems:
            raise RendererUnavailable("the shot renderer is not usable on this deployment",
                                      hint="; ".join(problems), stage=self.id)
        if self.halt_for(payload):
            return self._storyboard(ctx, payload, renderer, settings)

        cache = _caller_cache(ctx) / "shots"

        def render_one(project: ShotProject) -> Track:
            cached = cache / project.key[:2] / f"{project.key}.mp4"
            if cached.is_file():
                ctx.progress.detail(f"  · {project.shot_id}: cached")
            else:
                ctx.progress.detail(f"  · rendering {project.shot_id} ({project.render_ms / 1000:.1f}s)")
                partial = cached.with_name(f"{project.key}.partial.mp4")
                renderer.render(Path(project.dir), partial, fps=FPS, seconds=project.render_ms / 1000,
                                cancel=ctx.cancel)
                partial.replace(cached)
            return Track(shot_id=project.shot_id, path=str(cached), render_ms=project.render_ms,
                         renderer=renderer.name)

        with ThreadPoolExecutor(max_workers=_workers()) as pool:
            tracks = list(pool.map(render_one, payload.projects))
        return PrepOutput(shots=payload, tracks=tracks, renderer=renderer.name)

    def _storyboard(self, ctx: StageContext, payload: ShotsOutput, renderer: Any, settings: Settings) -> PrepOutput:
        stills = ctx.workdir / "stills"
        frames = [
            renderer.snapshot(Path(project.dir), project.nominal_ms / 1000 * 0.6,
                              stills / f"{project.index:03d}.png", cancel=ctx.cancel)
            for project in payload.projects
        ]
        cols = min(4, len(frames))
        rows = math.ceil(len(frames) / cols)
        # number the stills 1..n and fill the grid with white cells so `tile` emits it
        # without repeating a shot
        sheet_in = ctx.workdir / "sheet"
        sheet_in.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames, start=1):
            shutil.copy2(frame, sheet_in / f"{index:03d}.png")
        for index in range(len(frames) + 1, cols * rows + 1):
            ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                        "-i", "color=white:size=1080x1920", "-frames:v", "1",
                        str(sheet_in / f"{index:03d}.png")], cancel=ctx.cancel)
        sheet = _artifacts(ctx) / "storyboard.jpg"
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-framerate", "1",
                    "-start_number", "1", "-i", str(sheet_in / "%03d.png"),
                    "-vf", f"scale=360:640,tile={cols}x{rows}:padding=8:color=white",
                    "-frames:v", "1", "-q:v", "3", str(sheet)], cancel=ctx.cancel)
        return PrepOutput(shots=payload, storyboard=str(sheet), renderer=renderer.name, halt_pipeline=True)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> ComposeOutput:
        narration = payload.shots.narration
        inputs = narration.inputs
        settings: Settings = ctx.config
        video = [Clip(element_id=t.shot_id, asset=t.path, start_ms=w.start_ms, duration_ms=w.duration_ms,
                      origin="scene") for t, w in zip(payload.tracks, narration.windows)]
        captions = [
            Caption(start_ms=words[0].start_ms, end_ms=words[-1].end_ms,
                    text=display_text(parse_markup(shot.narration)), lang=inputs.language,
                    style_ref="explainer3d.caption", words=words, meta={"source": shot.id})
            for shot, words in zip(narration.shots, narration.words)
            if words  # a beat shot says nothing
        ]
        timeline = Timeline(
            duration_ms=narration.duration_ms, aspect="vertical", render_mode="hyperframes", style=inputs.look,
            video=video, captions=captions,
            audio=[TimedAudioRef(element_id="narration", asset=narration.audio_asset,
                                 duration_ms=narration.duration_ms, role="narration")],
            meta={"joins": [p.join for p in payload.shots.projects], "renderer": payload.renderer},
        )
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        name = inputs.name or ctx.job.id
        return ComposeOutput(prep=payload, timeline=timeline, render_options={
            "outdir": str(outdir), "reel_name": name, "workdir": str(settings.workdir / "render" / name),
            "credits": [f"Narration: {inputs.voice}", "3D: reel-machine explainer3d (three.js, HyperFrames)"],
        })


class RenderStage:
    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> RenderOutput:
        settings: Settings = ctx.config
        prep, timeline, options = payload.prep, payload.timeline, payload.render_options
        narration = prep.shots.narration
        inputs = narration.inputs
        work, outdir = Path(options["workdir"]), Path(options["outdir"])
        work.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        name = options["reel_name"]

        look = load_look(inputs.look)
        if inputs.music == "auto":
            underwater = [(w.start_ms, w.start_ms + w.duration_ms)
                          for shot, w in zip(narration.shots, narration.windows)
                          if narration.sets.get(shot.set_key) is not None and narration.sets[shot.set_key].mode == "underwater"]
            music = synth_music(work / "music.wav", total_ms=timeline.duration_ms, underwater=underwater)
        else:
            music = Path(inputs.music).expanduser() if inputs.music else None  # validated by the script stage
        joined = join_shots([Path(t.path) for t in prep.tracks], [p.join for p in prep.shots.projects],
                            [p.nominal_ms for p in prep.shots.projects], work / "video.mp4",
                            settings=settings, fps=FPS, cancel=ctx.cancel)
        designed = look.get("renderer") == "cinematic"
        cues = [(cue["at_ms"], synth_sfx(cue["sfx"], work / "sfx" / f"{cue['sfx']}.wav", settings=settings,
                                         designed=designed))
                for cue in prep.shots.cues]
        profile = look.get("audioProfile", inputs.audio_profile) if inputs.audio_profile == "studio" else inputs.audio_profile
        audio = mix(ctx.assets.get(narration.audio_asset), cues, music, work / "audio.wav",
                    total_ms=timeline.duration_ms, settings=settings, profile=profile,
                    cancel=ctx.cancel)
        video = mux(joined, audio, outdir / f"{name}.mp4", total_ms=timeline.duration_ms, settings=settings,
                    cancel=ctx.cancel)
        srt = write_srt(timeline.captions, work / f"{name}.srt")
        manifest = {
            "reel": str(video), "recipe": "explainer3d", "mode": "hyperframes",
            "created": datetime.now(timezone.utc).isoformat(), "durationMs": timeline.duration_ms,
            "shots": len(prep.tracks), "look": inputs.look, "renderer": prep.renderer,
            "models": {k: {"licence": m.licence.model_dump(), "provenance": m.provenance.model_dump()}
                       for k, m in narration.models.items()},
            "credits": options["credits"],
        }
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path),
                            duration_ms=timeline.duration_ms)
