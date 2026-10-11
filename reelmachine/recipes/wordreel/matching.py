"""Finding an English word or phrase inside a line of dialogue.

The Japanese pipeline matches a query against a corpus that already knows how to
mark up its own hits. English subtitles know nothing: they are plain text, and the
word a learner wants almost never appears in the form they typed. Ask for `give up`
and the line says *gave up*; ask for `run` and it says *running*, or *ran*.

So the unit of matching here is not a string, it is a **token with variants**. Each
token of the target is expanded into every surface form it can plausibly take
(regular inflection, consonant doubling, `y → ies`, and an irregular table for the
verbs and nouns that ignore all of that), and a match is a run of adjacent tokens
where token *i* is one of the variants of target token *i*. A single word is just
the one-token case of the same rule, which is why `run` finds *running* and
`give up` finds *gave up* through one code path.

`mode` is the escape hatch. `smart` (the default) expands; `exact` matches the
literal text, case-insensitively; `regex` hands the whole thing to the caller — for
a pattern like `\\bmind\\b.*\\bblow\\b`, or a phrase a teacher wants verbatim.

Nothing here needs a corpus, a dictionary or the network, and nothing here knows
what a reel is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from ...core.timing import proportional_word_spans
from ...core.timeline import WordSpan

#: A token is letters/digits plus internal apostrophes and hyphens, so `don't`,
#: `mother-in-law` and `1990s` each stay one token. Everything else separates.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019\-][A-Za-z0-9]+)*")

#: Words that are never worth teaching on their own — a hit on one of these is a
#: false positive that would otherwise fill a reel with *the*, *of* and *is*.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if of to in on at by for with from as is are was were be been
    being am do does did done have has had having will would shall should can could
    may might must this that these those it its he she they them his her their you
    your i me my we our us not no nor so than then there here who whom whose which
    what when where why how all any both each few more most other some such only own
    same too very just now also into over under again further once during about
    """.split()
)

