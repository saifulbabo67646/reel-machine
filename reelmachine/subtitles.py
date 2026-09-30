"""ASS / SRT subtitle generation, including the vocabulary card.

The reel is a teaching aid, so the frame is split into three bands:

* a **card** at the top holding the word being taught — romaji, English
  meaning, kana reading and the kanji form — so a viewer knows what to listen
  for before the clip plays;
* the **video** below it, as a sharp band over a blurred fill;
* the **caption** underneath: English, then the Japanese line with furigana
  above its kanji, then romaji.

Everything is one `.ass` file because libass gives positioning, outlines and
inline colour overrides in a single ffmpeg pass; a matching `.srt` is written
alongside for platforms that want it.  Furigana is placed by hand rather than
with ruby, because libass has no ruby support: the API's token readings are
positioned over their own characters using a glyph-advance model (CJK is
full-width, Latin is half), which is accurate enough for the CJK fonts used
here and needs no font metrics.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

# ASS colours are &HAABBGGRR (alpha, blue, green, red).
def rgb(red: int, green: int, blue: int) -> str:
    return f"&H00{blue:02X}{green:02X}{red:02X}"


WHITE = rgb(255, 255, 255)
YELLOW = rgb(255, 214, 64)
GREY = rgb(191, 191, 191)
OUTLINE = rgb(16, 16, 16)

CARD_BG = rgb(216, 232, 219)      # pale mint
CARD_INK = rgb(47, 93, 74)        # dark green
CARD_SOFT = rgb(74, 106, 92)      # muted green for the meaning line
CARD_GOLD = rgb(226, 176, 46)     # the kanji
EN_TEXT = rgb(255, 255, 255)
ROMAN_TEXT = rgb(228, 228, 228)

STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)


def _style(
    name: str,
    font: str,
    size: int,
    colour: str,
    *,
    bold: int = 0,
    outline: float = 0,
    shadow: float = 0,
    align: int = 5,
) -> str:
    return (
        f"Style: {name},{font},{size},{colour},{colour},{OUTLINE},&H00000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{align},0,0,0,1"
    )


def ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def escape_ass_text(text: str) -> str:
    """Neutralise ASS control characters and fold newlines."""
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\\N")


# The OpenAPI spec documents `<mark>`; every live response uses `<em>`.
_TAG_RE = re.compile(r"</?(?:mark|em)>", re.IGNORECASE)


def strip_marks(text: str) -> str:
    """Remove the API's highlight tags, whichever flavour it used."""
    return _TAG_RE.sub("", text or "")


def has_marks(text: str | None) -> bool:
    return bool(text and _TAG_RE.search(text))


def mark_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges the API wrapped in highlight tags.

    Offsets are into the *stripped* text, which is what gets rendered.
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    plain = 0
    open_at: int | None = None
    while cursor < len(text or ""):
        match = _TAG_RE.match(text, cursor)
        if match:
            if match.group(0).startswith("</"):
                if open_at is not None:
                    ranges.append((open_at, plain))
                    open_at = None
            else:
                open_at = plain
            cursor = match.end()
            continue
        plain += 1
        cursor += 1
    if open_at is not None:
        ranges.append((open_at, plain))
    return ranges


def locate_word(text: str, word: str, *, tokens: list | None = None) -> tuple[int, int] | None:
    """Find the word's character span in the Japanese line.

    Token offsets from the API are authoritative when present (they know where
    the morpheme is, even when the surface form is inflected); otherwise fall
    back to a substring search.
    """
    if not word:
        return None
    for token in tokens or []:
        surface = getattr(token, "s", None) or (token.get("s") if isinstance(token, dict) else None)
        begin = getattr(token, "b", None) if not isinstance(token, dict) else token.get("b")
        end = getattr(token, "e", None) if not isinstance(token, dict) else token.get("e")
        if not surface or begin is None or end is None:
            continue
        if surface == word or word in surface or surface in word:
            return int(begin), int(end)
    index = text.find(word)
    if index >= 0:
        return index, index + len(word)
    return None


