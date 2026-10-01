"""Configuration: `.env` + environment, plus resolved paths and ffmpeg binaries."""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ASPECTS = ("vertical", "square", "original")

_COMMENT_RE = re.compile(r"\s#")


def parse_env_value(raw: str) -> str:
    """Value of a `KEY=value` line, honouring quotes and inline comments.

    `REEL_ASPECT=vertical   # vertical | square` must yield `vertical`; the
    comment is documentation, not part of the value.  A `#` inside a quoted
    value is kept.
    """
    value = raw.strip()
    if not value:
        return ""
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    match = _COMMENT_RE.search(value)
    if match:
        value = value[: match.start()]
    return value.strip()


def normalise_aspect(value: str, default: str = "vertical") -> str:
    """One of vertical / square / original, defensively.

    A malformed value used to fall through to the 'original' branch and
    silently emit a landscape reel, so anything unrecognised is coerced.
    """
    token = (value or "").strip().split()[0].lower() if (value or "").strip() else ""
    return token if token in ASPECTS else default


def load_dotenv(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Minimal .env reader (no dependency).  Returns the values it parsed."""
    path = path or (PROJECT_ROOT / ".env")
    parsed: dict[str, str] = {}
    if not path.is_file():
        return parsed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = parse_env_value(value)
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw)
    except ValueError:
        return default


def _csv(key: str, default: str = "") -> list[str]:
    raw = _env(key, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(slots=True)
class Settings:
    # Nadeshiko
    api_key: str = ""
    base_url: str = "https://api.nadeshiko.co"

    # Source provider
    source: str = "local"
    local_root: Path | None = None
    local_templates: list[str] = field(default_factory=list)
    hls_template: str = ""
    hls_quality: str = "1080"
    hls_map: Path | None = None
    hls_resolver_cmd: str = ""
    hls_token_cmd: str = ""
    hls_extension_picky: bool = False

    # vidvault
    tmdb_api_key: str = ""
    vidvault_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".work" / "downloads")
    vidvault_quality: str = "1080,720,480,360"
    vidvault_max_link_age_s: float = 0.0
    vidvault_download: bool = True
    vidvault_connections: int = 8
    #: Hosts to try first, matched as substrings of the CDN hostname.  The
    #: Japanese-tagged renditions come from one worker (mk2) that has been
    #: measured stalling at a few KiB/s for minutes, while untagged renditions
    #: come from another (tdm) that reaches MB/s.  Ordering is only a
    #: *preference*: an untagged rendition is still checked for a Japanese
    #: audio track before it is downloaded, so this can never hand the aligner
    #: a dub.
    vidvault_prefer_hosts: list[str] = field(default_factory=lambda: ["tdm"])
    #: Seconds without a single byte before a transfer is treated as stalled.
    #: The socket timeout alone does not catch this: a link trickling 3 KiB/s
    #: never trips it, it just never finishes.
    vidvault_stall_timeout_s: float = 45.0
    vidvault_referer: str = "https://vidvault.to/"
    vidvault_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    )
    mock_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".work" / "mock")
    referer: str = ""
    origin: str = ""
    user_agent: str = "Mozilla/5.0"
    cookie: str = ""

    # Pipeline
    workdir: Path = field(default_factory=lambda: PROJECT_ROOT / ".work")
    outdir: Path = field(default_factory=lambda: PROJECT_ROOT / "out")
    #: Per-caller quota ledger override (a job runs against its caller's ledger).
    ledger_file: Path | None = None
    #: Which narration provider the doodle recipe binds (fake | elevenlabs | cartesia).
    tts: str = "fake"
    aspect: str = "vertical"
    pre_roll_ms: int = 350
    post_roll_ms: int = 450
    crf: int = 20
    preset: str = "medium"
    max_segments: int = 3
    match_mode: str = "strict"   # strict | api
    context_enabled: bool = True
    context_take: int = 1
    max_clip_ms: int = 14_000
    min_clip_ms: int = 3_000
    #: `line` cuts just the target line (padded); `scene` keeps the whole
    #: exchange.  The scene is still what the alignment anchors on either way.
    cut_mode: str = "line"
    cut_pad_ms: int = 220
    #: Keep adding clips until the reel reaches this, so a reel of 4-second
    #: lines is not 8 seconds long.
    target_ms: int = 20_000
    content_rating: list[str] = field(default_factory=lambda: ["SAFE", "SUGGESTIVE"])
    #: Which corpora to search.  Nadeshiko indexes J-Drama alongside anime, and
    #: the API only returns the categories you ask for — `["ANIME"]` is a real
    #: filter, not the default.  Order matters only for readability.
    categories: list[str] = field(default_factory=lambda: ["ANIME", "JDRAMA"])
    #: Optional cap on how many clips may come from one category.  0 = no cap.
    #: Without it a "mixed" reel is not actually mixed: anime outnumbers J-Drama
    #: in the corpus by roughly 15 to 1, so the top-ranked clips are all anime.
    per_category: int = 0
    source_lang: str = "ja"
    font_ja: str = "Hiragino Sans"
    font_en: str = "Helvetica"
    font_card: str = "Times New Roman"   # the vocabulary card's romaji heading
    #: Skip the jisho.org meaning lookup (the bundled JLPT list still works).
    dictionary_offline: bool = False

    # Binaries
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    # --- derived helpers -------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return self.workdir / "cache"

    @property
    def ledger_path(self) -> Path:
        return self.ledger_file or (self.workdir / "quota.json")

    def source_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.referer:
            headers["Referer"] = self.referer
        if self.origin:
            headers["Origin"] = self.origin
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def ensure_dirs(self) -> None:
        for path in (self.workdir, self.cache_dir, self.outdir, self.vidvault_dir):
            path.mkdir(parents=True, exist_ok=True)
        if self.source == "mock":
            self.mock_dir.mkdir(parents=True, exist_ok=True)

    def missing(self) -> list[str]:
        """Human-readable list of things that will block a real run."""
        problems: list[str] = []
        if not self.api_key:
            problems.append("NADESHIKO_API_KEY is not set (https://nadeshiko.co/user/developer)")
        if self.source == "local" and not self.local_root:
            problems.append("REEL_SOURCE=local but REEL_LOCAL_ROOT is not set")
        if self.source == "hls" and not self.hls_template:
            problems.append("REEL_SOURCE=hls but REEL_HLS_TEMPLATE is not set")
        if self.source in {"vidvault", "vid"} and not self.tmdb_api_key:
            problems.append(
                "REEL_SOURCE=vidvault but REEL_TMDB_API_KEY is not set "
                "(free key: https://www.themoviedb.org/settings/api)"
            )
        if shutil.which(self.ffmpeg) is None:
            problems.append(f"ffmpeg not found on PATH (looked for {self.ffmpeg!r})")
        return problems

    def warnings(self) -> list[str]:
        """Non-blocking: things that degrade gracefully."""
        notes: list[str] = []
        if shutil.which(self.ffprobe) is None:
            notes.append(
                f"ffprobe not found ({self.ffprobe!r}) — falling back to parsing `ffmpeg -i`, "
                "which works but reports slightly less metadata"
            )
        return notes


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    root = _env("REEL_LOCAL_ROOT")
    settings = Settings(
        api_key=_env("NADESHIKO_API_KEY"),
        base_url=_env("NADESHIKO_BASE_URL", "https://api.nadeshiko.co").rstrip("/"),
        source=_env("REEL_SOURCE", "local").lower(),
        local_root=Path(root).expanduser() if root else None,
        local_templates=_csv(
            "REEL_LOCAL_TEMPLATES",
            "{nameEn}/Season {season:02d}/{nameEn} - {ep2}.mkv,{nameEn}/{nameEn} - {ep2}.mkv",
        ),
        hls_template=_env("REEL_HLS_TEMPLATE"),
        hls_quality=_env("REEL_HLS_QUALITY", "1080"),
        hls_map=Path(_env("REEL_HLS_MAP")).expanduser() if _env("REEL_HLS_MAP") else None,
        hls_resolver_cmd=_env("REEL_HLS_RESOLVER_CMD"),
        hls_token_cmd=_env("REEL_HLS_TOKEN_CMD"),
        hls_extension_picky=_env("REEL_HLS_EXTENSION_PICKY", "false").lower() in {"1", "true", "yes"},
        tmdb_api_key=_env("REEL_TMDB_API_KEY"),
        vidvault_dir=Path(_env("REEL_VIDVAULT_DIR", str(PROJECT_ROOT / ".work" / "downloads"))).expanduser(),
        vidvault_quality=_env("REEL_VIDVAULT_QUALITY", "1080,720,480,360"),
        vidvault_max_link_age_s=float(_env("REEL_VIDVAULT_MAX_LINK_AGE_S", "0") or 0),
        vidvault_download=_env("REEL_VIDVAULT_DOWNLOAD", "true").lower() in {"1", "true", "yes"},
        vidvault_connections=max(1, int(_env("REEL_VIDVAULT_CONNECTIONS", "8") or 8)),
        vidvault_prefer_hosts=_csv("REEL_VIDVAULT_PREFER_HOSTS", "tdm"),
        vidvault_stall_timeout_s=float(_env("REEL_VIDVAULT_STALL_TIMEOUT_S", "45") or 45),
        vidvault_referer=_env("REEL_VIDVAULT_REFERER", "https://vidvault.to/"),
        vidvault_user_agent=_env("REEL_VIDVAULT_USER_AGENT", "") or
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        mock_dir=Path(_env("REEL_MOCK_DIR", str(PROJECT_ROOT / ".work" / "mock"))).expanduser(),
        referer=_env("REEL_SOURCE_REFERER"),
        origin=_env("REEL_SOURCE_ORIGIN"),
        user_agent=_env("REEL_SOURCE_USER_AGENT", "Mozilla/5.0"),
        cookie=_env("REEL_SOURCE_COOKIE"),
        workdir=Path(_env("REEL_WORKDIR", str(PROJECT_ROOT / ".work"))).expanduser(),
        outdir=Path(_env("REEL_OUTDIR", str(PROJECT_ROOT / "out"))).expanduser(),
        tts=(_env("REEL_TTS", "fake") or "fake").strip().lower(),
        aspect=normalise_aspect(_env("REEL_ASPECT", "vertical")),
        pre_roll_ms=_env_int("REEL_PRE_ROLL_MS", 350),
        post_roll_ms=_env_int("REEL_POST_ROLL_MS", 450),
        crf=_env_int("REEL_CRF", 20),
        preset=_env("REEL_PRESET", "medium"),
        max_segments=_env_int("REEL_MAX_SEGMENTS", 3),
        match_mode=_env("REEL_MATCH_MODE", "strict").lower(),
        context_enabled=_env("REEL_CONTEXT", "true").lower() in {"1", "true", "yes"},
        context_take=_env_int("REEL_CONTEXT_TAKE", 1),
        max_clip_ms=_env_int("REEL_MAX_CLIP_MS", 14_000),
        min_clip_ms=_env_int("REEL_MIN_CLIP_MS", 3_000),
        cut_mode=(_env("REEL_CUT_MODE", "line") or "line").strip().lower(),
        cut_pad_ms=_env_int("REEL_CUT_PAD_MS", 220),
        target_ms=_env_int("REEL_TARGET_MS", 20_000),
        content_rating=_csv("REEL_CONTENT_RATING", "SAFE,SUGGESTIVE"),
        categories=[c.upper() for c in _csv("REEL_CATEGORY", "ANIME,JDRAMA")],
        per_category=_env_int("REEL_PER_CATEGORY", 0),
        source_lang=_env("REEL_SOURCE_LANG", "ja").split()[0] if _env("REEL_SOURCE_LANG", "ja").strip() else "ja",
        font_ja=_env("REEL_FONT_JA", "Hiragino Sans"),
        font_en=_env("REEL_FONT_EN", "Helvetica"),
        font_card=_env("REEL_FONT_CARD", "Times New Roman"),
        dictionary_offline=_env("REEL_DICTIONARY_OFFLINE", "false").lower() in {"1", "true", "yes"},
        ffmpeg=_env("REEL_FFMPEG", "ffmpeg"),
        ffprobe=_env("REEL_FFPROBE", "ffprobe"),
    )
    settings.ensure_dirs()
    return settings


# ------------------------------------------------------- per-recipe / per-job resolution

#: Settings whose value lands in the manifest because it changes the output.
OUTPUT_SETTING_ENV: dict[str, str] = {
    "aspect": "REEL_ASPECT",
    "pre_roll_ms": "REEL_PRE_ROLL_MS",
    "post_roll_ms": "REEL_POST_ROLL_MS",
    "crf": "REEL_CRF",
    "preset": "REEL_PRESET",
    "cut_mode": "REEL_CUT_MODE",
    "cut_pad_ms": "REEL_CUT_PAD_MS",
    "target_ms": "REEL_TARGET_MS",
    "match_mode": "REEL_MATCH_MODE",
    "context_enabled": "REEL_CONTEXT",
    "context_take": "REEL_CONTEXT_TAKE",
    "min_clip_ms": "REEL_MIN_CLIP_MS",
    "max_clip_ms": "REEL_MAX_CLIP_MS",
    "font_ja": "REEL_FONT_JA",
    "font_en": "REEL_FONT_EN",
    "font_card": "REEL_FONT_CARD",
    "dictionary_offline": "REEL_DICTIONARY_OFFLINE",
}


def recipe_env_name(recipe_id: str, setting: str) -> str:
    """`REEL_<RECIPE>_<SETTING>` — a recipe-namespaced override."""
    token = recipe_id.upper().replace("-", "_")
    return f"REEL_{token}_{setting.upper()}"


def _coerce_env(raw: str, template: object) -> object:
    text = raw.strip()
    if isinstance(template, bool):
        return text.lower() in {"1", "true", "yes", "on"}
    if isinstance(template, int):
        try:
            return int(text)
        except ValueError:
            return template
    if isinstance(template, float):
        try:
            return float(text)
        except ValueError:
            return template
    return text


def resolve_output_config(
    recipe_id: str,
    settings: "Settings",
    overrides: dict[str, object] | None = None,
) -> tuple["Settings", dict[str, dict[str, object]]]:
    """Resolve output-affecting settings and record where each value came from.

    Resolution order: job config > `REEL_<RECIPE>_<SETTING>` > `REEL_<SETTING>` > default.
    """
    from .core.errors import InvalidInput

    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(OUTPUT_SETTING_ENV))
    if unknown:
        raise InvalidInput(
            f"unknown job config setting(s): {', '.join(unknown)}",
            hint="only output-affecting settings can be overridden; recipe inputs carry the rest",
            details={"unknown": unknown},
        )

    updates: dict[str, object] = {}
    provenance: dict[str, dict[str, object]] = {}
    for key, env_name in OUTPUT_SETTING_ENV.items():
        current = getattr(settings, key)
        if key in overrides:
            value, source = overrides[key], "job"
        else:
            recipe_raw = os.environ.get(recipe_env_name(recipe_id, key))
            if recipe_raw is not None and recipe_raw.strip():
                value, source = _coerce_env(recipe_raw, current), "recipe-env"
            elif os.environ.get(env_name, "").strip():
                value, source = current, "env"
            else:
                value, source = current, "default"
        updates[key] = value
        provenance[key] = {"value": value, "source": source}
    return dataclasses.replace(settings, **updates), provenance