#: Verbs and nouns whose forms cannot be derived by rule.
IRREGULAR: dict[str, tuple[str, ...]] = {
    # be / have / do
    "be": ("be", "am", "is", "are", "was", "were", "been", "being"),
    "have": ("have", "has", "had", "having"),
    "do": ("do", "does", "did", "done", "doing"),
    # high-frequency irregular verbs
    "go": ("go", "goes", "went", "gone", "going"),
    "say": ("say", "says", "said", "saying"),
    "get": ("get", "gets", "got", "gotten", "getting"),
    "make": ("make", "makes", "made", "making"),
    "know": ("know", "knows", "knew", "known", "knowing"),
    "think": ("think", "thinks", "thought", "thinking"),
    "take": ("take", "takes", "took", "taken", "taking"),
    "see": ("see", "sees", "saw", "seen", "seeing"),
    "come": ("come", "comes", "came", "coming"),
    "give": ("give", "gives", "gave", "given", "giving"),
    "find": ("find", "finds", "found", "finding"),
    "tell": ("tell", "tells", "told", "telling"),
    "become": ("become", "becomes", "became", "becoming"),
    "leave": ("leave", "leaves", "left", "leaving"),
    "feel": ("feel", "feels", "felt", "feeling"),
    "put": ("put", "puts", "putting"),
    "bring": ("bring", "brings", "brought", "bringing"),
    "begin": ("begin", "begins", "began", "begun", "beginning"),
    "keep": ("keep", "keeps", "kept", "keeping"),
    "hold": ("hold", "holds", "held", "holding"),
    "write": ("write", "writes", "wrote", "written", "writing"),
    "stand": ("stand", "stands", "stood", "standing"),
    "hear": ("hear", "hears", "heard", "hearing"),
    "let": ("let", "lets", "letting"),
    "mean": ("mean", "means", "meant", "meaning"),
    "set": ("set", "sets", "setting"),
    "meet": ("meet", "meets", "met", "meeting"),
    "run": ("run", "runs", "ran", "running"),
    "pay": ("pay", "pays", "paid", "paying"),
    "sit": ("sit", "sits", "sat", "sitting"),
    "speak": ("speak", "speaks", "spoke", "spoken", "speaking"),
    "lie": ("lie", "lies", "lay", "lain", "lying"),
    "lead": ("lead", "leads", "led", "leading"),
    "read": ("read", "reads", "reading"),
    "grow": ("grow", "grows", "grew", "grown", "growing"),
    "lose": ("lose", "loses", "lost", "losing"),
    "fall": ("fall", "falls", "fell", "fallen", "falling"),
    "send": ("send", "sends", "sent", "sending"),
    "build": ("build", "builds", "built", "building"),
    "understand": ("understand", "understands", "understood", "understanding"),
    "draw": ("draw", "draws", "drew", "drawn", "drawing"),
    "break": ("break", "breaks", "broke", "broken", "breaking"),
    "spend": ("spend", "spends", "spent", "spending"),
    "cut": ("cut", "cuts", "cutting"),
    "rise": ("rise", "rises", "rose", "risen", "rising"),
    "drive": ("drive", "drives", "drove", "driven", "driving"),
    "buy": ("buy", "buys", "bought", "buying"),
    "wear": ("wear", "wears", "wore", "worn", "wearing"),
    "choose": ("choose", "chooses", "chose", "chosen", "choosing"),
    "eat": ("eat", "eats", "ate", "eaten", "eating"),
    "drink": ("drink", "drinks", "drank", "drunk", "drinking"),
    "sing": ("sing", "sings", "sang", "sung", "singing"),
    "swim": ("swim", "swims", "swam", "swum", "swimming"),
    "teach": ("teach", "teaches", "taught", "teaching"),
    "catch": ("catch", "catches", "caught", "catching"),
    "fight": ("fight", "fights", "fought", "fighting"),
    "seek": ("seek", "seeks", "sought", "seeking"),
    "sell": ("sell", "sells", "sold", "selling"),
    "win": ("win", "wins", "won", "winning"),
    "forget": ("forget", "forgets", "forgot", "forgotten", "forgetting"),
    "forgive": ("forgive", "forgives", "forgave", "forgiven", "forgiving"),
    "freeze": ("freeze", "freezes", "froze", "frozen", "freezing"),
    "hide": ("hide", "hides", "hid", "hidden", "hiding"),
    "ride": ("ride", "rides", "rode", "ridden", "riding"),
    "shake": ("shake", "shakes", "shook", "shaken", "shaking"),
    "light": ("light", "lights", "lit", "lighted", "lighting"),
    "learn": ("learn", "learns", "learned", "learnt", "learning"),
    "dream": ("dream", "dreams", "dreamed", "dreamt", "dreaming"),
    "shine": ("shine", "shines", "shone", "shined", "shining"),
    "shoot": ("shoot", "shoots", "shot", "shooting"),
    "hit": ("hit", "hits", "hitting"),
    "cost": ("cost", "costs", "costing"),
    "beat": ("beat", "beats", "beaten", "beating"),
    "spell": ("spell", "spells", "spelled", "spelt", "spelling"),
    "smell": ("smell", "smells", "smelled", "smelt", "smelling"),
    "spill": ("spill", "spills", "spilled", "spilt", "spilling"),
    "leap": ("leap", "leaps", "leaped", "leapt", "leaping"),
    "kneel": ("kneel", "kneels", "knelt", "kneeling"),
    "lay": ("lay", "lays", "laid", "laying"),
    "mistake": ("mistake", "mistakes", "mistook", "mistaken", "mistaking"),
    "slide": ("slide", "slides", "slid", "sliding"),
    "swing": ("swing", "swings", "swung", "swinging"),
    "sting": ("sting", "stings", "stung", "stinging"),
    "split": ("split", "splits", "splitting"),
    "spit": ("spit", "spits", "spat", "spitting"),
    "cast": ("cast", "casts", "casting"),
    "burst": ("burst", "bursts", "bursting"),
    "shed": ("shed", "sheds", "shedding"),
    "withdraw": ("withdraw", "withdraws", "withdrew", "withdrawn", "withdrawing"),
    "overcome": ("overcome", "overcomes", "overcame", "overcoming"),
    "steal": ("steal", "steals", "stole", "stolen", "stealing"),
    "throw": ("throw", "throws", "threw", "thrown", "throwing"),
    "wake": ("wake", "wakes", "woke", "woken", "waking"),
    "fly": ("fly", "flies", "flew", "flown", "flying"),
    "sleep": ("sleep", "sleeps", "slept", "sleeping"),
    "sweep": ("sweep", "sweeps", "swept", "sweeping"),
    "weep": ("weep", "weeps", "wept", "weeping"),
    "creep": ("creep", "creeps", "crept", "creeping"),
    "deal": ("deal", "deals", "dealt", "dealing"),
    "feed": ("feed", "feeds", "fed", "feeding"),
    "flee": ("flee", "flees", "fled", "fleeing"),
    "hang": ("hang", "hangs", "hung", "hanging"),
    "strike": ("strike", "strikes", "struck", "striking"),
    "swear": ("swear", "swears", "swore", "sworn", "swearing"),
    "tear": ("tear", "tears", "tore", "torn", "tearing"),
    "bite": ("bite", "bites", "bit", "bitten", "biting"),
    "blow": ("blow", "blows", "blew", "blown", "blowing"),
    "burn": ("burn", "burns", "burnt", "burned", "burning"),
    "dig": ("dig", "digs", "dug", "digging"),
    "fit": ("fit", "fits", "fitting"),
    "hurt": ("hurt", "hurts", "hurting"),
    "lend": ("lend", "lends", "lent", "lending"),
    "quit": ("quit", "quits", "quitting"),
    "shut": ("shut", "shuts", "shutting"),
    "spread": ("spread", "spreads", "spreading"),
    "stick": ("stick", "sticks", "stuck", "sticking"),
    "spin": ("spin", "spins", "spun", "spinning"),
    "spring": ("spring", "springs", "sprang", "sprung", "springing"),
    "stink": ("stink", "stinks", "stank", "stunk", "stinking"),
    "sink": ("sink", "sinks", "sank", "sunk", "sinking"),
    # irregular plurals
    "man": ("man", "men", "man's", "men's"),
    "woman": ("woman", "women", "woman's", "women's"),
    "child": ("child", "children", "child's", "children's"),
    "person": ("person", "people", "persons", "person's", "people's"),
    "foot": ("foot", "feet", "foot's", "feet's"),
    "tooth": ("tooth", "teeth"),
    "mouse": ("mouse", "mice"),
    "goose": ("goose", "geese"),
    "life": ("life", "lives", "life's"),
    "knife": ("knife", "knives"),
    "wife": ("wife", "wives", "wife's"),
    "leaf": ("leaf", "leaves"),
    "half": ("half", "halves"),
    "shelf": ("shelf", "shelves"),
    "wolf": ("wolf", "wolves"),
    "thief": ("thief", "thieves"),
    "loaf": ("loaf", "loaves"),
    "self": ("self", "selves"),
    "scarf": ("scarf", "scarves"),
    "sheep": ("sheep",),
    "deer": ("deer",),
    "fish": ("fish", "fishes"),
    "ox": ("ox", "oxen"),
    # comparatives and function words worth teaching
    "good": ("good", "better", "best"),
    "bad": ("bad", "worse", "worst"),
    "far": ("far", "farther", "farthest", "further", "furthest"),
    "little": ("little", "less", "least"),
    "much": ("much", "more", "most"),
    "many": ("many", "more", "most"),
    "old": ("old", "older", "oldest", "elder", "eldest"),
}

