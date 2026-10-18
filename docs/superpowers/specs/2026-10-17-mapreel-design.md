# mapreel — narrated world-map reels with a hook in every beat

Status: approved in conversation 2026-10-17, pending review of this written spec
Branch: `feat/mapreel`

## 1. Intent

**Outcome.** A new recipe, `mapreel`, turns a structured script of map segments into a
vertical (1080×1920, 30 fps) reel: a satellite (or flat) world map, a camera that flies
from place to place, countries that fill with colour, and the things the narration names
— brands, dishes, inventions, stats — popping onto the country as they are spoken, with a
voiceover and word-synced captions.

**Who drives it.** The agent calling the MCP tools (e.g. Claude Code) is the script
writer (user decision, option A): it picks a viral topic, researches the facts with
sources, writes a hook-first script, resolves places and images with the new tools,
probes, reviews a storyboard and starts the build. There is no LLM inside the engine.

**Success.**
- An agent goes from "brands by country" to a finished MP4 without writing any map code.
- Every reel opens on a hook and keeps re-hooking; `probe` refuses a script that doesn't.
- Every claim can carry a source and every image a recorded licence; the reel ships with a
  `sources.md` ready for the video description.
- The same script renders the same frames; after the first data download, rendering needs
  no network except for TTS and image fetches.

**The reference** (`~/Downloads/Popular Brands from different Countries around the world
🌎🤯…mp4`): 720×1280, 30 fps, 65 s, 15 countries at ~4 s each. Each segment is one line,
"*{Country} has A, B, C and D.*" Per segment: the camera flies over a satellite world map
to the country; its outline fills with a bold colour with an inner glow and the country
name ghosted across it; each brand's logo pops onto the country (slightly blurred,
drifting) as it is spoken; a one-word caption follows the voice. A new visual every
~0.7 s is what holds the viewer.

**Deliberate differences from the reference.** The reference has no hook, no re-hook, no
payoff and no ending — it opens cold on "The United States has…". We make hooks and
re-hooks a checked requirement. We add rank, versus, highlight and region shots so the
rhythm varies. We add pronunciation hints (the reference's voice mispronounces "Loewe"
and "Yves Saint Laurent").

## 2. Decisions taken

| question | decision |
|---|---|
| Who writes the script | The MCP-calling agent (option A), guided by `describe_recipe`'s guide and gated by `probe`'s lint |
| Item imagery | Default: Wikimedia Commons images via `find_images`, licence recorded; no image → styled text badge. Override: caller-supplied local path or URL per item, marked "caller-supplied" |
| Formats in v1 | All five: `country`, `highlight`, `rank`, `versus`, `regions` |
| Renderer | HyperFrames (HTML → MP4), `d3-geo` projection on canvas — same toolchain as explainer3d |
| Map data | Natural Earth admin-0 and admin-1 (public domain); NASA Blue Marble texture (public domain) |
| Item timing | Automatic: an item pops when its `name` (or `say`) is spoken, from TTS word timings; `at` overrides |
| Fact safety | Optional `source` URL on items, stats and segments; `probe` warns for each unsourced claim |

## 3. The script model

A reel is `title`, `mode` (`storyboard` | `full`), `look`, `voice`, optional `length`,
optional `music`, `lint` (`strict` default | `warn`) and an ordered list of `segments`.
Every segment has a `kind`, `narration` and optional `camera`, `source`, `colour`.

Narration reuses explainer3d's markup (`reelmachine/recipes/explainer3d/markup.py`):
`*word*` highlights a caption word; `[shown|spoken]` shows one thing while the voice says
another. That module graduates to core only if a third recipe needs it; until then
mapreel imports it from explainer3d.

```json
{
  "title": "Brands you didn't know were from these countries",
  "mode": "storyboard",
  "look": "satellite",
  "voice": {"provider": "cartesia", "voice": "…"},
  "length": {"target_s": 60},
  "segments": [
    {"kind": "highlight",
     "narration": "Only *3 countries* make the brands you wear every day. Number 1 will surprise you.",
     "countries": ["USA", "ITA", "FRA"], "legend": "The fashion superpowers"},
    {"kind": "country", "country": "ITA",
     "narration": "Italy has Gucci, Prada, Versace and Armani.",
     "items": [{"name": "Gucci", "image": "commons:File:Gucci_logo.svg", "source": "https://…"},
               {"name": "Prada"},
               {"name": "Versace", "image": "./logos/versace.png"},
               {"name": "Armani", "say": "ar-MAH-nee"}]},
    {"kind": "rank", "rank": 1, "country": "TUR",
     "narration": "And number one… *Turkey*?!",
     "stat": {"value": "70%", "label": "of the world's hazelnuts", "source": "https://…"}},
    {"kind": "versus", "countries": ["JPN", "USA"],
     "narration": "Japan has one vending machine for every 23 people.",
     "stats": [{"value": "1 : 23"}, {"value": "1 : 50"}]},
    {"kind": "regions", "country": "USA",
     "narration": "Texas is famous for BBQ, California for avocados.",
     "regions": [{"region": "US-TX", "items": [{"name": "BBQ", "emoji": "🍖"}]},
                 {"region": "US-CA", "items": [{"name": "Avocado", "emoji": "🥑"}]}]}
  ]
}
```

