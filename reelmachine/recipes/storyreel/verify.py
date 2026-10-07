"""Verify a finished storyreel by inspecting the real file.

A render that exits zero is not a correct reel: every section must have become a clip
inside the film's runtime, the clip slots must add up to the narration, the captions
must ride the voice, the render mode must be what the manifest says, and the last
frame must not be blank. Jobs that stop early (`transcript`/`plan`) are verified for
what they are — a complete transcript or a concept list — not against a render that
was never asked for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.errors import VerificationFailed
from ...core.manifest import CheckResult, VerificationReport

BLANK_STDDEV = 3.0


def last_frame_stddev(video: Path, *, settings: Settings) -> float:
    """Decode the final frame as grey pixels and return its contrast."""
    proc = subprocess.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-sseof",
            "-0.2",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise VerificationFailed(
            "the final frame could not be decoded",
            hint="the render may be truncated; re-run the job",
            details={"stderr": (proc.stderr or b"").decode("utf-8", "ignore")[-300:]},
        )
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    if pixels.size == 0:
        return 0.0
    return float(pixels.std())


def verify_storyreel(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    settings: Settings = ctx.config
    render = outputs.get("render")
    transcript = outputs.get("transcript")
    notes: list[str] = []
    checks: list[CheckResult] = []

    if transcript is None:
        return VerificationReport(
            ok=False,
            checks=[CheckResult(name="stages", ok=False, detail="the transcript stage did not run")],
            render_mode=getattr(render, "render_mode", ""),
        )

    checks.append(
        CheckResult(
            name="transcript",
            ok=len(transcript.cues) > 0 and len(transcript.scenes) > 0,
            detail=f"{len(transcript.cues)} cues, {len(transcript.scenes)} scenes via {transcript.subtitle_source}",
        )
    )
    if not transcript.synced and transcript.subtitle_source not in ("caller", "embedded"):
        notes.append("subtitles were not aligned to this copy of the film; clip timings may drift")

    concepts = outputs.get("concepts")
    if concepts is not None:
        # a caller who handed over the finished story package never needed concepts
        concepts_ok = bool(concepts.concepts) or concepts.source == "supplied-story"
        checks.append(
            CheckResult(
                name="concepts",
                ok=concepts_ok,
                detail=f"{len(concepts.concepts)} concept(s) [{concepts.source}]",
            )
        )

    if render is None:
        # a mode that stops early is a complete result for its mode
        checks.append(
            CheckResult(
                name="render",
                ok=True,
                detail=f"mode {transcript.inputs.mode!r}: stopped by design before the render",
            )
        )
        return VerificationReport(
            ok=all(check.ok for check in checks), checks=checks, notes="; ".join(notes)
        )

    prep = outputs.get("prep")
    compose = outputs.get("compose")
    script = outputs.get("script")
    voiceover = outputs.get("voiceover")
    if not all(item is not None for item in (prep, compose, script, voiceover)):
        return VerificationReport(
            ok=False,
            checks=[CheckResult(name="stages", ok=False, detail="the build stages did not all run")],
            render_mode=getattr(render, "render_mode", ""),
        )

    video = Path(render.video)
    checks.append(
        CheckResult(name="output", ok=video.is_file() and video.stat().st_size > 0, detail=str(video))
    )
    info = ffmpeg.probe(video)
    checks.append(
        CheckResult(
            name="streams",
            ok=info.has_video and info.has_audio,
            detail=f"video={info.has_video} audio={info.has_audio} {info.vcodec}/{info.acodec}",
        )
    )
    checks.append(
        CheckResult(
            name="duration",
            ok=abs(info.duration_s * 1000 - compose.timeline.duration_ms) <= 1200,
            detail=f"container {info.duration_s * 1000:.0f} ms vs timeline {compose.timeline.duration_ms} ms",
        )
    )

    sections = len(script.story.sections)
    clip_total = sum(record.duration_ms for record in prep.clips)
    checks.append(
        CheckResult(
            name="clip_coverage",
            ok=len(prep.clips) == sections and abs(clip_total - prep.narration_ms) <= 1500,
            detail=f"{len(prep.clips)}/{sections} clip(s), {clip_total} ms of {prep.narration_ms} ms narration",
        )
    )
    outside = [
        record.element_id
        for record in prep.clips
        if prep.duration_ms and record.source_start_ms + record.duration_ms > prep.duration_ms + 1500
    ]
    checks.append(
        CheckResult(
            name="clip_windows",
            ok=not outside,
            detail="all clip windows inside the film" if not outside else f"outside: {', '.join(outside[:4])}",
        )
    )

    voice_captions = [c for c in compose.timeline.captions if c.style_ref == "storyreel.voice"]
    last_end = max((caption.end_ms for caption in voice_captions), default=0)
    checks.append(
        CheckResult(
            name="captions",
            ok=len(voice_captions) == sections and last_end <= compose.timeline.duration_ms + 250,
            detail=f"{len(voice_captions)} caption(s) of {sections}, last at {last_end} ms",
        )
    )

    mode = render.render_mode
    checks.append(
        CheckResult(
            name="render_mode",
            ok=bool(mode) and mode == compose.timeline.render_mode,
            detail=f"manifest {mode!r}, timeline {compose.timeline.render_mode!r}",
        )
    )
    checks.append(
        CheckResult(
            name="final_frame",
            ok=last_frame_stddev(video, settings=settings) >= BLANK_STDDEV,
            detail="the last frame must not be blank",
        )
    )
    if script.story.seo is None:
        notes.append("no SEO package was produced")
    for stage in (prep, voiceover):
        for warning in getattr(stage, "warnings", []) or []:
            notes.append(str(warning))

    return VerificationReport(
        ok=all(check.ok for check in checks),
        checks=checks,
        notes="; ".join(dict.fromkeys(notes)),
        render_mode=mode,
        visible_degradation=list(getattr(voiceover, "warnings", []) or [])[:3],
    )
