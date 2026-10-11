#!/usr/bin/env python3
"""Mix downloaded getyarn clips into a reel.

This is the local mixer the getyarn flow ends in: a folder of clips plus a small
manifest (what was searched, which clip is which line of dialogue) becomes one
vertical reel with the searched phrase pinned on screen and coloured wherever it
is spoken.

It is deliberately a client of the engine rather than a new recipe: the join,
the blur-fill fit to a vertical frame, the caption burn and the loudness
normalisation are `ffmpeg.build_reel_video`, and the captions are the same ASS
writer every other recipe uses, so the karaoke/emphasis behaviour is shared.

usage:
  uv run python tools/getyarn_search.py "piece of cake" --limit 6 \
      --download .work/getyarn/clips --json .work/getyarn/piece-of-cake.json
  uv run python tools/getyarn_reel.py --manifest .work/getyarn/piece-of-cake.json \
      --clips-dir .work/getyarn/clips --aspect vertical

The search tool writes the manifest and fetches the clips (each entry's `file` names the
file inside `--clips-dir`; the clip id is the name when it does not), so the two commands
are a chain: search -> download -> reel.

The clips themselves are third-party media; the manifest it writes records where
each one came from so the rights question is answerable later.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reelmachine import ffmpeg  # noqa: E402
from reelmachine.config import get_settings  # noqa: E402
from reelmachine.core.timeline import Caption  # noqa: E402
from reelmachine.render.captions import CaptionStyle, ass_colour, write_ass  # noqa: E402

PHRASE_STYLE = "getyarn.phrase"
GLOSS_STYLE = "getyarn.gloss"
LINE_STYLE = "getyarn.line"
SOURCE_STYLE = "getyarn.source"

GEOMETRY = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}
FPS = 30

#: How long the meaning of the phrase stays up. Long enough to read before the
#: first example starts competing with it.
GLOSS_MS = 3_500

GOLD = "#ffd166"


# --------------------------------------------------------------------------- styles


def caption_styles(settings: Any, *, aspect: str) -> dict[str, CaptionStyle]:
    """Four layers: the phrase, its meaning, the spoken line, the attribution."""
    font = str(getattr(settings, "font_en", "") or "Helvetica")
    width, height = GEOMETRY.get(aspect, GEOMETRY["vertical"])
    portrait = height >= width
    scale = 1.0 if portrait else 0.78
    return {
        PHRASE_STYLE: CaptionStyle(
            name="Phrase",
            font=font,
            size=int(88 * scale),
            primary=ass_colour("#ffffff"),
            highlight=ass_colour(GOLD),
            outline=3.4,
            shadow=1.0,
            alignment=8,  # top-centre
            margin_v=int(150 * scale),
            bold=1,
        ),
        GLOSS_STYLE: CaptionStyle(
            name="Gloss",
            font=font,
            size=int(44 * scale),
            primary=ass_colour("#c9d4e0"),
            highlight=ass_colour(GOLD),
            outline=2.2,
            shadow=0.8,
            alignment=8,
            margin_v=int(272 * scale),
        ),
        LINE_STYLE: CaptionStyle(
            name="Line",
            font=font,
            size=int(58 * scale),
            primary=ass_colour("#ffffff"),
            highlight=ass_colour(GOLD),
            outline=3.0,
            shadow=1.2,
            alignment=2,  # bottom-centre
            margin_v=int(290 * scale),
            bold=1,
            karaoke=True,
        ),
        SOURCE_STYLE: CaptionStyle(
            name="Source",
            font=font,
            size=int(34 * scale),
            primary=ass_colour("#9fb0c0"),
            highlight=ass_colour(GOLD),
            outline=2.0,
            shadow=0.6,
            alignment=1,  # bottom-left
            margin_lr=int(64 * scale),
            margin_v=int(74 * scale),
        ),
    }


# --------------------------------------------------------------------------- input


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("clips"):
        raise SystemExit(f"{path} names no clips")
    return data


def resolve_clips(
    manifest: dict[str, Any], clips_dir: Path, only: list[str] | None = None
) -> list[dict[str, Any]]:
    """Match each manifest entry to a file on disk, in manifest order.

    A manifest clip may name its file outright; otherwise the clip id is the
    filename, which is what getyarn's own download link produces.
    """
    resolved: list[dict[str, Any]] = []
    for entry in manifest["clips"]:
        clip_id = str(entry.get("id") or "").strip()
        if only and clip_id not in only:
            continue
        path = _clip_path(entry, clips_dir, clip_id)
        if not path.exists():
            sys.stderr.write(f"missing clip, skipping: {path}\n")
            continue
        info = ffmpeg.probe(path)
        seconds = float(entry.get("seconds") or info.duration_s or 0.0)
        resolved.append(
            {
                **entry,
                "id": clip_id,
                "path": path,
                "seconds": seconds,
                "line": str(entry.get("line") or "").strip(),
                "title": str(entry.get("title") or "Unknown").strip(),
            }
        )
    if not resolved:
        raise SystemExit(f"no clips found under {clips_dir}")
    return resolved


def _clip_path(entry: dict[str, Any], clips_dir: Path, clip_id: str) -> Path:
    """Resolve one entry's file: a bare name lives in `--clips-dir`, a path is taken as given.

    Both conventions exist in the wild — a manifest written by the search tool carries the
    bare name, one written by hand may carry a path — and joining `clips_dir` onto a path
    that is already right produced `clips_dir/clips_dir/<id>.mp4`, which is how a full
    directory of clips came back as "no clips found".
    """
    raw = str(entry.get("file") or f"{clip_id}.mp4")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return clips_dir / candidate.name



# --------------------------------------------------------------------------- captions


def build_captions(
    clips: list[dict[str, Any]],
    *,
    phrase: str,
    gloss: str = "",
) -> tuple[list[Caption], int]:
    """The reel's text: a pinned phrase, its meaning, then one line per clip."""
    captions: list[Caption] = []
    total_ms = 0
    for clip in clips:
        start = total_ms
        end = start + max(500, int(round(clip["seconds"] * 1000)))
        captions.append(
            Caption(
                start_ms=start,
                end_ms=end,
                text=clip["line"] or phrase,
                style_ref=LINE_STYLE,
                meta={"emphasis": phrase, "source": clip["title"]},
            )
        )
        captions.append(
            Caption(
                start_ms=start,
                end_ms=end,
                text=clip["title"],
                style_ref=SOURCE_STYLE,
                meta={"source": clip["title"]},
            )
        )
        total_ms = end

    # The phrase is pinned for the whole reel, so a viewer who joins halfway
    # still knows what every clip has in common.
    captions.append(
        Caption(start_ms=0, end_ms=total_ms, text=phrase, style_ref=PHRASE_STYLE, meta={})
    )
    if gloss:
        captions.append(
            Caption(
                start_ms=0,
                end_ms=min(GLOSS_MS, total_ms),
                text=gloss,
                style_ref=GLOSS_STYLE,
                meta={},
            )
        )
    captions.sort(key=lambda caption: (caption.start_ms, caption.style_ref != PHRASE_STYLE))
    return captions, total_ms


