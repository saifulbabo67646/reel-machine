"""Where example lines come from — the pluggable seam.

A `ClipSource` answers one question: *which lines of dialogue contain this word, and
where does the footage of them live?* It is deliberately the same shape as the
`subtitles` and `sources` groups next to it: a name, a `missing()` that reports unmet
requirements instead of failing a deployment, a `describe()` that `reel doctor` prints,
and one `find()` that returns plain data.

`wordreel`'s own answer is `library` — it reads subtitles you already have (sidecar
files or the tracks inside your own video) and cuts from that same footage. A
deployment that wants its examples from somewhere else registers another provider
through `reelmachine.clips`, exactly like every other extension point in this engine.

`find()` returns `ClipCandidate`s, not files: searching is cheap and must stay cheap,
because the recipe searches *before* it downloads or cuts anything. Nothing here
touches ffmpeg, the network, or a render.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

#: Subtitle languages this recipe searches by default. English-learning reels read
#: the English track; a bilingual deck can put another language first.
DEFAULT_LANGUAGES = ["en"]


class ClipRequest(BaseModel):
    """What a clip source is asked for."""

    model_config = ConfigDict(extra="forbid")

    word: str = ""
    match_mode: str = "smart"          # smart | exact | regex
    allow_inflected: bool = True       # accept *gave up* for `give up`
    languages: list[str] = Field(default_factory=lambda: list(DEFAULT_LANGUAGES))
    library: list[str] = Field(default_factory=list)   # directories to scan
    subtitles: list[str] = Field(default_factory=list)  # explicit subtitle files
    min_clip_ms: int = 2_000
    max_clip_ms: int = 12_000
    #: Spread the shortlist: aim for at most this many lines from one film, so a reel
    #: is not one voice six times. It is a preference, not a hard cap — a library with
    #: a single title still fills the reel rather than returning one clip.
    per_source: int = 1
    media_suffixes: list[str] = Field(default_factory=list)  # empty = the usual set


class ClipCandidate(BaseModel):
    """One line of dialogue that uses the target, and where its footage is.

    `start_ms`/`end_ms` are timings in the *source* file, already widened by the
    source's own pre/post roll — the cutter takes them as given.
    """

    model_config = ConfigDict(extra="forbid")

    text: str                          # the line as it is spoken
    media_path: str = ""               # the file to cut from; "" when nothing local has it
    media_label: str = ""              # what to print on the manifest
    subtitle_source: str = ""          # where the line was read from
    start_ms: int = 0
    end_ms: int = 0
    #: where the target sits inside `text`, as character offsets
    match_start: int = 0
    match_end: int = 0
    match_surface: str = ""            # the form that actually appeared
    exact: bool = False                # the surface form is literally the target
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "media": self.media_label,
            "mediaPath": self.media_path,
            "subtitleSource": self.subtitle_source,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "matched": self.match_surface,
            "exact": self.exact,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@runtime_checkable
class ClipSource(Protocol):
    """A searchable body of dialogue with playable footage behind it."""

    name: str

    def missing(self) -> list[str]:
        """Unmet requirements — a missing directory, key or binary."""

    def describe(self) -> dict[str, Any]:
        """A row for `reel doctor`: name, group, description, missing."""

    def suggest(self, request: ClipRequest, *, limit: int = 20) -> list[str]:
        """Teachable words this corpus is rich in, best first.

        This is what makes "let the agent choose the word" possible: it proposes from
        evidence (words that actually recur in the footage) instead of from a word
        list, so a suggested word is known to have examples behind it.
        """

    def find(self, request: ClipRequest, *, limit: int = 20) -> list[ClipCandidate]:
        """Lines matching `request.word`, best first. Never raises for "no match"."""


def describe_row(name: str, description: str, missing: list[str], **extra: Any) -> dict[str, Any]:
    """The `describe()` shape every provider group in this engine shares."""
    row: dict[str, Any] = {
        "name": name,
        "group": "clips",
        "description": description,
        "missing": list(missing),
    }
    row.update(extra)
    return row


def media_directories(raw: list[str]) -> list[Path]:
    """Expand and de-duplicate the `library` input, dropping paths that are not there."""
    seen: dict[str, Path] = {}
    for entry in raw:
        if not entry:
            continue
        path = Path(entry).expanduser()
        if path.is_dir():
            seen.setdefault(str(path.resolve()), path)
    return list(seen.values())
