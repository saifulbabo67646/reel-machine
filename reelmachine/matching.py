"""Deciding whether a clip actually says the word being taught.

Nadeshiko searches by *lexeme*: a query for `やっぱり` also returns `やはり` and
`やっぱ`, marking the matched text with `<em>`.  That is correct for a dictionary
and wrong for a reel titled with one word, where the audio has to say that word.

The distinction is made on the marked span rather than on the raw content,
because the span is what the API actually matched:

* `やっぱり` -> exact
* `やっぱ`   -> a contraction; shares the query as a prefix
* `やはり`   -> a different pronunciation; dropped
* `食べた`   -> an inflection of a search for `食べる`; kept via its dictionary form
"""

from __future__ import annotations

from typing import Iterable

from .models import Segment

MATCH_MODES = ("strict", "api")

_TAG_RE = None


def _tag_re():
    global _TAG_RE
    if _TAG_RE is None:
        import re

        _TAG_RE = re.compile(r"<em>(.*?)</em>", re.IGNORECASE | re.DOTALL)
    return _TAG_RE


def matched_forms(segment: Segment) -> list[str]:
    """The text spans the API flagged as matching, if any.

    An empty list means "the API marked nothing", which is different from
    "the API marked something that is not the word".
    """
    return [span.strip() for span in _tag_re().findall(segment.textJa.highlight or "") if span.strip()]


def dictionary_forms(segment: Segment, form: str) -> set[str]:
    """Dictionary forms of the tokens overlapping `form`.

    The marked span and the token boundaries do not line up: Elasticsearch marks
    the whole inflected surface (`食べた`) while UniDic tokenises it as `食べ` +
    `た`, so overlapping surfaces are collected rather than exact ones.
    """
    out: set[str] = set()
    for token in segment.textJa.tokens or []:
        surface = token.s or ""
        if not surface:
            continue
        if surface == form or surface in form or form in surface:
            if token.d:
                out.add(token.d)
            out.add(surface)
    return out


def accepted_forms(segment: Segment, word: str) -> list[str]:
    """Matched spans that count as the searched word."""
    forms = matched_forms(segment)
    if not forms:
        # No highlight available (segment not from a search); fall back to text.
        return [word] if word and word in segment.textJa.content else []

    accepted: list[str] = []
    for form in forms:
        if form == word:
            accepted.append(form)
            continue
        # A contraction or a partial surface: `やっぱ` for `やっぱり`, `食べ` for `食べる`.
        if word and (form in word or word in form):
            accepted.append(form)
            continue
        # An inflected form carries the query in its dictionary form.
        if word and word in dictionary_forms(segment, form):
            accepted.append(form)
    return accepted


def is_word_match(segment: Segment, word: str, mode: str = "strict") -> bool:
    if mode == "api":
        return True
    return bool(accepted_forms(segment, word))


def describe(segment: Segment, word: str) -> str:
    """Short label for the manifest: what the clip actually says."""
    forms = matched_forms(segment)
    if not forms:
        return "unmarked"
    accepted = accepted_forms(segment, word)
    return ", ".join(forms) + ("" if accepted else "  (variant, not the taught form)")