# --------------------------------------------------------------------------- mix


def mix(
    clips: list[dict[str, Any]],
    dest: Path,
    *,
    phrase: str,
    gloss: str = "",
    aspect: str = "vertical",
    crf: int | None = None,
    preset: str | None = None,
    fps: int = FPS,
    keep_ass: Path | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    width, height = GEOMETRY.get(aspect, GEOMETRY["vertical"])
    captions, total_ms = build_captions(clips, phrase=phrase, gloss=gloss)
    ass_path = keep_ass or (dest.parent / f"{dest.stem}.ass")
    write_ass(
        ass_path,
        captions,
        styles=caption_styles(settings, aspect=aspect),
        width=width,
        height=height,
        title=phrase,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.build_reel_video(
        [clip["path"] for clip in clips],
        dest,
        ass_path=ass_path,
        aspect=aspect,
        width=width,
        height=height,
        fps=fps,
        crf=crf if crf is not None else settings.crf,
        preset=preset or settings.preset,
    )
    return {
        "dest": dest,
        "ass": ass_path,
        "captions": captions,
        "duration_ms": total_ms,
        "width": width,
        "height": height,
        "output_aspect": ffmpeg.probe(dest).aspect,
    }


# --------------------------------------------------------------------------- cli


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in text.lower())
    return "-".join(cleaned.split()) or "reel"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--clips-dir", required=True, type=Path)
    parser.add_argument("--phrase", default=None, help="defaults to the manifest's phrase")
    parser.add_argument("--gloss", default=None, help="defaults to the manifest's gloss")
    parser.add_argument("--aspect", default="vertical", choices=sorted(GEOMETRY))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--crf", type=int, default=None)
    parser.add_argument("--preset", default=None)
    parser.add_argument("--only", default=None, help="comma-separated clip ids")
    parser.add_argument("--manifest-out", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    phrase = (args.phrase or manifest.get("phrase") or "").strip()
    if not phrase:
        raise SystemExit("no phrase: pass --phrase or set it in the manifest")
    gloss = (args.gloss if args.gloss is not None else manifest.get("gloss") or "").strip()
    only = [part.strip() for part in (args.only or "").split(",") if part.strip()] or None

    clips = resolve_clips(manifest, args.clips_dir, only)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = args.name or _slug(phrase)
    dest = args.out or (ROOT / "out" / f"{name}-{args.aspect}-{stamp}.mp4")

    print(f"mixing {len(clips)} clips -> {dest}")
    for clip in clips:
        print(f"  {clip['seconds']:5.2f}s  {clip['title']}  ·  {clip['line']}")

    result = mix(
        clips,
        dest,
        phrase=phrase,
        gloss=gloss,
        aspect=args.aspect,
        crf=args.crf,
        preset=args.preset,
    )

    sidecar = args.manifest_out or dest.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "phrase": phrase,
                "gloss": gloss,
                "aspect": args.aspect,
                "size": [result["width"], result["height"]],
                "duration_ms": result["duration_ms"],
                "rendered_at": stamp,
                "output": str(dest),
                "ass": str(result["ass"]),
                "search": manifest.get("search"),
                "source": manifest.get("source"),
                "clips": [
                    {
                        "id": clip["id"],
                        "title": clip["title"],
                        "line": clip["line"],
                        "seconds": clip["seconds"],
                        "page": clip.get("page"),
                        "clip_url": clip.get("clip_url"),
                        "file": str(clip["path"]),
                    }
                    for clip in clips
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"done: {dest} ({result['duration_ms'] / 1000:.1f}s) · sidecar {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