### Segment kinds

| kind | required | shows |
|---|---|---|
| `country` | `country`, `items` (1–8) | fly-to, fill + glow + ghosted name, items pop on speech |
| `highlight` | `countries` (1–60) | world or regional view, all listed countries fill (staggered or together), optional `legend` |
| `rank` | `rank` (1–99), `country` | fly-to, fill, big rank badge, optional `stat` and `items` |
| `versus` | `countries` (2–3), `stats` (same length) | split frame, one country per panel, stat cards that count up |
| `regions` | `country`, `regions` (1–12, each with `region` and `items`) | fly into the country, admin-1 borders, each region fills and gets its items |

### Items, places, images, overrides

- **Item:** `name` (shown and matched), optional `say` (what the voice says and what is
  matched, for pronunciation), `image`, `emoji`, `source`, `at` (seconds from the
  segment start; overrides speech matching).
- **Places** are ISO 3166-1 alpha-3 (`ITA`), ISO 3166-2 (`US-TX`, `JP-13`) or plain
  names (`"Italy"`, `"Texas"`). Names resolve against Natural Earth names, alternate
  names and a small alias table (`Holland`→`NLD`, `UK`→`GBR`); an ambiguous name
  (`Korea`) is an error that lists the candidates.
- **Image:** `commons:File:<name>` (from `find_images`), a local path, or an `https` URL.
  Unset or unfetchable → a text badge (name in a heavy display face, with `emoji` if
  given).
- **Speech matching:** the item's spoken form is matched case- and punctuation-
  insensitively against the segment's word timings, in order, as a token sequence
  (`Dolce & Gabbana` matches "Dolce and Gabbana"); the item pops at its first word's
  start. Unmatched items pop evenly across the segment and raise a warning naming them.
- **Camera:** automatic fly-to framing the segment's places. `camera.zoom` (0.5–3)
  scales the framing; `camera.hold: true` skips the move and keeps the previous frame.
- **Colour:** automatic from a vivid palette, assigned so that no two places shown
  together, or consecutive segments, get similar colours (ΔE threshold); `colour`
  overrides.

## 4. Pipeline

Stages follow explainer3d's pattern; each is cached by content hash.

1. **`script`** — validate, resolve places (with suggestions on failure), assign colours.
2. **`geo`** — fetch Natural Earth admin-0 and admin-1 (10m for regions, 50m otherwise)
   and the Blue Marble texture once into the shared asset cache with licence and
   provenance; cut each segment's shapes, simplify to on-screen size, compute camera
   frames. Framing uses the main landmass for countries with distant territories (FRA,
   USA, NOR, NLD …) and handles the antimeridian (RUS, FJI, USA-Alaska).
3. **`images`** — fetch each `commons:` image with its licence and attribution; accept
   local paths and URLs as "caller-supplied"; normalise to PNG ≤ 1024 px. A failure
   becomes a text badge plus a warning, never an error.
4. **`narration`** — one TTS call per segment through the existing `narration` providers
   (`fake`, `cartesia`, `elevenlabs`) with word timings; compute segment windows and item
   pop times.
5. **`compose`** — one HyperFrames HTML project for the reel: canvas basemap
   (reprojected texture or flat fill per `look`), fills with inner glow, ghosted names,
   item pops (scale overshoot, slight blur and drift), rank badges, versus cards,
   legends, word-synced captions, a van Wijk–style zoom-and-pan fly-to between segments.
   SFX (whoosh per fly-to, pop per item) from explainer3d's synthesised soundtrack code,
   optional music bed.
6. **`render`** — HyperFrames → `reel.mp4`, or one still per segment tiled into
   `storyboard.jpg` in storyboard mode.
7. **`verify`** — duration matches the voice, audio present, no frozen stretch longer
   than the beat-gap limit, each item visible at its pop time (pixel check against the
   storyboard state).

