"""Japanese vocabulary: JLPT levels, readings, romaji and English meanings.

Two sources, deliberately split by what each is good at:

* **Levels and readings are bundled** (`data/jlpt_vocab.json`, 8,430 entries).
  A reel is built from a word, and the card above the video has to say which
  JLPT level it is, so that has to work offline and instantly.  The data is the
  tanos.co.uk JLPT lists (CC-BY Jonathan Waller), via the cleaned JSON/CSV
  release at github.com/Bluskyo/JLPT_Vocabulary.
* **Readings and meanings are looked up on demand** and cached.  No bundled
  Japanese-English dictionary is small enough to be worth shipping, and a reel
  only ever needs the handful of words it is actually about.

Romaji is generated rather than looked up: it is a pure function of the kana
reading, and a converter is smaller and more predictable than a fifth column.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(__file__).resolve().parent / "data"
JLPT_PATH = DATA_DIR / "jlpt_vocab.json"

JISHO_API = "https://jisho.org/api/v1/search/words"

#: Levels are the N-numbers: 5 = N5 (easiest) … 1 = N1 (hardest).
LEVELS = (5, 4, 3, 2, 1)


# --------------------------------------------------------------------- romaji


# Longest keys first at match time, so きゃ wins over き.
_KANA: dict[str, str] = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo", "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo", "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo", "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo", "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo", "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe",
    "ふぉ": "fo", "てぃ": "ti", "でぃ": "di", "うぇ": "we", "うぃ": "wi", "ゔぁ": "va",
    "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa", "ゔ": "vu",
    "ー": "",
}

_KEYS_BY_LENGTH = sorted(_KANA, key=len, reverse=True)


def _to_hiragana(text: str) -> str:
    """Fold katakana onto hiragana so one table covers both."""
    out = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:      # katakana block
            out.append(chr(code - 0x60))
        else:
            out.append(char)
    return "".join(out)


def kana_to_romaji(text: str) -> str:
    """Hepburn-ish romaji for a kana reading.

    `だんちょう` -> `danchou`.  Long vowels are spelled out (`ou`, not `ō`)
    because that is what a learner types and what the reference reels show.
    """
    source = _to_hiragana(text or "")
    out: list[str] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == "っ":                   # sokuon doubles the next consonant
            for key in _KEYS_BY_LENGTH:
                if source.startswith(key, index + 1):
                    romaji = _KANA[key]
                    out.append((romaji[0] if romaji else "") + romaji)
                    index += 1 + len(key)
                    break
            else:
                index += 1
            continue
        if char == "ん":
            # `ん` before a vowel or y-glide needs the apostrophe: しんいち -> shin'ichi
            nxt = source[index + 1: index + 2]
            out.append("n'" if nxt and _KANA.get(nxt, " ")[0] in "aeiouy" else "n")
            index += 1
            continue
        for key in _KEYS_BY_LENGTH:
            if source.startswith(key, index):
                out.append(_KANA[key])
                index += len(key)
                break
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _has_kana(text: str) -> bool:
    return any("\u3041" <= ch <= "\u30ff" for ch in text or "")


def romaji_display(text: str) -> str:
    """Title-cased romaji for a card heading: `danchou` -> `Danchou`."""
    romaji = kana_to_romaji(text)
    return romaji[:1].upper() + romaji[1:] if romaji else ""


# ------------------------------------------------------------------- the list


@dataclass(frozen=True, slots=True)
class VocabEntry:
    word: str                 # kanji / surface form, e.g. 約束
    reading: str = ""         # kana, e.g. やくそく
    level: int | None = None  # 5 = N5 … 1 = N1
    meaning: str = ""

    @property
    def romaji(self) -> str:
        """Kana reading as romaji, or "" when there is no reading to convert.

        Returning the kanji unchanged would put `団長` in the card's romaji
        slot; an empty string lets the card fall back to the headword instead.
        """
        if not _has_kana(self.reading):
            return ""
        return romaji_display(self.reading)

    @property
    def level_label(self) -> str:
        return f"N{self.level}" if self.level else ""

    @property
    def kana(self) -> str:
        """The reading, or the word itself when it is already kana."""
        return self.reading or self.word

    @property
    def has_kanji(self) -> bool:
        return any("\u4e00" <= c <= "\u9fff" for c in self.word)

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "reading": self.reading,
            "romaji": self.romaji,
            "meaning": self.meaning,
            "level": self.level,
            "levelLabel": self.level_label,
        }


@lru_cache(maxsize=1)
def load_jlpt() -> tuple[tuple[str, str, int], ...]:
    """The bundled `(word, reading, level)` rows."""
    try:
        raw = json.loads(JLPT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - only if the data is missing
        return ()
    return tuple((str(w), str(r), int(lv)) for w, r, lv in raw)


@lru_cache(maxsize=1)
def _indexes() -> tuple[
    dict[str, tuple[str, str, int]],
    dict[str, tuple[str, str, int]],
    dict[str, tuple[str, str, int]],
    int,
]:
    """Three lookup maps plus the longest key length.

    * `by_word`     — surface form -> row.  What `scan` matches on.
    * `by_reading`  — reading -> row, **kana-only entries only** (`あさって`).
      Kept narrow because `scan` slides a window over raw dialogue, where
      matching every kanji word by its reading would fire on any kana that
      happens to spell one.
    * `by_any_reading` — reading -> row for *every* entry.  Used by lookups,
      where the input is a known word rather than arbitrary text.  This is what
      lets a token's dictionary form `たたく` find the entry listed as `叩く`.

    On a collision the easiest level wins, so a word taught early is not
    labelled with a later, harder duplicate's level.
    """
    by_word: dict[str, tuple[str, str, int]] = {}
    by_reading: dict[str, tuple[str, str, int]] = {}
    by_any_reading: dict[str, tuple[str, str, int]] = {}
    longest = 0
    for row in load_jlpt():
        word, reading, level = row
        for table, key in ((by_word, word), (by_any_reading, reading)):
            current = table.get(key)
            if current is None or level > current[2]:
                table[key] = row
        if word == reading:
            current = by_reading.get(reading)
            if current is None or level > current[2]:
                by_reading[reading] = row
        longest = max(longest, len(word))
    return by_word, by_reading, by_any_reading, min(longest, 10)


def find_word(text: str, word: str) -> tuple[str, str, int] | None:
    """The JLPT row for `word`, by surface form first and then by reading.

    The reading fallback matters for mined tokens: a verb is tagged with its
    dictionary form in kana (`たたく`) while the list may hold it under kanji
    (`叩く`), with no surface form in common.
    """
    by_word, by_reading, by_any_reading, _ = _indexes()
    return by_word.get(word) or by_reading.get(word) or by_any_reading.get(word)


def level_of(word: str) -> int | None:
    row = find_word(word, word)
    return row[2] if row else None


def scan(
    text: str,
    *,
    levels: Iterable[int] | None = None,
    min_length: int = 1,
    kana_min_length: int = 2,
) -> list[VocabEntry]:
    """JLPT vocabulary occurring in `text`, longest match first.

    Used both to label a caption and to mine a downloaded episode for words
    worth making a reel about.  A sliding window over the bundled index needs
    no tokeniser — the corpus is short lines of dialogue, not prose.

    `levels` is the set of N-numbers to accept; remembering that 5 is N5 and 1
    is N1, "N5 and N4" is `{5, 4}`.  `kana_min_length` drops the one-character
    kana entries (`し`, `た`, `は`) that match constantly in real dialogue and
    would otherwise dominate a mining result.
    """
    if not text:
        return []
    allowed = set(levels) if levels is not None else None
    by_word, by_reading, _by_any_reading, longest = _indexes()
    found: dict[str, VocabEntry] = {}
    index = 0
    while index < len(text):
        for length in range(min(longest, len(text) - index), 0, -1):
            chunk = text[index:index + length]
            row = by_word.get(chunk) or by_reading.get(chunk)
            if row is None:
                continue
            word, reading, level = row
            if allowed is not None and level not in allowed:
                continue
            if len(word) < min_length:
                continue
            # A kana-only headword has to clear a higher bar than a kanji one:
            # `は` is a dictionary entry but never a word worth teaching here.
            if word == reading and len(word) < kana_min_length:
                continue
            if word not in found:
                found[word] = VocabEntry(word=word, reading=reading, level=level)
            index += length
            break
        else:
            index += 1
    return list(found.values())


# ------------------------------------------------------------------ dictionary


@dataclass(frozen=True, slots=True)
class Definition:
    reading: str = ""
    meaning: str = ""


class JishoDictionary:
    """Readings and English meanings from jisho.org, cached on disk.

    A reel needs a handful of words, so this is a lookup rather than a shipped
    Japanese-English dictionary (none is small enough to be worth bundling).
    Misses are cached too, so an unknown word is not re-requested every run.
    """

    def __init__(self, cache_path: Path, *, timeout: int = 20, offline: bool = False):
        self.cache_path = cache_path
        self.timeout = timeout
        self.offline = offline
        self._cache: dict[str, dict[str, str]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._cache = {
                str(k): (v if isinstance(v, dict) else {"meaning": str(v)})
                for k, v in (raw or {}).items()
            }
        except (OSError, ValueError):
            self._cache = {}

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8",
            )
            self._dirty = False
        except OSError:
            pass

    def lookup(self, word: str) -> Definition:
        """Reading + first English gloss for `word`, empty if unknown."""
        word = (word or "").strip()
        if not word:
            return Definition()
        hit = self._cache.get(word)
        if hit is None:
            if self.offline:
                return Definition()
            hit = self._fetch(word)
            self._cache[word] = hit
            self._dirty = True
            self.flush()
        return Definition(reading=hit.get("reading", ""), meaning=hit.get("meaning", ""))

    def meaning(self, word: str) -> str:
        return self.lookup(word).meaning

    def _fetch(self, word: str) -> dict[str, str]:
        url = f"{JISHO_API}?keyword={urllib.parse.quote(word)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return {"reading": "", "meaning": ""}
        # Jisho answers a kana query with every homophone it knows, so matches
        # are ranked in tiers rather than taking the first hit:
        #   1. the headword *is* the query            (約束 -> 約束)
        #   2. a kana-only headword reads as the query (もう -> もう)
        #   3. a kanji headword reads as the query     (ある -> 有る)
        # Without the tiers `もう` came back as `猛` ("greatly energetic") and
        # `ある` came back as nothing at all; within a tier the common entry
        # wins over a rarer homophone.
        exact: list[dict[str, Any]] = []
        kana: list[dict[str, Any]] = []
        by_reading: list[dict[str, Any]] = []
        for entry in payload.get("data") or []:
            for form in entry.get("japanese") or []:
                headword, reading = form.get("word"), form.get("reading") or ""
                if headword == word:
                    exact.append(entry)
                    break
                if not headword and reading == word:
                    kana.append(entry)
                    break
                if reading == word:
                    by_reading.append(entry)
                    break

        for tier in (exact, kana, by_reading):
            if not tier:
                continue
            tier.sort(key=lambda e: not e.get("is_common"))
            entry = tier[0]
            japanese = entry.get("japanese") or []
            reading = next(
                (j.get("reading") or "" for j in japanese
                 if (j.get("word") or j.get("reading")) == word),
                "",
            ) or next((j.get("reading") or "" for j in japanese), "")
            senses = entry.get("senses") or []
            glosses = (senses[0].get("english_definitions") or []) if senses else []
            return {"reading": reading, "meaning": ", ".join(glosses[:3])}
        return {"reading": "", "meaning": ""}


def describe(
    word: str,
    *,
    dictionary: JishoDictionary | None = None,
    reading: str = "",
    meaning: str = "",
) -> VocabEntry:
    """Everything the vocabulary card needs for `word`.

    The bundled list supplies the level and reading when it knows the word and
    the dictionary fills the gaps; anything neither knows still gets a card,
    just without a level, so a reel about a name or a rare compound is not
    blocked.  A reading from the bundled list wins when present: it is the one
    the JLPT level is keyed by.
    """
    row = find_word(word, word)
    found = dictionary.lookup(word) if dictionary else Definition()
    resolved_reading = (row[1] if row else "") or reading or found.reading or word
    return VocabEntry(
        word=word,
        reading=resolved_reading,
        level=row[2] if row else None,
        meaning=meaning or found.meaning,
    )


#: Parts of speech worth building a vocabulary reel around.  Everything else
#: the tokeniser emits — particles, copulas, auxiliaries, conjunctions,
#: interjections, suffixes, symbols, proper nouns — is grammar or noise, and
#: left in it swamps the ranking: `こと`, `そう` and `さん` are the three most
#: common "words" in any episode of dialogue.
CONTENT_POS = frozenset({"Noun", "Verb", "Adjective", "Adverb"})


def mine_tokens(
    tokens: Iterable[Any],
    *,
    dictionary: JishoDictionary | None = None,
    levels: Iterable[int] | None = None,
    limit: int | None = None,
    pos: Iterable[str] | None = None,
) -> list[tuple[VocabEntry, int]]:
    """`(entry, occurrences)` for the graded content words among `tokens`.

    This is the mining path that matters, because the tokeniser supplies two
    things raw text cannot: a **part of speech**, so particles can be dropped,
    and a **dictionary form**, so `よかった` is counted and displayed as `よい`
    rather than as whatever inflection happened to be spoken.
    """
    allowed = set(levels) if levels is not None else None
    wanted = set(pos) if pos is not None else set(CONTENT_POS)

    counts: dict[str, int] = {}
    readings: dict[str, str] = {}
    for token in _token_dicts(tokens):
        if wanted and token["pos"] not in wanted:
            continue
        word = (token["d"] or token["s"]).strip()
        if not word:
            continue
        row = find_word(word, word)
        if row is None:
            continue
        if allowed is not None and row[2] not in allowed:
            continue
        counts[word] = counts.get(word, 0) + 1
        readings[word] = row[1] or token["r"]

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit is not None:
        ranked = ranked[:limit]

    out: list[tuple[VocabEntry, int]] = []
    for word, count in ranked:
        entry = describe(word, dictionary=dictionary, reading=readings.get(word, ""))
        out.append((entry, count))
    # Most-said first, then easiest: a word repeated across an episode is the
    # one a learner will actually meet again.
    out.sort(key=lambda pair: (-pair[1], -(pair[0].level or 0), pair[0].word))
    return out


def _token_dicts(tokens: Iterable[Any]) -> list[dict[str, str]]:
    """Normalise pydantic tokens and plain dicts to one shape for mining."""
    out: list[dict[str, str]] = []
    for token in tokens or []:
        get = token.get if isinstance(token, dict) else (lambda k, t=token: getattr(t, k, None))
        surface = get("s")
        if not surface:
            continue
        out.append({
            "s": str(surface),
            "d": str(get("d") or ""),
            "r": str(get("r") or ""),
            "pos": str(get("posLabel") or ""),
        })
    return out


def mine(
    texts: Iterable[str],
    *,
    dictionary: JishoDictionary | None = None,
    levels: Iterable[int] | None = None,
    min_length: int = 2,
    limit: int | None = None,
) -> list[tuple[VocabEntry, int]]:
    """`(entry, occurrences)` for every JLPT word appearing across `texts`.

    This is what turns an already-downloaded episode into more reels: one pass
    over its dialogue yields every graded word in it, ranked by how often it is
    said.  Meanings are only looked up for entries that survive the cut, so
    mining a whole episode costs a handful of requests rather than thousands.
    """
    counts: dict[str, int] = {}
    rows: dict[str, tuple[str, str, int]] = {}
    for text in texts:
        for entry in scan(text, levels=levels, min_length=min_length):
            counts[entry.word] = counts.get(entry.word, 0) + 1
            rows[entry.word] = (entry.word, entry.reading, entry.level or 0)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -rows[kv[0]][2], kv[0]))
    if limit is not None:
        ranked = ranked[:limit]

    out: list[tuple[VocabEntry, int]] = []
    for word, count in ranked:
        _, reading, level = rows[word]
        entry = describe(word, dictionary=dictionary, reading=reading)
        if level and entry.level != level:
            entry = VocabEntry(
                word=entry.word, reading=entry.reading, level=level, meaning=entry.meaning
            )
        out.append((entry, count))
    # Most-said first, then easiest: a word repeated across an episode is the
    # one a learner will actually meet again.
    out.sort(key=lambda pair: (-pair[1], -(pair[0].level or 0), pair[0].word))
    return out
