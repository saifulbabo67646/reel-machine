"""Vocabulary: romaji, JLPT levels, mining and the dictionary tiers.

No network: the dictionary tests inject a cache file, and the tier logic is
exercised through a fake `_fetch` payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reelmachine.models import Token
from reelmachine.vocab import (
    CONTENT_POS,
    JishoDictionary,
    VocabEntry,
    describe,
    find_word,
    kana_to_romaji,
    level_of,
    load_jlpt,
    mine_tokens,
    romaji_display,
    scan,
)


# -------------------------------------------------------------------- romaji


@pytest.mark.parametrize(
    "kana,expected",
    [
        ("だんちょう", "danchou"),
        ("やくそく", "yakusoku"),
        ("がっこう", "gakkou"),        # sokuon doubles the consonant
        ("しんいち", "shin'ichi"),     # ん before a vowel takes an apostrophe
        ("コーヒー", "kohi"),          # katakana folds onto hiragana
        ("きょう", "kyou"),
        ("まって", "matte"),
        ("", ""),
    ],
)
def test_kana_to_romaji(kana: str, expected: str) -> None:
    assert kana_to_romaji(kana) == expected


def test_romaji_display_title_cases() -> None:
    assert romaji_display("やくそく") == "Yakusoku"
    assert romaji_display("") == ""


# ----------------------------------------------------------------- the list


def test_bundled_list_loads_and_is_shaped_correctly() -> None:
    rows = load_jlpt()
    assert len(rows) > 5000
    assert all(isinstance(w, str) and isinstance(r, str) and lv in {1, 2, 3, 4, 5}
               for w, r, lv in rows)


@pytest.mark.parametrize(
    "word,level",
    [("私", 5), ("あさって", 5), ("約束", 4), ("嗚呼", 1)],
)
def test_levels_are_n_numbered_not_inverted(word: str, level: int) -> None:
    """5 is N5 (easiest) and 1 is N1 (hardest) — the mapping is easy to flip."""
    assert level_of(word) == level
    assert find_word(word, word)[2] == level


def test_find_word_matches_on_the_reading_too() -> None:
    assert find_word("やくそく", "やくそく") is not None


def test_one_word_with_two_kana_spellings_is_shown_as_both() -> None:
    """The bundled list keys やっぱり/やはり as one entry; the card shows both."""
    entry = describe("やっぱり")
    assert entry.level == 4
    assert entry.has_kanji is False
    assert entry.two_kana_forms is True
    assert entry.kana_display == "やはり/やっぱり"
    assert entry.romaji_display == "Yahari/Yappari"

    # a kanji word keeps its reading/word split exactly as before
    promise = describe("約束")
    assert promise.two_kana_forms is False
    assert promise.kana_display == "やくそく"
    assert promise.romaji_display == "Yakusoku"

    # a single kana word is unchanged too
    kana = describe("これ")
    assert kana.kana_display == kana.kana


# -------------------------------------------------------------------- scanning


def test_scan_finds_graded_words_and_respects_levels() -> None:
    line = "確かに昔 そんな約束をしたのかもしれないけど"
    everything = {e.word for e in scan(line)}
    assert {"確か", "昔", "約束"} <= everything

    n4_only = {e.word for e in scan(line, levels={4})}
    assert "約束" in n4_only
    assert all(e.level == 4 for e in scan(line, levels={4}))

    # Nothing in that line is N5, so an N5-only scan is empty rather than
    # silently returning everything.
    assert scan(line, levels={5}) == []


def test_scan_drops_single_kana_entries_by_default() -> None:
    """`し` and `た` are dictionary entries and would match every line."""
    noisy = scan("わたしは", kana_min_length=1)
    quiet = scan("わたしは", kana_min_length=2)
    assert len(quiet) <= len(noisy)
    assert all(len(e.word) > 1 for e in quiet)


# ------------------------------------------------------------------- mining


def _tok(surface: str, reading: str, pos: str, dictionary: str = "") -> Token:
    return Token(s=surface, r=reading, p=None, posLabel=pos, d=dictionary or None, b=0, e=len(surface))


def test_mine_tokens_keeps_content_words_and_drops_grammar() -> None:
    """Particles and copulas swamp a raw word count; POS is what removes them."""
    tokens = [
        _tok("約束", "ヤクソク", "Noun"),
        _tok("は", "ハ", "Particle"),
        _tok("し", "シ", "Verb", dictionary="する"),
        _tok("た", "タ", "Auxiliary"),
        _tok("です", "デス", "Copula"),
        _tok("それ", "ソレ", "Pronoun"),
        _tok("網代", "アジロ", "Proper noun"),
        _tok("約束", "ヤクソク", "Noun"),
    ]
    got = mine_tokens(tokens)
    words = [entry.word for entry, _ in got]
    assert "約束" in words
    for noise in ("は", "た", "です", "それ", "網代"):
        assert noise not in words, f"{noise} is not a word worth a reel"
    assert dict((e.word, n) for e, n in got)["約束"] == 2


def test_mine_tokens_uses_the_dictionary_form() -> None:
    """`よかった` must be counted and shown as `よい`, not as the inflection."""
    tokens = [
        _tok("よかった", "ヨカッタ", "Adjective", dictionary="よい"),
        _tok("よかった", "ヨカッタ", "Adjective", dictionary="よい"),
        _tok("たたきたい", "タタキタイ", "Verb", dictionary="たたく"),
    ]
    got = {entry.word: count for entry, count in mine_tokens(tokens)}
    assert got.get("よい") == 2
    assert got.get("たたく") == 1
    assert "よかった" not in got


def test_mine_tokens_honours_the_pos_allowlist() -> None:
    tokens = [_tok("約束", "ヤクソク", "Noun"), _tok("走る", "ハシル", "Verb")]
    only_nouns = {e.word for e, _ in mine_tokens(tokens, pos={"Noun"})}
    assert only_nouns == {"約束"}
    assert "Noun" in CONTENT_POS and "Particle" not in CONTENT_POS


# --------------------------------------------------------------- dictionary


class _FakeJisho(JishoDictionary):
    """A dictionary whose HTTP layer is replaced by a canned payload."""

    def __init__(self, payload: dict, tmp_path: Path):
        self.payload = payload
        super().__init__(tmp_path / "dict.json")

    def _fetch(self, word: str) -> dict[str, str]:
        return super()._fetch(word)


def _entry(headword: str | None, reading: str, *defs: str, common: bool = True) -> dict:
    return {
        "is_common": common,
        "japanese": [{"word": headword, "reading": reading}],
        "senses": [{"english_definitions": list(defs)}],
    }


def test_dictionary_prefers_the_exact_headword(tmp_path: Path, monkeypatch) -> None:
    payload = {"data": [_entry("銅", "どう", "copper"), _entry("約束", "やくそく", "promise")]}
    d = JishoDictionary(tmp_path / "d.json")
    monkeypatch.setattr(d, "_fetch", lambda w: JishoDictionary._fetch(d, w))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _Resp(json.dumps(payload).encode()),
    )
    assert d.lookup("約束").meaning == "promise"


def test_dictionary_prefers_a_kana_headword_over_a_homophone(tmp_path: Path, monkeypatch) -> None:
    payload = {"data": [_entry("猛", "もう", "greatly energetic"), _entry(None, "もう", "already")]}
    d = JishoDictionary(tmp_path / "d.json")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    assert d.lookup("もう").meaning == "already"


def test_dictionary_falls_back_to_a_reading_match(tmp_path: Path, monkeypatch) -> None:
    """`ある` has no kana headword; its entry is 有る, read ある."""
    payload = {"data": [_entry("有る", "ある", "to be", "to exist")]}
    d = JishoDictionary(tmp_path / "d.json")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    got = d.lookup("ある")
    assert got.meaning == "to be, to exist"
    assert got.reading == "ある"


def test_dictionary_prefers_the_common_entry_within_a_tier(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "data": [
            _entry(None, "ちょっと", "rare sense", common=False),
            _entry(None, "ちょっと", "a little", common=True),
        ]
    }
    d = JishoDictionary(tmp_path / "d.json")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(json.dumps(payload).encode()))
    assert d.lookup("ちょっと").meaning == "a little"


def test_dictionary_caches_misses_and_hits(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    d = JishoDictionary(path, offline=True)
    assert d.lookup("何か").meaning == ""
    # A second instance sees the flushed cache rather than re-requesting.
    d._cache["何か"] = {"reading": "なにか", "meaning": "something"}
    d._dirty = True
    d.flush()
    assert JishoDictionary(path).lookup("何か").meaning == "something"


def test_describe_still_builds_a_card_for_unknown_words(tmp_path: Path) -> None:
    """A word outside the JLPT list gets a card, just without a level."""
    entry = describe("団長", dictionary=JishoDictionary(tmp_path / "d.json", offline=True))
    assert isinstance(entry, VocabEntry)
    assert entry.word == "団長"
    assert entry.level is None and entry.level_label == ""
    # No reading available offline, so no romaji — echoing the kanji back into
    # the romaji slot would be worse than leaving it empty.
    assert entry.romaji == ""


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None