#: Spellings that differ only by the apostrophe/hyphen we fold away.
_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": "", "\u201d": ""})


def normalise(text: str) -> str:
    """Lowercase and fold curly quotes, so `Don’t` and `don't` are the same token."""
    return (text or "").translate(_FOLD).strip().lower()


def tokenise(text: str) -> list["Token"]:
    """Split into tokens that remember where they were in the original string."""
    out: list[Token] = []
    for match in _TOKEN_RE.finditer(text or ""):
        surface = match.group(0)
        out.append(
            Token(text=surface, norm=normalise(surface), start=match.start(), end=match.end())
        )
    return out


def _regular_forms(word: str) -> set[str]:
    """The rule-governed inflections of an English word.

    Deliberately *not* included: `-er` / `-est`. They look like comparatives but they
    are also how English builds agent nouns, and an agent noun is a different word —
    expanding `run` into `runner` or `teach` into `teacher` would put a clip on screen
    that teaches something the reel did not ask for. A learner searching `big` misses
    `bigger`, which costs one example; a learner searching `run` seeing *runner*
    taught as `run` is simply wrong. Comparative forms that are irregular (`good` →
    `better`) are curated in `IRREGULAR` and still match.
    """
    forms = {word}
    if not word:
        return forms

    # plural / third person singular
    if word.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(word + "es")
    elif word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ies")
    else:
        forms.add(word + "s")

    # past tense / past participle
    if word.endswith("e"):
        forms.add(word + "d")
    elif word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ied")
    elif (
        len(word) >= 3
        and word[-1] not in "aeiouwxy"
        and word[-2] in "aeiou"
        and word[-3] not in "aeiou"
    ):
        forms.add(word + word[-1] + "ed")  # stop → stopped
    else:
        forms.add(word + "ed")

    # present participle: drop a silent e, double a final consonant after one vowel
    if word.endswith("e") and not word.endswith("ee"):
        forms.add(word[:-1] + "ing")
    elif (
        len(word) >= 3
        and word[-1] not in "aeiouwxy"
        and word[-2] in "aeiou"
        and word[-3] not in "aeiou"
    ):
        forms.add(word + word[-1] + "ing")
    else:
        forms.add(word + "ing")

    # possessive
    forms.add(word + "'s" if not word.endswith("s") else word + "'")
    return {form for form in forms if form}


