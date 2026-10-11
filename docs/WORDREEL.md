# English learning reels (`wordreel`)

A word or phrase becomes a short reel: several real lines of dialogue that use it, cut
from your own media, with the word pinned at the top, its meaning under it, and the
target highlighted in the caption exactly where it is spoken.

Four stages: **select → scan → prep → render**. `scan` reads subtitles and lists what it
found; `prep` is the first stage that touches video. A caller can therefore inspect the
material before paying for a render.

## Quick start

```bash
# what can this library teach?
uv run reel run wordreel -i '{"mode": "suggest"}'

# which lines use "give up"? (no render)
uv run reel run wordreel -i '{"word": "give up", "mode": "scan"}'

# build the reel
uv run reel run wordreel -i '{"word": "give up", "gloss": "to stop trying", "count": 6}'
```

Point it at media:

```env
REEL_WORDREEL_LIBRARY=/media/films:/media/series
```

## Where the examples come from

The corpus is **your** subtitles. That is the whole design: a film you own is a film
whose every line is already indexed, the subtitle track *is* the index, and ffmpeg can
cut the exact second the line is spoken. Nothing is fetched from a third party, so there
is no quota, no account, and no bot challenge in the path.

Two passes, cheapest first:

| pass | what it reads | cost |
|---|---|---|
| **sidecar** | `Film.en.srt` beside `Film.mkv` (also `.ass`, `.ssa`, `.vtt`) | a text read — free |
| **embedded** | the subtitle track inside the video, via ffmpeg | a probe plus an extraction per file |

Every sidecar is read before the embedded pass starts, and the embedded pass only runs
when the sidecars did not already answer the query. Turn it off entirely with
`REEL_WORDREEL_EMBEDDED=false` for a fast sidecar-only scan of a large library.

Cutting from the same file the subtitle came from is what makes the timing free: an
embedded track is timed against exactly the footage being cut, so a clip lands on its
line with no alignment step and nothing to drift. A sidecar can belong to a different
release, which is why every candidate records where its line was read from
(`sidecar:…` / `embedded:…` / `caller:…`).

## Finding the word

English subtitles are plain text, and the word a learner wants almost never appears in
the form they typed. Ask for `give up` and the line says *gave up*; ask for `run` and it
says *running*, or *ran*.

So matching works on **tokens with variants**. Each token of the target is expanded into
the surface forms it can take — regular inflection, consonant doubling, `y → ies`, and a
curated irregular table (`run/ran`, `give/gave/given`, `tell/told`, `light/lit`) — and a
match is a run of adjacent tokens where token *i* is a variant of target token *i*. One
word is the one-token case of the same rule, which is why `run` finds *running* and
`give up` finds *gave up* through one code path.

Two deliberate exclusions:

- **`-er`/`-est` are not generated.** They look like comparatives but they are also how
  English builds agent nouns, and an agent noun is a different word: expanding `run` into
  *runner* would put a clip on screen that teaches something the reel did not ask for. A
  learner searching `big` misses *bigger*, which costs one example. Irregular comparatives
  (`good` → *better*) are curated and still match.
- **A word never matches inside a longer word.** `light` does not fire on *lighthouse*.

`match_mode` changes the rule:

| mode | behaviour |
|---|---|
| `smart` (default) | expand to inflected forms |
| `exact` | the literal text, case-insensitively |
| `regex` | the whole pattern is yours, e.g. `\bmind\b.*\bblow\b` |

Set `allow_inflected=false` to keep `smart`'s token boundaries but refuse an inflected
hit — useful when the form *is* the lesson.

## Choosing a word

`mode: "suggest"` proposes words the corpus can actually teach, ranked by how many titles
they appear in and how often they occur. Suggestions come from evidence rather than a
word list, so each one is known to have examples behind it. Words that appear once (no
material) and words that appear everywhere (`get`, `go`) are excluded, as are stopwords.

`gloss` is the meaning shown on the card. The recipe never guesses one — an agent
supplies it, which keeps a wrong definition out of a rendered artifact.

## Inputs

| field | default | meaning |
|---|---|---|
| `word` | — | the word or phrase; empty only in `suggest` mode |
| `mode` | `build` | `suggest` \| `scan` \| `plan` \| `build` |
| `match_mode` | `smart` | `smart` \| `exact` \| `regex` |
| `allow_inflected` | `true` | accept `gave up` for `give up` |
| `library` | settings | directories to scan; empty falls back to `REEL_WORDREEL_LIBRARY` |
| `subtitles` | — | explicit subtitle files (a scan with no footage still lists examples) |
| `languages` | `["en"]` | preferred subtitle languages |
| `count` | `6` | how many example clips the reel holds |
| `per_source` | `1` | *preference* for how many lines come from one title |
| `gloss` | — | the meaning shown under the word for the first seconds |
| `aspect` | setting | `vertical` \| `square` \| `original` |
| `name`, `outdir`, `watermark` | settings | output naming |

`per_source` is a preference, not a cap: the shortlist aims for that many lines per title
and then fills any remaining slots, so a one-film library still produces a full reel
instead of a single clip.

## The reel

Three caption layers over full-frame video, which is what lets every aspect work without
reserving a card band:

| layer | where | when |
|---|---|---|
| the **word** | top centre | the whole reel |
| the **gloss** | under the word | the first 3.5 s |
| the **line** | bottom, karaoke | per clip, with the target coloured |

The highlight composes with karaoke rather than replacing it: the target keeps its own
`\k` run, so it still fills with the voice while staying a different colour from the rest
of the line. Set `include_card=false` for a bare reel with captions only.

Artifacts: `reel.mp4`, `captions.ass`, `captions.srt`, `reel.json`, `plan.json`,
`timeline.json`. `plan.json` is every matching line with its file and timestamps —
written by `scan`, before anything is cut.

## Over MCP

`list_recipes` / `describe_recipe` / `probe` / `start_job` work as for any recipe, and two
tools answer the questions an agent asks *before* it submits anything:

| tool | question |
|---|---|
| `suggest_words` | what can this corpus teach? |
| `find_examples` | which lines use this word, and where is the footage? |

Both are synchronous, read-only and start no job — searching subtitles is cheap, and an
agent has to decide what to teach before it can write a request.

A typical loop:

```
suggest_words()                         → ["light", "letter", "water", …]
find_examples("light")                  → 6 lines, 3 with footage
start_job("wordreel", {"word": "light", "gloss": "illumination", "count": 6})
get_job(jobId)                          → artifacts
```

## Adding another corpus

`wordreel` searches whatever provider is registered under the `corpus` role, group
`reelmachine.clips`. The interface is deliberately small:

```python
class MyClipSource:
    name = "mysource"

    def missing(self) -> list[str]: ...          # unmet requirements, for `reel doctor`
    def describe(self) -> dict[str, Any]: ...    # a doctor row
    def suggest(self, request, *, limit): ...    # teachable words, best first
    def find(self, request, *, limit): ...       # list[ClipCandidate], best first
```

```toml
[project.entry-points."reelmachine.clips"]
mysource = "mypack.source:MyClipSource"
```

`find()` returns data, never files: the recipe searches *before* it downloads or cuts
anything, so a source must not render, and must not touch ffmpeg.

**A note on sources that are not yours.** Some services offer the same
word → clip lookup over their own catalogue. Whatever a provider does, it is bound by
that site's terms — respect them, and do not build a provider that defeats an access
control or a bot challenge to reach the content. The reason this recipe is built on
subtitles you already have is not only that it is free: it is the version that has no
such problem, and it is also the one that can highlight the word in sync, because you
have the subtitle track and the footage that matches it.
