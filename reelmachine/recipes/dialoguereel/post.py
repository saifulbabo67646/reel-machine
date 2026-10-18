"""post.md: the reel's caption, ready to paste (hook, dialogue with readings and meanings, words, hashtags)."""

from __future__ import annotations

import re

from ..mapreel.sources import _clean
from .cast import ResolvedCast
from .compose import _recap, prompt_for
from .levels import n_level, primary_language
from .models import DialogueReelInputs
from .ruby import parse_ruby, plain_text
from .strings import strings_for

MAX_TAGS = 10
BASE_TAGS: dict[str, tuple[str, ...]] = {
    "ja": ("#learnjapanese", "#japanese", "#日本語", "#nihongo", "#japaneselanguage", "#studyjapanese"),
    "ko": ("#learnkorean", "#korean", "#한국어", "#koreanlanguage", "#studykorean"),
    "es": ("#learnspanish", "#spanish", "#español", "#spanishlanguage", "#aprenderespañol"),
    "zh": ("#learnchinese", "#chinese", "#中文", "#mandarin", "#studychinese"),
}
OTHER_TAGS = ("#languagelearning", "#learnlanguages", "#vocabulary", "#language", "#polyglot")


def _level_tags(primary: str, level: str) -> list[str]:
    flat = "".join(level.split())
    if primary == "ja":
        n = n_level(flat)
        return [f"#JLPTN{n}", "#jlpt"] if n else []
    if primary == "ko":
        return [f"#{flat}"] if flat.upper().startswith("TOPIK") else []
    if primary == "es":
        return [f"#{flat}"] if re.fullmatch(r"[ABC][12]", flat) else []
    if primary == "zh":
        return [f"#{flat}"] if re.fullmatch(r"HSK\d", flat) else []
    return []


def hashtags(language: str, level: str) -> list[str]:
    """5 to 10 hashtags for the reel's language and level, each without spaces."""
    primary = primary_language(language)
    tags = [*BASE_TAGS.get(primary, OTHER_TAGS), *_level_tags(primary, level), "#languagelearning"]
    return list(dict.fromkeys(tags))[:MAX_TAGS]


def post_markdown(inputs: DialogueReelInputs, cast: ResolvedCast) -> str:
    """The caption: hook, dialogue, words from the reel, the call to action and the hashtags."""
    words = strings_for(inputs.meaning_language, inputs.level)
    names = {c.id: c.name for c in cast.characters}
    hook = _clean(inputs.title) + (f" {_clean(inputs.scene)}" if inputs.scene else "")
    out = [hook.strip()]
    if prompt := prompt_for(inputs.meaning_language):
        out.append(_clean(prompt))
    out += ["", f"**{words['dialogue']}**"]
    for line in inputs.lines:
        out.append(f"{_clean(names.get(line.speaker, line.speaker))}: {_clean(plain_text(parse_ruby(line.text)[0]))}")
        if line.reading:
            out.append(_clean(line.reading))
        out += [f"→ {_clean(line.meaning)}", ""]
    recap = _recap(inputs)
    if recap:
        out.append(f"**{words['recap']}**")
        for card in recap:
            item = f"- {_clean(card['text'])}"
            if card["reading"]:
                item += f" ({_clean(card['reading'])})"
            item += f" — {_clean(card['meaning'])}"
            out.append(item + (f" · N{card['level']}" if card["level"] else ""))
        out.append("")
    out += [_clean(words["cta"]), _clean(words["follow"]), "", " ".join(hashtags(inputs.language, inputs.level))]
    return "\n".join(out) + "\n"