def variants(word: str) -> frozenset[str]:
    """Every surface form a target word could legitimately appear as.

    An irregular entry is *exhaustive and authoritative*: it was written by hand, it
    already lists the regular forms that really exist (`run` → `runs`, `running`),
    and the rules are not applied on top of it — that is what keeps `runed` and
    `runner` out of the set. Regular words get the safe rule-generated forms only.
    """
    key = normalise(word)
    if not key:
        return frozenset()
    if key in IRREGULAR:
        return frozenset({key, *IRREGULAR[key]})
    return frozenset(_regular_forms(key))


@dataclass(frozen=True, slots=True)
class Token:
    """One word of a cue, with its character offsets in the original text."""

    text: str   # the surface form, exactly as it appears
    norm: str   # folded for comparison
    start: int  # char offset of the first character
    end: int    # char offset one past the last

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Match:
    """One occurrence of the target, located in both characters and tokens."""

    start: int        # char offset of the matched text
    end: int          # char offset one past it
    surface: str      # the matched text, as it appears in the line
    token_start: int  # index of the first matched token
    token_end: int    # index one past the last matched token
    exact: bool       # True when the surface form is literally the target

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def token_count(self) -> int:
        return self.token_end - self.token_start


class WordMatcher:
    """Match one English word or phrase against lines of dialogue.

    Build it once and reuse it: the variant sets are computed in the constructor.
    """

    def __init__(self, target: str, *, mode: str = "smart", case_sensitive: bool = False) -> None:
        self.target = (target or "").strip()
        self.mode = (mode or "smart").strip().lower()
        if self.mode not in ("smart", "exact", "regex"):
            raise ValueError(f"unknown match mode {mode!r} (smart | exact | regex)")
        self.case_sensitive = case_sensitive
        self.phrase = tokenise(self.target)
        if not self.phrase and self.mode != "regex":
            raise ValueError("the target word or phrase is empty")
        self._target_norm = normalise(self.target)
        #: variant set per phrase token — the whole matcher in one structure
        self._expansions: tuple[frozenset[str], ...] = tuple(
            variants(token.norm) for token in self.phrase
        )
        self._regex = None
        if self.mode == "regex" and self.target:
            try:
                self._regex = re.compile(self.target, 0 if case_sensitive else re.IGNORECASE)
            except re.error as exc:
                # a bad pattern is a bad argument, and callers (the probe, the MCP
                # tools) catch ValueError to turn it into a structured error
                raise ValueError(f"not a valid regular expression: {exc}") from exc

    # ------------------------------------------------------------------ matching
    def match(self, text: str) -> list[Match]:
        """Every occurrence of the target in `text`, in order, non-overlapping."""
        if not text:
            return []
        if self.mode == "regex":
            return self._match_regex(text)
        tokens = tokenise(text)
        return self._match_tokens(text, tokens)

    def find(self, text: str) -> Match | None:
        """The first occurrence, or None — the common "does this line use it?" call."""
        matches = self.match(text)
        return matches[0] if matches else None

    def matches(self, text: str) -> bool:
        return bool(self.match(text))

    # ------------------------------------------------------------------ internals
    def _match_regex(self, text: str) -> list[Match]:
        assert self._regex is not None
        tokens = tokenise(text)
        out: list[Match] = []
        for found in self._regex.finditer(text):
            if found.start() == found.end():
                continue
            first = next((i for i, t in enumerate(tokens) if t.end > found.start()), 0)
            last = sum(1 for t in tokens if t.start < found.end())
            out.append(
                Match(
                    start=found.start(),
                    end=found.end(),
                    surface=found.group(0),
                    token_start=first,
                    token_end=max(last, first + 1),
                    exact=self.case_sensitive and found.group(0) == self.target,
                )
            )
        return out

    def _match_tokens(self, text: str, tokens: Sequence[Token]) -> list[Match]:
        width = len(self._expansions)
        if width == 0 or len(tokens) < width:
            return []

        if self.mode == "exact":
            if self.case_sensitive:
                wanted = [token.text for token in self.phrase]
                haystack = [token.text for token in tokens]
            else:
                wanted = [token.norm for token in self.phrase]
                haystack = [token.norm for token in tokens]
        else:
            wanted = []
            haystack = [token.norm for token in tokens]

        out: list[Match] = []
        index = 0
        while index <= len(tokens) - width:
            window = haystack[index : index + width]
            if self.mode == "exact":
                hit = window == wanted
            else:
                hit = all(
                    window[offset] in self._expansions[offset] for offset in range(width)
                )
            if not hit:
                index += 1
                continue
            first, last = tokens[index], tokens[index + width - 1]
            surface = text[first.start : last.end]
            out.append(
                Match(
                    start=first.start,
                    end=last.end,
                    surface=surface,
                    token_start=index,
                    token_end=index + width,
                    exact=normalise(surface) == self._target_norm,
                )
            )
            index += width  # non-overlapping: "very very good" is one hit, not two
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"WordMatcher({self.target!r}, mode={self.mode!r})"


