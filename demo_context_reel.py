"""Demo reel using real Nadeshiko scenes, aligned the way production does it.

Each scene comes from a different anime, so each gets **its own episode file and
its own alignment** — exactly what `build_plan` does when a reel draws from 3-5
titles.  Collapsing them onto one timeline (as an earlier version of this script
did) is invalid: three episodes have three different offsets, and no single
affine map can describe them.

Everything except the video pixels is real: the word's segments, their
neighbouring lines from `/context`, every line's reference audio, the Japanese
text, the translations and the timestamps.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

from reelmachine import ffmpeg, reel as reelmod
from reelmachine.align import align_episode
from reelmachine.config import get_settings
from reelmachine.nadeshiko import NadeshikoClient
from reelmachine.sources.base import EpisodeAsset

WORD = "やっぱり"
SR = 48_000
LEAD_IN_MS = 20_000        # where the scene starts inside its own episode file
EPISODE_S = 75
PICKS = 3
TARGET_OFFSET_MS = 4_800   # difference between the API clock and "our copy"


def build_episode(root: Path, index: int, scene, seed: int):
    """One episode file containing this scene's lines at a known offset."""
    first = scene[0].startTimeMs
    audio = (np.random.default_rng(seed).standard_normal(EPISODE_S * SR) * 0.004).astype(np.float32)
    placements: list[tuple[str, int, int]] = []
    for line in scene:
        pcm = ffmpeg.read_pcm(root / "clips" / f"{line.publicId}.mp3", sample_rate=SR) * 0.9
        # local = src + constant, which is what the aligner has to recover.
        local_ms = line.startTimeMs - first + LEAD_IN_MS - TARGET_OFFSET_MS
        start = int(local_ms * SR / 1000)
        end = min(audio.size, start + pcm.size)
        audio[start:end] += pcm[: end - start]
        placements.append((line.publicId, line.startTimeMs, local_ms))

    wav = root / f"ep{index}.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())

    video = root / f"ep{index}.mkv"
    ffmpeg.run([
        get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={EPISODE_S}",
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR), "-shortest", "-y", str(video),
    ])
    return video, placements


def main() -> None:
    settings = get_settings()
    root = Path(".work/context-demo")
    (root / "clips").mkdir(parents=True, exist_ok=True)

    with NadeshikoClient(settings=settings) as client:
        page = client.search(WORD, take=50)
        picks = []
        seen: set[str] = set()
        for segment in page.segments:
            if segment.mediaPublicId in seen or not segment.english:
                continue
            scene = sorted(client.segment_context(segment.publicId, take=1), key=lambda s: s.startTimeMs)
            if len(scene) < 3 or segment.publicId not in {s.publicId for s in scene}:
                continue
            seen.add(segment.mediaPublicId)
            for line in scene:
                client.download(line.urls.audioUrl, root / "clips" / f"{line.publicId}.mp3")
            picks.append((segment, page.media.get(segment.mediaPublicId), scene))
            if len(picks) == PICKS:
                break
        print(f"quota: {client.quota().requests} requests\n")

    plan = reelmod.Plan(word=WORD, source="context-demo")
    for index, (word, media, scene) in enumerate(picks):
        title = (media.nameEn if media else "?") or "?"
        video, placements = build_episode(root, index, scene, seed=100 + index)
        clip_paths = {pid: root / "clips" / f"{pid}.mp3" for pid, _, _ in placements}

        timeline = align_episode(
            video, scene, clip_paths=clip_paths, max_anchors=len(scene),
            episode_duration_ms=EPISODE_S * 1000,
        )
        print(f"{title[:36]:<38} ep{word.episode}  {timeline.describe()}")

        low, high = reelmod.compute_scene_window(
            word.startTimeMs, word.endTimeMs,
            [x for x in scene if x.publicId != word.publicId],
            max_ms=settings.max_clip_ms, min_ms=settings.min_clip_ms,
        )
        plan.items.append(reelmod.PlannedSegment(
            segment=word, media=media,
            asset=EpisodeAsset(
                media_public_id=word.mediaPublicId, episode=word.episode, url=str(video),
                duration_ms=EPISODE_S * 1000, local_path=video,
                label=f"{title[:30]} · ep {word.episode}"),
            timeline=timeline,
            local_start_ms=timeline.to_local_ms(low),
            local_end_ms=timeline.to_local_ms(high),
            scene_start_ms=low, scene_end_ms=high,
            context_count=len(scene) - 1, status="ok",
        ))

    result = reelmod.render(plan, outdir=Path("out"), name=f"{WORD}-scene-demo",
                            watermark="@reel-machine · scene cut")
    info = ffmpeg.probe(result.video)
    print(f"\nreel: {result.video}  {result.duration_ms / 1000:.1f}s  {info.width}x{info.height}")
    ass = re.sub(r"\{[^}]*\}", "", result.ass.read_text(encoding="utf-8"))
    for line in ass.splitlines():
        if ",JA," in line:
            print("   JA:", line.split(",,", 1)[1])
        elif ",EN," in line:
            print("   EN:", line.split(",,", 1)[1])


if __name__ == "__main__":
    main()
