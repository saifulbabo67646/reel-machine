"""post.md: the reel's caption, ready to paste (hook, summary, call to action, sources, hashtags).

It is built from the captions (`text`, else `say`), never from a `say` that has a separate `text`: for Hindi
`say` is the Devanagari spelling for the voice and is not what viewers read.
"""

from __future__ import annotations

import unicodedata

from ..mapreel.sources import _clean, _link
from .markup import is_unspaced, plain
from .models import Line, TechReelInputs

MAX_SUMMARY = 5
MIN_TAGS, MAX_TAGS, MAX_TITLE_TAGS = 8, 12, 4
BASE_TAGS: dict[str, tuple[str, ...]] = {
    "hi": ("#ai", "#tech", "#hindi", "#aitools", "#techhindi", "#opensource"),
    "en": ("#ai", "#tech", "#aitools", "#opensource", "#llm", "#coding"),
    "ja": ("#AI", "#テック", "#生成AI", "#プログラミング", "#オープンソース"),
}
FILLER_TAGS = ("#reels", "#shorts", "#technology", "#learnai", "#developer")  # top up a short set to MIN_TAGS
# English function words a title is full of and nobody searches for
STOPWORDS = frozenset({"a", "an", "and", "are", "can", "for", "from", "how", "into", "is", "its", "of", "on", "or",
                       "the", "to", "what", "when", "why", "with", "your", "you", "this", "that"})
LATIN_MIN = 4               # a Latin-script title word shorter than this is too generic to be a tag ("#old")
OTHER_MIN = 3               # any other script: Devanagari and kanji words are short


def _latin(word: str) -> bool:
    return all(not ch.isalpha() or "LATIN" in unicodedata.name(ch, "") for ch in word)


def _caption(line: Line) -> str:
    return _clean(plain(line.caption))


def _title_tags(title: str) -> list[str]:
    """Hashtags from the title's words: letters, marks and digits only (any script); English function words
    dropped; at least 4 characters for a Latin word, 3 for any other. Marks (category M) stay so Devanagari
    matras and viramas survive."""
    words = ["".join(ch for ch in raw if unicodedata.category(ch)[0] in "LMN") for raw in title.split()]
    return [f"#{w.lower()}" for w in words
            if w.lower() not in STOPWORDS and len(w) >= (LATIN_MIN if _latin(w) else OTHER_MIN)]


def hashtags(language: str, title: str) -> list[str]:
    """8 to 12 tags: the language's base set, up to 4 from the title, topped up if still short."""
    base = BASE_TAGS.get(language.split("-")[0].lower(), BASE_TAGS["en"])
    seen = {t.casefold() for t in base}
    extra: list[str] = []
    for tag in _title_tags(title):
        if tag.casefold() not in seen and len(extra) < MAX_TITLE_TAGS:
            seen.add(tag.casefold())
            extra.append(tag)
    tags = [*base, *extra]
    for tag in FILLER_TAGS:
        if len(tags) >= MIN_TAGS:
            break
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)
    return tags[:MAX_TAGS]


def post_markdown(inputs: TechReelInputs) -> str:
    beats = inputs.beats
    join = "" if is_unspaced(inputs.language) else " "     # Japanese sentences run on without a space
    # the schema needs a beat; a model built around it still gets a post, from the title
    out = [_caption(beats[0].lines[0]) if beats else _clean(inputs.title), ""]
    middle = [b for b in beats[1:] if b.kind != "cta"]
    out += [f"- {_caption(b.lines[0])}" for b in middle[:MAX_SUMMARY]]
    if middle:
        out.append("")
    for beat in beats[1:]:
        if beat.kind == "cta":
            out += [join.join(_caption(line) for line in beat.lines), ""]
    if inputs.sources:
        out += ["Sources:", *[f"- {_link(s)}" for s in inputs.sources], ""]
    out.append(" ".join(hashtags(inputs.language, inputs.title)))
    return "\n".join(out) + "\n"