# --------------------------------------------------------------------- selection


@dataclass(frozen=True, slots=True)
class ScoredMatch:
    """A matched cue with the reason it is (or is not) a good teaching example."""

    text: str
    match: Match
    score: float
    duration_ms: int
    words: int
    reasons: tuple[str, ...] = ()


def score_example(
    text: str,
    match: Match,
    *,
    start_ms: int,
    end_ms: int,
    min_clip_ms: int = 2_000,
    max_clip_ms: int = 12_000,
) -> ScoredMatch:
    """Rank one hit as a teaching example.

    A good clip is short enough to loop, long enough to carry the line, and the
    target sits inside it rather than being the last word cut off by the tail. A
    line that is nothing *but* the target teaches nothing, so a sentence with some
    context around the hit scores higher than a two-word interjection.
    """
    duration = max(0, end_ms - start_ms)
    tokens = tokenise(text)
    words = len(tokens)
    score = 100.0
    reasons: list[str] = []

    if duration < min_clip_ms:
        shortfall = (min_clip_ms - duration) / max(1, min_clip_ms)
        score -= 45 * shortfall
        reasons.append("short")
    elif duration > max_clip_ms:
        excess = (duration - max_clip_ms) / max(1, max_clip_ms)
        score -= min(45.0, 60 * excess)
        reasons.append("long")
    else:
        score += 12.0

    # a usable example has words on both sides of the hit
    before = len(tokens[: match.token_start])
    after = len(tokens[match.token_end :])
    context = before + after
    if context == 0:
        score -= 50
        reasons.append("no-context")
    elif context <= 1:
        score -= 18
        reasons.append("thin-context")
    else:
        score += min(18.0, 3.0 * context)

    # ends on the target: the tail of the sentence is missing
    if after == 0 and before > 0:
        score -= 10
        reasons.append("target-at-end")

    # sanity: a very long line is hard to read in a vertical caption
    if words > 18:
        score -= min(25.0, 1.5 * (words - 18))
        reasons.append("wordy")

    # the learner should hear the word, not a fragment of a longer word
    if not match.exact:
        score -= 4
        reasons.append("inflected")

    return ScoredMatch(
        text=text,
        match=match,
        score=round(max(0.0, score), 3),
        duration_ms=duration,
        words=words,
        reasons=tuple(reasons),
    )