**Artifacts:** `reel.mp4`, `captions.srt`, `storyboard.jpg`, `script.resolved.json`
(places, colours, timings as resolved), `sources.md` (every claim with its source, every
image with its licence and attribution, Natural Earth and NASA credits).

**Looks:** `satellite` (Blue Marble, the reference), `flat` (solid land and sea, bold
borders), `dark` (night palette, neon fills).

## 5. MCP tools

The generic tools (`describe_recipe`, `probe`, `start_job`, `get_job`, `get_artifact`)
run the recipe. Two tools are added in `reelmachine/mcp/tools.py`:

- **`find_places(query, limit=8)`** — resolves a name or code to candidates: code, name,
  level (country/region), parent, whether admin-1 data exists, ambiguity flag.
- **`find_images(query, kind="any", limit=12, include_nonfree=False)`** — searches
  Wikimedia Commons (`logo` / `photo` / `flag` / `any` bias the query and categories);
  returns `id` (`commons:File:…`), thumbnail URL, size, licence, attribution, and whether
  the licence permits use. Non-free files are excluded by default.

Both return the standard structured error payload on failure, and both are read-only.

## 6. The guide and the hook lint

**Guide** (in `describe_recipe`): the workflow (topic → research with sources → hook →
segments → `find_places` / `find_images` → `probe` → storyboard → full); a topic bank of
~30 map-reel angles; hook formulas with examples (curiosity gap, challenge, shock stat,
"you" framing, contradiction); re-hook phrases; ending patterns (pay off the open loop,
then a comment-bait CTA).

**Lint** (in `probe`; rules 1–2 are errors in `full` mode unless `lint: "warn"`; the rest
are warnings with a fix hint):

1. **Weak opener** — segment 1's first ~2 s of speech has no hook signal (a number, a
   question, "you/your", a superlative, "only", a tease word) or opens with a list
   statement ("X has…", "Welcome…", "Today…").
2. **Unpaid loop** — the hook teases ("number 1", "the last one", "surprise", "guess")
   and no later segment is `rank: 1` or a final reveal.
3. **Beat gap** — more than 1.2 s of narration without a new visual beat (item pop,
   fly-to, fill, stat, badge).
4. **No re-hook** — more than 12 s without a re-hook phrase or a `rank`, `versus` or
   `highlight` segment.
5. **Monotony** — 4 or more consecutive `country` segments; suggest a `versus`, `rank`
   or `highlight` between them.
6. **Length** — suggest a length from the material; warn when `target_s` would mean
   padding.
7. **Unsourced claims** — list each item, stat or segment without a source.
8. **No ending** — the last segment has no CTA or payoff.

The reference reel's own script must fail rules 1, 4 and 5 (a regression test).

## 7. Errors

Unknown or ambiguous place, bad schema → `InvalidInput` at `probe`/`script` with
suggestions. Missing image, missing source, weak hook in storyboard mode → warnings.
TTS out of credits or a bad key → the existing clear voice error. Missing Node or
HyperFrames → the renderer's `missing()` report, same as explainer3d.

## 8. Testing

No network or render in the default suite.

- **Geo:** a tiny Natural Earth fixture (≈5 countries, a few US states and Japanese
  prefectures): name resolution, aliases, ambiguity, framing for split and
  antimeridian countries, palette contrast for neighbours.
- **Timing:** fake narration with known word timings: items pop on their words; `say`
  and `at` overrides; multi-word and `&` names; unmatched items warn.
- **Lint:** one failing and one passing example per rule; the reference script as a
  regression.
- **Images:** a fake Commons provider: licence filtering, fallback to a badge, local and
  URL overrides, provenance.
- **MCP:** `find_places` / `find_images` payloads and error shapes.
- **Real render** (slow marker, existing CI job with Node and HyperFrames): a 3-segment
  storyboard and a short MP4 through `verify`.

## 9. Delivery

Three plans, each ending in working, committed code:

1. **Foundation** — script model, place resolution and `find_places`, geo data and
   framing, `country` and `highlight` kinds, narration timing, compose, render,
   storyboard, `script.resolved.json`. Ends with a reference-style reel.
2. **Retention** — `rank`, `versus`, `regions` kinds; the full lint; the guide and topic
   bank; SFX.
3. **Images and polish** — `find_images` and Commons licences, `sources.md`, `flat` and
   `dark` looks, `verify`, docs (`docs/MAPREEL.md`, `docs/MCP.md`), an example reel with
   Cartesia.

## 10. Out of scope (v1)

An in-engine LLM script writer; cities and point markers; routes/arrows between places;
animated choropleths over time; languages other than English for the guide and lint
heuristics (narration in other languages works, but lint's hook words are English).