def colourise(text: str, spans: list[tuple[int, int]], *, colour: str = YELLOW) -> str:
    """Wrap each (start, end) span of `text` in an ASS colour override."""
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        start = max(cursor, min(start, len(text)))
        end = max(start, min(end, len(text)))
        if start == end:
            continue
        out.append(escape_ass_text(text[cursor:start]))
        out.append(f"{{\\c{colour}\\b1}}")
        out.append(escape_ass_text(text[start:end]))
        out.append(f"{{\\c{WHITE}\\b0}}")
        cursor = end
    out.append(escape_ass_text(text[cursor:]))
    return "".join(out)


def highlight(
    text: str,
    word: str,
    *,
    tokens: list | None = None,
    marked: str | None = None,
    colour: str = YELLOW,
) -> str:
    """Wrap the matched term in an ASS colour override.

    `marked` is the API's `textJa.highlight`, which flags exactly what matched —
    including variant forms (`やはり` for a search for `やっぱり`) that a literal
    substring search would miss.
    """
    if marked:
        ranges = mark_ranges(marked)
        if ranges:
            return colourise(strip_marks(marked), ranges, colour=colour)

    span = locate_word(text, word, tokens=tokens)
    if span is None:
        return escape_ass_text(text)
    return colourise(text, [span], colour=colour)


# ------------------------------------------------------------ glyph geometry


@dataclass(frozen=True, slots=True)
class Metrics:
    """Per-character advance widths, in em, for one font at one size.

    These are *measured*, not assumed.  A CJK glyph is conventionally one em
    wide, but libass renders `Hiragino Sans` at **0.80 em** and `YuGothic` at
    0.625, and an ASCII space is nowhere near the half-em a naive model would
    guess.  Getting this wrong is invisible until furigana is placed: the
    readings drift away from the characters they annotate, further with every
    character, and the error is different for every font.
    """

    cjk: float = 0.80
    latin: float = 0.50
    space: float = 0.264
    ideographic_space: float = 0.80

    def advance(self, ch: str) -> float:
        if ch == "\u3000":
            return self.ideographic_space
        if ch == " ":
            return self.space
        if ord(ch) < 0x2000:              # latin, digits, ascii punctuation
            return self.latin
        return self.cjk


DEFAULT_METRICS = Metrics()


def char_width(ch: str, metrics: Metrics | None = None) -> float:
    """Advance width in em for one character."""
    return (metrics or DEFAULT_METRICS).advance(ch)


def text_width_em(text: str, metrics: Metrics | None = None) -> float:
    return sum(char_width(ch, metrics) for ch in text)


def wrap_ranges(
    text: str,
    *,
    font_size: int,
    max_width: int,
    metrics: Metrics | None = None,
    break_points: Iterable[int] | None = None,
) -> list[tuple[int, int]]:
    """Split `text` into (start, end) ranges that each fit `max_width` pixels.

    Returns character ranges rather than strings so token offsets stay valid
    and furigana can still be attributed to the right slice.

    `break_points` are offsets a row may break *at* — pass the token starts and
    a compound is kept whole.  Without it `お姉ちゃん` splits across the wrap, and
    because furigana is only drawn for tokens wholly inside a row, the split
    half loses its reading.  A break point is only honoured when it is close to
    where the row would otherwise end, so a very long token still gets broken
    rather than leaving a row nearly empty.
    """
    if not text:
        return []
    metrics = metrics or DEFAULT_METRICS
    limit_em = max_width / float(max(1, font_size))
    allowed = sorted(set(break_points)) if break_points else []
    ranges: list[tuple[int, int]] = []
    start = 0
    width = 0.0
    for index, ch in enumerate(text):
        advance = metrics.advance(ch)
        if width + advance > limit_em and index > start:
            cut = index
            behind = [point for point in allowed if start < point < index]
            if behind and index - behind[-1] <= 6:
                cut = behind[-1]
            ranges.append((start, cut))
            start = cut
            # Re-measure from the new row start: characters between `cut` and
            # `index` have already been walked past.
            width = sum(metrics.advance(c) for c in text[start:index])
        width += advance
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges


#: Calibration rows, one sample per row.  Each pair shares its side bearings, so
#: subtracting one ink width from the other isolates the advance being measured.
#: Spaces are measured by sandwiching them between two kanji: a run of spaces has
#: no ink at all and cannot be measured directly.
_CALIBRATION_ROWS: tuple[str, ...] = (
    "\u78ba" * 12,
    "\u78ba" * 6,
    "\u78ba\u78ba",
    "x" * 12,
    "x" * 6,
    "\u78ba" + " " * 6 + "\u78ba",
    "\u78ba" + "\u3000" * 6 + "\u78ba",
)


@lru_cache(maxsize=16)
def measure_metrics(font: str, size: int) -> Metrics:
    """Measure this font's real advance widths by rendering a test frame.

    Falls back to the defaults if ffmpeg or numpy is unavailable — a slightly
    misplaced reading beats no reel.  Cached per (font, size), so a build pays
    for it once.
    """
    try:
        import subprocess

        import numpy as np

        from .config import get_settings

        settings = get_settings()
        # Generous margins: the sample lines must never reach the frame edge,
        # and the outermost pixels are unreliable anyway (see `ink_width`).
        width = max(700, int(16 * size))
        row_h = int(size * 1.8) + 24
        height = row_h * len(_CALIBRATION_ROWS)
        styles, events = "", []
        for index, sample in enumerate(_CALIBRATION_ROWS):
            name = f"C{index}"
            styles += (
                f"Style: {name},{font},{size},&H00000000,&H00000000,&H00101010,"
                f"&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
            )
            y = row_h // 2 + index * row_h
            events.append(
                f"Dialogue: 0,0:00:00.00,0:00:01.00,{name},,0,0,0,,"
                f"{{\\an5\\pos({width // 2},{y})}}{escape_ass_text(sample)}"
            )
        ass = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n" + styles + "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n" + "\n".join(events) + "\n"
        )
        path = Path(tempfile.mkdtemp(prefix="reel-metrics-")) / "calib.ass"
        path.write_text(ass, encoding="utf-8")
        raw = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
             "-f", "lavfi", "-i", f"color=c=white:s={width}x{height}:d=1",
             "-vf", f"ass={path}", "-frames:v", "1",
             "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60,
        ).stdout
        expected = width * height * 3
        if len(raw) < expected:
            return DEFAULT_METRICS
        frame = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(height, width, 3)
        dark = frame.mean(axis=2) < 128

        def ink_width(index: int) -> int:
            y = row_h // 2 + index * row_h
            band = dark[max(0, y - row_h // 2 + 4): y + row_h // 2 - 4]
            # Skip the outermost columns: the ass filter leaves a dark 1px seam
            # at the right edge of the frame, which would otherwise be read as
            # ink and inflate every span by however far it sits from the text.
            inset = band[:, 3:width - 3]
            cols = np.where(inset.any(axis=0))[0]
            return int(cols.max() - cols.min()) if len(cols) else 0

        spans = [ink_width(i) for i in range(len(_CALIBRATION_ROWS))]
        cjk = (spans[0] - spans[1]) / 6.0 / size
        latin = (spans[3] - spans[4]) / 6.0 / size
        space = (spans[5] - spans[2]) / 6.0 / size
        ideographic = (spans[6] - spans[2]) / 6.0 / size

        def sane(value: float, low: float = 0.02, high: float = 2.0) -> bool:
            return low < value < high

        if not sane(cjk, 0.05):
            return DEFAULT_METRICS
        return Metrics(
            cjk=cjk,
            latin=latin if sane(latin, 0.05) else DEFAULT_METRICS.latin,
            space=space if sane(space, 0.01) else DEFAULT_METRICS.space,
            ideographic_space=ideographic if sane(ideographic, 0.05) else cjk,
        )
    except Exception:  # noqa: BLE001 - calibration is best-effort
        return DEFAULT_METRICS


def _iter_tokens(tokens: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Normalise pydantic tokens and plain dicts to one shape."""
    out: list[dict[str, Any]] = []
    for token in tokens or []:
        if isinstance(token, dict):
            get = token.get
        else:
            def get(key: str, t: Any = token) -> Any:
                return getattr(t, key, None)
        surface, reading = get("s"), get("r")
        begin, end = get("b"), get("e")
        if surface is None or begin is None or end is None:
            continue
        out.append({"s": str(surface), "r": str(reading or ""), "b": int(begin), "e": int(end)})
    return out


def has_kanji(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def kana_reading(reading: str) -> str:
    """Furigana is written in hiragana; the API returns katakana readings."""
    from .vocab import _to_hiragana  # local import keeps this module standalone

    return _to_hiragana(reading or "")


_ROMAJI_KEEP = re.compile(r"[\u3041-\u3093\u30a1-\u30f6\u30fc a-zA-Z0-9]")


def line_romaji(text: str, tokens: Iterable[Any] | None) -> str:
    """The whole line in romaji, built from the tokens' readings.

    Romaji cannot be derived from the surface text — `\u78ba\u304b\u306b` has to be read
    `tashika ni`, and the kanji do not say so.  The API's per-token readings do.

    Each token is converted *separately* and the results joined with spaces.
    Converting the concatenated readings instead would run the words together
    (`\u30bf\u30b7\u30ab\u30cb` -> `tashikani`), because romaji has no way to recover a
    word boundary that kana does not mark.
    """
    from .vocab import kana_to_romaji

    words: list[str] = []
    for token in _iter_tokens(tokens):
        surface, reading = token["s"], token["r"]
        if not surface.strip():
            continue
        # A punctuation token reports its reading as \u30ad\u30b4\u30a6 ("symbol"); using
        # that would spell the punctuation out loud, so it is dropped.
        source = surface if (not reading or reading in {"\u30ad\u30b4\u30a6", "\u8a18\u53f7"}) else reading
        cleaned = "".join(ch if _ROMAJI_KEEP.match(ch) else "" for ch in source)
        romaji = kana_to_romaji(cleaned).strip()
        if romaji:
            words.append(romaji)
    if not words:
        return kana_to_romaji("".join(ch if _ROMAJI_KEEP.match(ch) else "" for ch in text))
    return " ".join(words)


def furigana_runs(
    text: str, tokens: Iterable[Any] | None, *, limit: tuple[int, int] | None = None
) -> list[tuple[int, int, str]]:
    """`(start, end, reading)` for each kanji-bearing token inside `limit`.

    Offsets are relative to `limit`'s start, so they index the slice that is
    actually drawn on a row.  Tokens that are already kana (`\u306f`, `\u3067\u3059`) are
    skipped — annotating them would put a reading above text that already is one.
    """
    low, high = limit if limit else (0, len(text))
    runs: list[tuple[int, int, str]] = []
    for token in _iter_tokens(tokens):
        start, end = token["b"], token["e"]
        if start < low or end > high or end <= start:
            continue
        if not has_kanji(token["s"]) or not token["r"]:
            continue
        reading = kana_reading(token["r"])
        if reading == token["s"]:
            continue
        runs.append((start - low, end - low, reading))
    return runs


def make_positions(
    text: str,
    tokens: Iterable[Any] | None,
    *,
    font_size: int,
    metrics: Metrics | None = None,
    limit: tuple[int, int] | None = None,
    centre: int = 540,
) -> list[tuple[int, str]]:
    """`(x, reading)` for every furigana run, in the row's pixel coordinates.

    Text is centred, so the row's left edge is the centre minus half its width;
    a token's reading is then centred over its own character cells.  All of
    this rests on `metrics` being right — see `measure_metrics`.
    """
    metrics = metrics or DEFAULT_METRICS
    low, high = limit if limit else (0, len(text))
    slice_text = text[low:high]
    width_px = text_width_em(slice_text, metrics) * font_size
    left_px = centre - width_px / 2.0
    out: list[tuple[int, str]] = []
    for start, end, reading in furigana_runs(text, tokens, limit=(low, high)):
        before = text_width_em(slice_text[:start], metrics) * font_size
        span = text_width_em(slice_text[start:end], metrics) * font_size
        out.append((int(left_px + before + span / 2.0), reading))
    return out


# ---------------------------------------------------------------------- model


@dataclass(slots=True)
class Cue:
    """One subtitle cue on the *reel* timeline (ms from the start of the reel)."""

    start_ms: int
    end_ms: int
    japanese: str
    english: str
    word: str = ""
    tokens: list | None = None
    marked: str | None = None   # the API's `<mark>`-tagged highlight, if any
    source: str = ""      # e.g. "Anime Name · ep 3"
    index_label: str = "" # e.g. "1 / 5"
    reading: str = ""     # kana for the whole line, shown above it
    vocab: Any = None     # vocab.VocabEntry for the card, or None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class Layout:
    """Frame geometry.  Positions are ASS coordinates (origin top-left)."""

    play_x: int = 1080
    play_y: int = 1920

    # card
    card_h: int = 430
    badge_cx: int = 118
    badge_cy: int = 218
    badge_r: int = 56
    card_text_cx: int = 660
    word_y: int = 96
    word_size: int = 64
    mean_y: int = 172
    mean_size: int = 31
    kana_y: int = 244
    kana_size: int = 46
    kanji_y: int = 336
    kanji_size: int = 80

    # video band (also read by the ffmpeg compositor)
    video_y: int = 474
    video_h: int = 608

    # captions
    en_y: int = 1226
    en_size: int = 42
    ja_y: int = 1452
    ja_size: int = 56
    ja_max_width: int = 940
    furi_size: int = 25
    furi_gap: int = 52
    romaji_y: int = 1592
    romaji_size: int = 32

    margin_lr: int = 70
    ja_outline: float = 3.4
    en_outline: float = 3.0
    romaji_outline: float = 2.0
    source_size: int = 28
    source_y: int = 500
    wm_size: int = 30
    wm_margin_v: int = 40
    source_label: bool = True
    counter: bool = False

    @classmethod
    def for_aspect(cls, aspect: str, play_y: int = 1920) -> "Layout":
        if aspect == "vertical":
            return cls(play_x=1080, play_y=play_y)
        if aspect == "square":
            # No room for a card band and a caption stack in 1080x1080, so the
            # card shrinks and the video gives up most of its height.
            return cls(
                play_x=1080, play_y=1080,
                card_h=250, badge_cx=78, badge_cy=125, badge_r=36,
                card_text_cx=580, word_y=58, word_size=42, mean_y=104, mean_size=22,
                kana_y=150, kana_size=30, kanji_y=206, kanji_size=48,
                video_y=268, video_h=380,
                en_y=690, en_size=30, ja_y=800, ja_size=40, ja_max_width=900,
                furi_size=18, furi_gap=38, romaji_y=880, romaji_size=24,
                source_y=282, source_size=22,
            )
        return cls(
            play_x=1920, play_y=1080,
            card_h=0, video_y=0, video_h=1080,
            en_y=880, en_size=38, ja_y=980, ja_size=54, ja_max_width=1760,
            furi_size=24, furi_gap=50, romaji_y=1046, romaji_size=28,
            source_y=40, source_size=24,
        )


CARD_BADGE_GLYPH = "文"
#: Wrap manually: libass honours \N inside a Dialogue line, and a two-line
#: tagline fits under the badge where a one-line one would overhang the edge.
CARD_TAGLINE = ("Learn Japanese", "with anime")


# ------------------------------------------------------------------- assembly


def _circle_path(radius: int) -> str:
    """An ASS drawing path for a filled circle in a 0,0..2r,2r box.

    Four cubic arcs with the usual 0.5523 magic constant.  The path must start
    at 0,0 rather than being centred on the origin: libass shifts a drawing by
    its negative extent, so a path built around (-r,-r) lands nowhere near the
    position it was given.
    """
    r = max(1, int(radius))
    k = round(r * 0.5523)
    d = 2 * r
    return (
        f"m {r} 0 "
        f"b {r + k} 0 {d} {r - k} {d} {r} "
        f"b {d} {r + k} {r + k} {d} {r} {d} "
        f"b {r - k} {d} 0 {r + k} 0 {r} "
        f"b 0 {r - k} {r - k} 0 {r} 0"
    )


def _rect_path(width: int, height: int) -> str:
    """A rectangle from 0,0 — same 0,0 rule as `_circle_path`."""
    return f"m 0 0 l {int(width)} 0 {int(width)} {int(height)} 0 {int(height)}"


def _card_events(layout: Layout, vocab: Any, start: str, end: str, font_card: str) -> list[str]:
    """The vocabulary card: background, badge, and the four word lines.

    Emitted once for the whole reel rather than per cue — the card is the
    lesson, and it stays put while the clips change underneath it.
    """
    if not vocab or layout.card_h <= 0:
        return []
    cx = layout.card_text_cx
    r = layout.badge_r
    events = [
        # Background panel, drawn from the frame's top-left corner.
        f"Dialogue: 0,{start},{end},CardBox,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\p1\\1c{CARD_BG}\\1a&H00&}}{_rect_path(layout.play_x, layout.card_h)}{{\\p0}}",
        # Badge disc, then the 文 mark centred on it, then the tagline beneath.
        f"Dialogue: 0,{start},{end},CardBox,,0,0,0,,"
        f"{{\\an7\\pos({layout.badge_cx - r},{layout.badge_cy - r})\\p1\\1c{CARD_INK}\\1a&H00&}}"
        f"{_circle_path(r)}{{\\p0}}",
        f"Dialogue: 0,{start},{end},CardBadge,,0,0,0,,"
        f"{{\\an5\\pos({layout.badge_cx},{layout.badge_cy})}}{CARD_BADGE_GLYPH}",
    ]
    # One event per tagline row: `\N` inside the text would be doubled by
    # `escape_ass_text` and shown literally.
    for line_index, tagline in enumerate(CARD_TAGLINE):
        events.append(
            f"Dialogue: 0,{start},{end},CardTag,,0,0,0,,"
            f"{{\\an5\\pos({layout.badge_cx},{layout.badge_cy + r + 34 + line_index * 30})}}"
            f"{escape_ass_text(tagline)}"
        )

    word = getattr(vocab, "romaji", "") or getattr(vocab, "word", "")
    meaning = getattr(vocab, "meaning", "") or ""
    kana = getattr(vocab, "kana", "") or ""
    kanji = getattr(vocab, "word", "") or ""
    level = getattr(vocab, "level_label", "") or ""

    if word:
        suffix = f"{{\\c{CARD_GOLD}\\fs{int(layout.word_size * 0.5)}}}  {level}" if level else ""
        events.append(
            f"Dialogue: 0,{start},{end},CardWord,,0,0,0,,"
            f"{{\\an5\\pos({cx},{layout.word_y})}}{escape_ass_text(word)}{suffix}"
        )
    if meaning:
        events.append(
            f"Dialogue: 0,{start},{end},CardMean,,0,0,0,,"
            f"{{\\an5\\pos({cx},{layout.mean_y})}}{escape_ass_text(meaning[:64])}"
        )
    if kana and kana != kanji:
        events.append(
            f"Dialogue: 0,{start},{end},CardKana,,0,0,0,,"
            f"{{\\an5\\pos({cx},{layout.kana_y})}}{escape_ass_text(kana)}"
        )
    if kanji:
        # A box around the kanji, the way a flashcard would show it: an
        # unfilled rectangle drawn just behind the glyphs.
        pad_x = max(46, int(layout.kanji_size * 0.9))
        pad_y = int(layout.kanji_size * 0.62)
        events.append(
            f"Dialogue: 0,{start},{end},CardBox,,0,0,0,,"
            f"{{\\an7\\pos({cx - pad_x},{layout.kanji_y - pad_y})\\p1"
            f"\\1c{CARD_GOLD}\\1a&HFF&\\bord3}}{_rect_path(pad_x * 2, pad_y * 2)}{{\\p0}}"
        )
        events.append(
            f"Dialogue: 0,{start},{end},CardKanji,,0,0,0,,"
            f"{{\\an5\\pos({cx},{layout.kanji_y})}}{escape_ass_text(kanji)}"
        )
    return events


def _slice_highlight(
    text: str,
    low: int,
    high: int,
    *,
    word: str,
    marked: str | None,
    tokens: list | None,
    colour: str,
) -> str:
    """Highlight the word inside `text[low:high]`, rebasing absolute offsets.

    The API's mark ranges and token offsets are relative to the whole line, so
    both have to be shifted onto the slice when a long line is split across
    rows — otherwise the highlight lands on the wrong characters.
    """
    slice_text = text[low:high]
    spans: list[tuple[int, int]] = []
    if marked:
        for start, end in mark_ranges(marked):
            start, end = max(start, low) - low, min(end, high) - low
            if end > start:
                spans.append((start, end))
    if not spans and word:
        index = slice_text.find(word)
        if index >= 0:
            spans.append((index, index + len(word)))
    if not spans and word:
        for token in _iter_tokens(tokens):
            if token["b"] < low or token["e"] > high:
                continue
            surface = token["s"]
            if surface == word or word in surface or surface in word:
                spans.append((token["b"] - low, token["e"] - low))
                break
    if not spans:
        return escape_ass_text(slice_text)
    return colourise(slice_text, spans, colour=colour)


def _caption_events(
    cue: Cue,
    layout: Layout,
    start: str,
    end: str,
    font_ja: str,
    font_en: str,
    metrics: Metrics | None = None,
) -> list[str]:
    """English line, Japanese line(s) with furigana, then romaji."""
    events: list[str] = []
    text = strip_marks(cue.japanese)
    english = strip_marks(cue.english)

    if english:
        events.append(
            f"Dialogue: 0,{start},{end},EN,,0,0,0,,"
            f"{{\\an5\\pos({layout.play_x // 2},{layout.en_y})}}{escape_ass_text(english)}"
        )

    metrics = metrics or DEFAULT_METRICS
    ranges = wrap_ranges(
        text,
        font_size=layout.ja_size,
        max_width=layout.ja_max_width,
        metrics=metrics,
        # Break between tokens where possible, so a compound is not split.
        break_points={token["b"] for token in _iter_tokens(cue.tokens)},
    )
    ranges = ranges[:2] or [(0, len(text))]
    centre = layout.play_x // 2
    # A row's furigana sits `furi_gap` above it, so rows must be at least
    # that far apart or the reading lands on top of the line above.
    line_step = layout.ja_size + layout.furi_gap + 12
    # The last line sits on `ja_y`; earlier lines stack upwards from it.
    for depth, (low, high) in enumerate(reversed(ranges)):
        line_y = layout.ja_y - depth * line_step
        body = _slice_highlight(
            text, low, high,
            word=cue.word, marked=cue.marked, tokens=cue.tokens, colour=YELLOW,
        )
        events.append(
            f"Dialogue: 0,{start},{end},JA,,0,0,0,,"
            f"{{\\an5\\pos({centre},{line_y})}}{body}"
        )

        for x, reading in make_positions(
            text, cue.tokens,
            font_size=layout.ja_size, metrics=metrics, limit=(low, high), centre=centre,
        ):
            events.append(
                f"Dialogue: 0,{start},{end},Furi,,0,0,0,,"
                f"{{\\an5\\pos({x},{line_y - layout.furi_gap})}}"
                f"{escape_ass_text(reading)}"
            )

    romaji = cue.reading or ""
    if romaji:
        events.append(
            f"Dialogue: 0,{start},{end},Romaji,,0,0,0,,"
            f"{{\\an5\\pos({centre},{layout.romaji_y})}}{escape_ass_text(romaji)}"
        )

    if layout.source_label and cue.source:
        # `\an9` anchors the right edge at the position; `\an5` would centre on
        # it and push half the label off the frame.
        events.append(
            f"Dialogue: 0,{start},{end},Source,,0,0,0,,"
            f"{{\\an9\\pos({layout.play_x - layout.margin_lr},{layout.source_y})}}"
            f"{escape_ass_text(cue.source)}"
        )
    return events


def build_ass(
    cues: list[Cue],
    *,
    layout: Layout | None = None,
    font_ja: str = "Hiragino Sans",
    font_en: str = "Helvetica",
    font_card: str = "Times New Roman",
    metrics: Metrics | None = None,
    watermark: str = "",
    total_ms: int | None = None,
) -> str:
    layout = layout or Layout()
    styles = [
        _style("JA", font_ja, layout.ja_size, WHITE, outline=layout.ja_outline, shadow=1.6),
        _style("Furi", font_ja, layout.furi_size, WHITE, outline=2.2, shadow=1.0),
        _style("EN", font_en, layout.en_size, EN_TEXT, outline=layout.en_outline, shadow=1.4),
        _style("Romaji", font_en, layout.romaji_size, ROMAN_TEXT, outline=layout.romaji_outline, shadow=1.0),
        _style("WM", font_en, layout.wm_size, GREY, outline=1.6, align=9),
        _style("Source", font_en, layout.source_size, GREY, outline=1.6),
        _style("CardWord", font_card, layout.word_size, CARD_INK, align=5),
        _style("CardMean", font_en, layout.mean_size, CARD_SOFT, align=5),
        _style("CardKana", font_ja, layout.kana_size, CARD_INK, align=5),
        _style("CardKanji", font_ja, layout.kanji_size, CARD_GOLD, align=5),
        _style("CardBadge", font_ja, int(layout.badge_r * 0.86), WHITE, align=5),
        _style("CardTag", font_en, max(14, int(layout.mean_size * 0.72)), CARD_INK, align=5),
        _style("CardBox", font_en, 10, CARD_BG, align=7),
    ]
    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {layout.play_x}\n"
        f"PlayResY: {layout.play_y}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        f"{STYLE_FORMAT}\n" + "\n".join(styles) + "\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    end_of_reel = total_ms if total_ms is not None else (cues[-1].end_ms if cues else 3000)
    lines: list[str] = []
    if watermark:
        lines.append(
            f"Dialogue: 0,{ass_time(0)},{ass_time(end_of_reel)},WM,,0,0,0,,{escape_ass_text(watermark)}"
        )

    # The card is one lesson for the whole reel, so it is emitted once.
    card_vocab = next((c.vocab for c in cues if c.vocab), None)
    lines.extend(_card_events(layout, card_vocab, ass_time(0), ass_time(end_of_reel), font_card))

    for cue in cues:
        start, end = ass_time(cue.start_ms), ass_time(cue.end_ms)
        lines.extend(_caption_events(cue, layout, start, end, font_ja, font_en, metrics))
    return head + "\n".join(lines) + "\n"


def write_ass(path: Path, cues: list[Cue], **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(cues, **kwargs), encoding="utf-8")
    return path


def write_srt(path: Path, cues: list[Cue], *, bilingual: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for number, cue in enumerate(cues, start=1):
        body = strip_marks(cue.japanese)
        if bilingual and cue.english:
            body += "\n" + strip_marks(cue.english)
        blocks.append(f"{number}\n{srt_time(cue.start_ms)} --> {srt_time(cue.end_ms)}\n{body}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path