def is_content_word(word: str) -> bool:
    """False for the stopwords a reel must never be built around."""
    key = normalise(word)
    return bool(key) and key not in STOPWORDS


def words_in(text: str) -> Iterator[str]:
    yield from (token.norm for token in tokenise(text))


def content_words(text: str) -> list[str]:
    """The teachable words of a line, in order of first appearance."""
    seen: dict[str, None] = {}
    for word in words_in(text):
        if is_content_word(word) and word not in seen:
            seen[word] = None
    return list(seen)


def word_spans(text: str, start_ms: int, end_ms: int) -> list[WordSpan]:
    """Word-level timings for a line, from the line's own timing.

    An SRT times the *line*, never the words, so every word-level caption is an
    estimate. `core.timing.proportional_word_spans` is the engine's existing answer
    (quranic and doodle use it for exactly this), and it is deterministic — which
    matters because these spans end up inside a rendered artifact, and a caption that
    jittered between two identical runs would make the stage cache a lie.

    The token surface forms are passed through unchanged, so the spans line up with
    the same offsets `Match` uses and the highlight cannot drift off its word.
    """
    return proportional_word_spans([token.text for token in tokenise(text)], start_ms, end_ms)


def highlight_span(text: str, match: Match) -> tuple[str, str, str]:
    """Split a line into `(before, hit, after)` around one match."""
    return text[: match.start], text[match.start : match.end], text[match.end :]


def best_match(text: str, matcher: WordMatcher) -> Match | None:
    """The match a caption should highlight when a line contains several.

    Prefers an exact hit over an inflected one, then the hit with the most
    surrounding words — a title-cased echo is usually the weaker example.
    """
    found = matcher.match(text)
    if not found:
        return None
    tokens = len(tokenise(text))
    return max(
        found,
        key=lambda hit: (
            hit.exact,
            min(hit.token_start, tokens - hit.token_end),
            hit.token_count,
        ),
    )


def dedupe(cues: Iterable[tuple[str, Match, int, int]]) -> list[tuple[str, Match, int, int]]:
    """Drop repeated lines — a show that says the same thing twice is one example."""
    seen: set[str] = set()
    out: list[tuple[str, Match, int, int]] = []
    for text, match, start_ms, end_ms in cues:
        key = normalise(text)
        if key in seen:
            continue
        seen.add(key)
        out.append((text, match, start_ms, end_ms))
    return out
