# techreel — funny, fast motion-graphics reels about AI and tech, in any language

Status: drafted 2026-10-19 from the brainstorming conversation; the user said "go ahead" and
left the remaining calls to me (marked *my call*).
Branch: `feat/techreel`, from `main` at `30e584e` (Supertonic narration merged).

## 1. Intent

**Outcome.** A new recipe, `techreel`, turns a researched topic — a new AI tool, an AI/tech
news item, or an AI/LLM concept — into a ~60–110 s vertical (1080×1920, 30 fps) reel in the
style of the two references: a funny, conversational voice-over over dense 2D motion
graphics (animated UI mock-ups, counters, stamps, emoji gags, kinetic headings, word-by-word
captions). It is exposed through the MCP server so any agent (Claude Code or another) can go
from "make a reel about <topic or link>" to a finished MP4 plus a post caption.

**User's words.** "technical or AI news type video or new AI tools related or concept of AI
or LLM explained with … great visualization or motion graphics and the script is also
engaging with humor … to keep engaged user to the whole video." Languages: Hindi, English,
Japanese "or others". TTS: the new Supertonic provider. Facts: "AI Agent should find those
fact or information from the topic user will give or the topic link".

**Who drives it.** The MCP-calling agent does everything creative and factual (same model as
`mapreel` and `dialoguereel`): it researches the topic/link with its own tools, writes the
script in the reference formula, chooses a visual for every beat from the component
catalogue, probes, looks at the storyboard, and starts the full build. reel-machine has no
LLM and does no web research: it guides (via `describe_recipe`), checks (`probe` lint),
narrates (Supertonic), renders and verifies.

**Success.**
- An agent goes from a topic or URL to `reel.mp4` + `post.md` without writing rendering code.
- A viewer can't tell it apart, in pacing and polish, from the references: a new visual idea
  every 2–3 s, a joke every few lines, nothing static for more than ~3 s.
- Hindi (spoken Devanagari, Hinglish-in-Latin captions), English and Japanese reels all work
  from the same script model; any Supertonic language works.
- The same inputs render the same frames; after TTS no network is needed.

## 2. What the references do (the analysis the design follows)

Files: `~/Downloads/🤖 What if you had AI coworkers…mp4` (OpenDots, 91.5 s) and
`~/Downloads/BHAIII 😭🔥 176 BILLION PARAMETERS…mp4` (Strata, 105 s). Both 720×1280, 30 fps,
Hinglish voice-over.

**Look.**
- Dark background (near-black) with one accent per video (teal / orange-red) and soft
  out-of-focus glow blobs that drift. One palette, held for the whole reel; occasionally a
  full-bleed accent flash as a transition (Strata's "MIT LICENSE · free" beat).
- **One signature hero object** recurs and morphs: a chrome liquid orb (OpenDots: it splits
  into "dots", gets locked behind glass, sprouts role labels, rings when "on call"); a
  line-art laptop/monitor and a giant number (Strata).
- **A new beat every ~2–3 s**: a bold display heading at the top ("PROBLEM?", "TWIST 🤓",
  "REALITY CHECK", "HONEST MOMENT 🤏", "FEATURE 02/04 · …") with a small monospace kicker,
  and one animated mock-up in the middle: app window with tabs and a LIVE badge and a moving
  cursor; terminal printing `$ POST localhost:8080/v1/... → 200 OK`; GitHub repo card with a
  star count; approve/decline permission dialog; Slack-style thread; stacked subscription
  receipts; GPU outline; Windows/Linux/Mac chips; a checklist ticking; a comment box.
- **Sight gags carry the humour**: rotated rubber stamps ("PRO ONLY", "EMI", "DESI JUGAAD",
  "NO OFFICE POLITICS", "NO PRO SUBSCRIPTION ✓"), emoji reactions (💀 😭 🍿 🙏 😤), fake OS
  errors ("multitasking.exe has stopped working", "NOT RESPONDING"), rolling counters
  (176B, 19.9K ⭐ vs "school ki copy: 0 ⭐"), an analogy drawn literally (mixture-of-experts =
  an office of 125 people, 6 light up, the rest are "in a MEETING 💤"), "VS" score cards
  (ROOMMATE 0 vs DOT 1).
- **Captions**: one short phrase at a time in a dark pill at the bottom, word-by-word
  reveal, keywords in the accent colour. A thin progress bar along the top.
- **Ending**: a CTA beat — "Comment DOTS" typed into a fake comment box, a "link DM kar
  dunga" chip, a creator card whose Follow button flips to "Following ✓", a next-reel tease
  or a FOMO line.

**Script formula** (both scripts follow it, ~45 short lines):
1. **Hook** (line 1, ≤ 3 s): a shock fact/number or bold claim. "OpenAI ne 29 September ko
   Dots launch kiya… aur 2 din mein internet ne open-source version bana diya."
2. **Relatable pain**: the paywall, the monthly bills, the laptop that dies on 3 Chrome tabs.
3. **Reveal**: "Toh milo …" — name, the trust numbers (stars, licence).
4. **What it is / the concept**, each idea with one everyday analogy that becomes the visual
   ("an intern who never takes a chai break").
5. **Features**, numbered, each with a gag.
6. **Honest moment / reality check**: the catches (setup, alpha, GPU, your API bill).
7. **CTA**: comment keyword → DM link → follow → next-reel tease / FOMO.

Humour devices: self-roast ("more stars than my school notebook ever got"), comparison to
everyday life (roommate, mummy, EMI, office meetings), a callback to an earlier joke,
deadpan understatement, a mock-dramatic error. One joke every 2–3 lines.

## 3. Decisions taken

| question | decision | by |
|---|---|---|
| Who researches and writes | The calling agent; reel-machine has no LLM and no web fetch | user / me |
| Recipe | New recipe `techreel`, its own package, HyperFrames HTML runtime like `dialoguereel` | me |
| Visual vocabulary | A fixed catalogue of 2D components (§5) + a recurring hero + agent-written SVG and caller images for anything else | me |
| Narration | One narrator, any `narration` provider; Supertonic is the recommended one and the guide's examples use it | user / me |
| Spoken vs shown text | Each line has `say` (sent to TTS) and optional `text` (shown); `text` defaults to `say` | me (from the TTS test, §6) |
| Languages | Any language the bound TTS speaks; fonts for Latin, Devanagari, Japanese in v1 | user / me |
| Looks | `mint`, `ember`, `violet`, `mono`, plus an `accent` override | me |
| Length | Target 45–110 s; lint warns outside 30–150 s | me |
| Posting | Out of scope; `post.md` to paste | me |

## 4. The script model

```jsonc
{
  "title": "176B AI on a laptop",          // internal name + post.md headline
  "language": "hi",                        // BCP-47 of the voice (Supertonic code)
  "voice": "supertonic:M1",                // narration voice id; must match the bound provider
  "look": "ember",                         // mint | ember | violet | mono
  "accent": null,                          // optional "#ff5a2a" override of the look's accent
  "hero": {"kind": "laptop"},              // the recurring object (§5.1), optional
  "mode": "full",                          // storyboard | full
  "lint": "strict",                        // strict | warn
  "music": null,                           // optional bed, same option as mapreel/dialoguereel
  "creator": {"name": "Your Name", "handle": "@yourhandle", "avatar": null},  // for the CTA card
  "sources": ["https://github.com/…", "https://…"],   // where the facts came from (post.md, never on screen)
  "beats": [
    {
      "id": "hook",                        // optional, for findings and storyboard labels
      "kind": "hook",                      // hook | pain | reveal | explain | feature | honest | cta | other
      "heading": "bhai…",                  // optional big heading; *word* = accent colour
      "kicker": null,                      // optional small mono label above the heading
      "lines": [
        {"say": "भाई, मैंने अपने लैपटॉप में एक सौ छिहत्तर बिलियन पैरामीटर का AI घुसा दिया।",
         "text": "Bhai, maine apne laptop mein *176 billion* parameter ka AI ghusa diya."},
        {"say": "और लैपटॉप अभी तक ज़िंदा है।", "text": "Aur laptop abhi tak *zinda* hai."}
      ],
      "visuals": [                         // components, each entering on a cue (§5.3)
        {"kind": "hero", "state": "center"},
        {"kind": "counter", "to": 176, "suffix": "", "label": "BILLION PARAMETERS", "at": {"line": 0, "word": "176"}},
        {"kind": "heartbeat", "label": "LAPTOP STATUS: ZINDA", "at": {"line": 1}},
        {"kind": "emoji", "emoji": "💀", "at": {"line": 1, "word": "zinda"}}
      ],
      "transition": "cut"                  // cut | slide | flash | zoom (into this beat)
    }
  ]
}
```

- **`say`** is exactly what the voice reads: native script, numbers and symbols written as
  words ("एक सौ छिहत्तर", "one hundred seventy-six"), no markup. **`text`** is what the
  caption shows (any script; `*word*` marks accent words). If `text` is omitted the caption
  shows `say` (minus nothing — `say` has no markup).
- A **beat** is one visual scene: 1–4 lines (lint), its own heading/kicker and visuals. The
  reel is the beats in order. `kind` drives defaults (a `cta` beat with no visuals gets the
  standard comment-box + follow ending from `creator` and a `keyword`, below) and the lint's
  structure checks.
- **CTA beat** extra fields: `"keyword": "STRATA"`, `"dm": "link DM kar dunga"`,
  `"next": "My personal AI team 🤖"` (optional tease).
- **Cues** (`at`): `{"line": i}` (the line's start), `{"line": i, "word": "176"}` (when the
  first caption word matching it, case-insensitively and ignoring `*`/punctuation, is spoken),
  `{"line": i, "frac": 0.5}` (a fraction into the line's speech), or omitted (the beat's
  start). A word that is not found is a lint warning and falls back to the line's start.
- Every visual may have `exit` (`{"line": i}` or `"beat"` = stays to the beat's end, the
  default) and `place` (`top` | `middle` | `bottom-third` | `left` | `right`; default from the
  component).

## 5. Components

Each component is a small DOM/SVG builder in the runtime with deterministic enter/hold/exit
animation from time alone (spring overshoot on enter, ~250 ms; fade/scale on exit), styled
from the look's tokens. The guide documents every field with an example.

### 5.1 Hero (the recurring object)

`hero` on the reel picks the object; `{"kind": "hero", "state": …}` in a beat's visuals
changes what it does there. It persists across beats and moves smoothly between states
(no pop between beats).

- kinds: `orb` (chrome/liquid sphere — layered radial gradients + an SVG turbulence/
  displacement filter animated over time, specular highlight), `laptop`, `monitor`,
  `phone`, `chip` (GPU card outline with spinning fans), `robot` (simple friendly bot
  head), `svg` (agent-written, §5.4), `image` (caller PNG, §5.4).
- states: `center`, `small-top`, `corner`, `hidden`, `pulse`, `shake`, `rings` (concentric
  call rings), `locked` (frosted glass box + padlock), `split` (orb only: into N dots with
  optional labels), `screen` (laptop/monitor/phone: show a text/number on its screen),
  `sleep` (dims, "z z z").

### 5.2 Catalogue (v1)

| kind | what it shows | key fields |
|---|---|---|
| `title` | extra kinetic text (beyond the heading), word-by-word slam | `text` (`*accent*`), `size` |
| `counter` | number rolls from→to, big, accent | `from`, `to`, `decimals`, `prefix`, `suffix`, `label` |
| `equation` | stacked sum with a rule and a result that ticks | `rows` [`text`], `result` |
| `window` | app/browser mock: title, tabs, skeleton rows, optional LIVE badge, moving cursor, a button | `title`, `tabs`, `active`, `live`, `cursor` [`{to:[x,y], at}`], `button` |
| `terminal` | commands typed char by char, responses appear | `lines` [`{cmd}` or `{out, tone}`] |
| `repo` | GitHub-style card: owner/name, description, stars counter, licence, language | `name`, `description`, `stars`, `license`, `language` |
| `chat` | Slack/WhatsApp-style messages appearing one by one | `channel`, `messages` [`{from, text, avatar?, bot?}`] |
| `dialog` | permission dialog with approve/decline; a click animates | `title`, `body`, `approve`, `decline`, `click` |
| `error` | fake OS error box ("multitasking.exe has stopped working"), shakes in | `title`, `body`, `button` |
| `receipts` | paper tickets stacking with a slight rotation | `items` [`{title, sub}`] |
| `stamp` | rotated rubber stamp slamming on, with a thud | `text`, `tone` (`accent`/`red`/`green`), `rotate` |
| `emoji` | emoji pop reaction with a wobble | `emoji`, `place` |
| `chips` | row of pills appearing one by one | `items`, `style` (`outline`/`filled`) |
| `checklist` | items tick in sequence | `items`, `state` (`done`/`progress`) |
| `people` | grid of N person icons, K light up (analogy grids) | `total`, `lit`, `label`, `badge` (e.g. "MEETING 💤") |
| `versus` | two score cards with a VS | `left` `{label, value, emoji}`, `right` |
| `nodes` | small labelled bubbles/dots linked by lines (teams, pipelines, architectures) | `items` [`{label, icon?}`], `layout` (`row`/`triangle`/`ring`/`flow`) |
| `bars` | a few horizontal bars growing (benchmarks, prices) | `items` [`{label, value}`], `unit`, `highlight` |
| `flash` | full-bleed accent wipe with big text (transition beat) | `text` |
| `heartbeat` | ECG line drawing across with a label | `label` |
| `comment` | comment box typing the keyword, a send arrow, a reply chip | `keyword`, `reply` |
| `follow` | creator card: avatar, name, handle, Follow → Following ✓ | (from `creator`) |
| `image` | caller screenshot/logo, rounded frame, Ken-Burns drift, optional highlight box | `src`, `highlight` `[x,y,w,h]` (fractions) |
| `svg` | agent-written SVG drawing (§5.4) | `svg`, `size` |

Every beat may have **at most 4 visuals live at once** (lint warning), so the frame never
clutters.

**Layout** (*my call*, answering the builder check). The frame has fixed bands: progress
bar (top 6 px), heading band (y 120–420), **stage** (y 440–1380), caption band (y 1420–1640),
safe margins 64 px left/right and the platform UI zone below 1660 kept empty. Visuals are of
two classes:
- **block** visuals (`counter`, `equation`, `window`, `terminal`, `repo`, `chat`, `dialog`,
  `receipts`, `chips`, `checklist`, `people`, `versus`, `nodes`, `bars`, `heartbeat`,
  `comment`, `follow`, `image`, `svg`, `title`, and the hero when not `hidden`/`corner`)
  stack top-to-bottom in the stage in cue order, centred, with 32 px gaps. Each component
  has a natural size; when the live stack is taller than the stage, the whole stack scales
  down uniformly (min 0.6, then the lint's "too many visuals" warning applies). When a block
  leaves, the rest glide to their new slots (300 ms). The hero, when other blocks are live,
  moves to `small-top` of the stack automatically unless its state pins it.
- **overlay** visuals (`stamp`, `emoji`, `error`, `flash`) do not take stack space: `stamp`
  lands rotated across the newest block, `emoji` at the newest block's top-right corner (or
  `place`), `error` centred over the stage, `flash` full-bleed.
- `place: left|right` puts two consecutive blocks side by side in one stack row (for
  "A vs B" moments not covered by `versus`). `place: top|bottom-third` is dropped from v1.
The layout is computed in `pure.mjs` from time alone (which blocks are live at t, their
natural sizes measured once at build), so every frame is deterministic.

### 5.3 Always-on layers

- Background: the look's base colour + 3 large blurred glow blobs drifting slowly
  (seeded), a subtle grain. `flash` replaces it for its beat.
- Heading/kicker: top area; the heading slams in word-by-word at the beat start; it stays
  while the beat lasts and blurs out on the transition.
- Captions: bottom-third pill, the current caption chunk (≤ 6 words for spaced scripts, ≤ 14
  characters for Japanese), words revealed on their spoken times, the current word bright,
  `*accent*` words in the accent colour.
- Progress bar: 6 px at the very top, accent colour.
- Beat transitions (`cut` default 0 ms; `slide` 250 ms; `flash` 200 ms; `zoom` 300 ms).

### 5.4 Agent-written SVG and caller images

- `svg` (component or hero): sanitised exactly like `dialoguereel` agent SVG (≤ 60 KB,
  `defusedxml`, no script/foreignObject/`on*`/external refs; `data:` PNG/JPEG only). Reuse
  `dialoguereel/svg.py`'s checker (move it to a shared place if needed).
- `image`: an `https` URL or, for local runs only (`REEL_LOCAL_FILES`), a path; fetched once
  with the same guarded download as `dialoguereel/images.py` (public address only, size cap,
  PNG/JPEG/WebP), normalised to PNG ≤ 1600 px, cached by URL hash per caller.

## 6. Language and voice

**Measured on the user's Supertonic 3 server (2026-10-19):** `hi` reads Devanagari
naturally; romanized Hinglish under `hi` comes out garbled (Whisper heard German); digits are
misread in every language ("176" → "1 in 76" in `en`, nonsense in `hi`). Hence `say`/`text`.

- **Hindi**: `say` in Devanagari (English tech words may stay Latin: "AI", "GitHub"); `text`
  in Hinglish Latin script like the references. Fonts: Latin display + Devanagari (bundled
  Noto Sans Devanagari, see Fonts).
- **English**: `text` usually omitted; `say` spells numbers out.
- **Japanese**: `say` in Japanese with numbers in kanji/kana readings as wanted; captions
  chunk by characters (no spaces). Fonts: Noto Sans JP (HyperFrames supplies it).
- **Other Supertonic languages**: work with Latin/Cyrillic/Greek fonts; Arabic is RTL —
  *my call*: v1 renders RTL captions with `dir="rtl"` but the guide lists only hi/en/ja as
  tested.
- **What the server returns (measured):** word spans for spaced languages (`en`, `hi`:
  one per space-separated token, estimated from length), one span per character for `ja`
  (estimated). Names with digits are misread as well ("GPT-4o" → "GPT-40", "H100" →
  "H-Wonder"), so the digit rule covers them: `say` spells them ("G P T four o", "H one
  hundred"; Hindi "जीपीटी फ़ोर ओ", "एच वन हंड्रेड") while `text` keeps "GPT-4o". The guide
  carries a small spelling table per language. If a provider returns no spans, timings are
  spread over the clip length by character count (the `fake` voice path).
- **Word timings → caption words.** The provider returns spans for `say`'s words; captions
  show `text`'s words. When the two have the same word count, map 1:1; otherwise map each
  `text` word to the time at the same fraction of characters through the line (`say` spans
  give a char→time function). Japanese uses characters, not words.
- **Voice**: one narrator for the reel (`voice`), one TTS call per line, cached by
  (provider, voice, language, say) via `explainer3d.stages.speak_line`. The narration
  provider stays the deployment's binding (`REEL_TTS=supertonic`); a voice whose prefix
  doesn't match the bound provider is a probe failure (as in dialoguereel).
- **Fonts** (*my call*, keeping the repo's policy that no fonts are bundled —
  `THIRD_PARTY_NOTICES.md`): the CSS names font stacks and the deployment supplies them,
  like dialoguereel. Display: `"Archivo Black", "Montserrat", "Helvetica Neue", Arial
  Black, sans-serif` at weight 800–900; mono: `"JetBrains Mono", "SF Mono", Menlo,
  monospace`; Devanagari: `"Noto Sans Devanagari", "Kohinoor Devanagari", "Devanagari
  Sangam MN", sans-serif` (macOS ships Kohinoor; Linux needs `fonts-noto`); Japanese:
  HyperFrames' Noto Sans JP, then Hiragino. `docs/TECHREEL.md` lists the packages to
  install; the CI render job installs `fonts-noto-core`.

## 7. Timing

- Line gap 120 ms inside a beat; beat gap = the transition length (min 150 ms). The voice
  track is continuous speech with those gaps (fast, like the references).
- Beat length = its lines' speech + gaps; the visuals' cues resolve to absolute ms from
  word timings.
- An intro of 0 ms (the hook starts speaking at frame 0 — the references do), an outro of
  1.2 s after the last word (the Follow flip lands, then hold).
- `storyboard` mode: stills at each beat's midpoint and at the last cue of each beat;
  needs no real voice (`fake` timings, as in dialoguereel).

## 8. Pipeline

Stages, cached by content hash like the other recipes:

1. **`script`** — validate, parse `*accent*`, resolve cues to (line, word index), sanitise
   SVG, run the lint.
2. **`assets`** — fetch/normalise `image` sources (skipped when none).
3. **`narration`** — one TTS call per line (`speak_line`), map timings onto caption words,
   compute the schedule (beats, lines, cue times), build the voice track (`pad_speech`,
   `silence`, `ffmpeg.concat_audio`).
4. **`compose`** — one HyperFrames HTML project: a JSON document (looks, beats, lines with
   word times, visuals with absolute enter/exit ms, hero state timeline) + the runtime
   (`runtime/main.mjs` builds DOM once; `runtime/pure.mjs` decides every frame from time).
   Writes `script.resolved.json` and `post.md`.
5. **`preview`** — storyboard sheet (halts in `storyboard` mode).
6. **`render`** — HyperFrames → silent MP4 (cached per caller by project key), SFX + voice
   (+ optional music, ducked) mixed with `explainer3d.audio.mix`, muxed; `captions.srt`;
   manifest.
7. **`verify`** — duration within tolerance, audio present, no silence > 1.0 s outside the
   outro, every beat has at least one visual or heading on screen during its speech.

**SFX** (`explainer3d.audio.synth_sfx`): `whoosh` on each beat transition, `pop` on each
visual's enter, `thud` on stamps, `ding` on counters finishing and Following ✓, a short
typing tick for terminal/comment (add a `tick` effect to the synth table). Ducked under the
voice. A reel-level `"sfx": false` turns them off.

**Artifacts:** `reel.mp4`, `captions.srt`, `reel.json` (manifest), `storyboard.jpg`,
`script.resolved.json`, `post.md` (hook line, 3–5 line summary, CTA, sources list, 8–12
hashtags in the reel's language + English).

## 9. Lint (in `probe`)

Errors (block a strict full build):
1. No beats; a beat with no lines; a line with empty `say`.
2. Any digit in `say`, standalone or inside a name (fix hint: write numbers and names
   with digits as words in the voice's language — "GPT-4o" → "G P T four o"; both are
   measured Supertonic failures, §6).
3. `*` markup or other caption markup inside `say`.
4. A cue pointing at a line index outside its beat.
5. Invalid SVG / unsafe SVG; an `image` path when local files are off.
6. A voice whose provider is not the bound narration provider.

Warnings (with fix hints):
7. The first line estimated > 3.5 s (hook too slow) or the first beat has no visual.
8. A beat with > 4 lines, or estimated > 9 s, or a stretch > 3.5 s with no new visual
   entering (the "something new every 2–3 s" rule).
9. No `cta` beat last; no `honest` beat (trust); no `hook` beat first.
10. A cue `word` not found in its line's caption words.
11. More than 4 visuals live at once.
12. Estimated total outside 30–150 s (target 45–110).
13. Caption `text` and `say` differ wildly in length (ratio > 2.5) — likely a translation
    mismatch.
14. Hindi `say` mostly Latin script (would be garbled — the measured failure).
15. No `sources` (the facts must be traceable).

## 10. The guide (in `describe_recipe("techreel").guide`)

The most important part; written for an agent:

- **Workflow**: research → outline in the formula → write lines → pick visuals → `probe` →
  `storyboard` → look → `full`.
- **Research rules**: read the given link first (README, release notes, paper abstract);
  confirm every number from a primary source on the day; never invent a number, quote or
  date; when a fact is uncertain, say less; put every source URL in `sources`; for news,
  include the date in the hook if it matters.
- **The formula** (§2) with timing targets per part and two complete worked scripts,
  written fresh in the references' style (not transcribed from them — *my call*): a Hindi
  new-tool reel about a fictional open-source app (Devanagari `say`, Hinglish `text`) and
  an English concept reel ("What is RAG?"), plus a short Japanese example. Example creators
  are obviously fictional (`"name": "Your Name", "handle": "@yourhandle"`).
- **Humour rules**: one joke per 2–3 lines; joke types with examples in hi/en/ja; jokes
  punch at the situation or the narrator, never at groups of people; keep it brand-safe; a
  callback near the end; analogies must be both funny and technically right.
- **Visual rules**: one idea per beat; pair every analogy with a literal visual; prefer a
  component over plain text; use the hero at least every 3 beats; a stamp or emoji at each
  punchline; numbers always as `counter`; ≤ 4 visuals live.
- **Language notes**: §6 (`say` vs `text`, numbers as words, Hinglish style, Japanese
  caption chunking, voice ids per Supertonic: `F1`–`F5`, `M1`–`M5`).
- **Component reference**: every kind with fields and a one-line example.
- **CTA patterns** per language.

## 11. MCP

No new tools: `describe_recipe`, `probe`, `start_job`, `get_job`, `get_artifact` cover it.
`docs/MCP.md` gains a "techreel workflow" paragraph; the README gains the recipe in its
table and a quick start.

## 12. Testing

No network or real render in the default suite.

- **Model**: schema, defaults, `*accent*` parsing, cue resolution (line/word/frac), CTA
  defaults.
- **Lint**: one failing and one passing example per rule; digits-in-`say`; Hindi Latin
  `say`.
- **Timing map**: `say` spans → `text` words (equal counts, unequal counts, Japanese
  characters); schedule with transitions; cue times.
- **Compose**: the document for a fixture reel (snapshot of key fields); `post.md`.
- **Runtime** (`node --test`, like the other runtimes): pure functions — enter/exit
  easing, counter values, caption chunking, hero state interpolation, typing progress.
- **SVG/image safety**: reuse dialoguereel's malicious samples.
- **Real render** (slow marker, existing CI job): a storyboard and a short MP4 through
  `verify` with the `fake` voice.
- **Manual acceptance** (not CI): the Strata-style Hindi reel and an English reel with the
  real Supertonic server, checked by watching.

## 13. Delivery

Three plans, each ending in working, committed code:

1. **Foundation** — package, models, `say`/`text` timing map, schedule, narration, compose
   with background, heading, captions, progress bar, transitions, hero (`orb`, `laptop`,
   `monitor` with the core states), and the first components (`title`, `counter`,
   `stamp`, `emoji`, `chips`, `window`, `terminal`, `repo`), render, storyboard, SFX,
   minimal lint (errors 1–6). Ends with a short Hindi reel rendered with Supertonic.
2. **Catalogue and retention** — the rest of the components (`equation`, `chat`, `dialog`,
   `error`, `receipts`, `checklist`, `people`, `versus`, `nodes`, `bars`, `flash`,
   `heartbeat`, `comment`, `follow`, `image`, `svg`), remaining hero kinds/states, the CTA
   defaults, the full lint, the full guide with worked scripts.
3. **Polish** — looks (`mint`, `ember`, `violet`, `mono`, accent override), fonts bundled,
   music, `post.md`, `captions.srt`, `verify`, docs (`docs/TECHREEL.md`, `docs/MCP.md`,
   README), example reels: Hindi, English, Japanese.

## 14. Out of scope (v1)

An in-engine LLM or web researcher; posting to platforms; real screen recordings or video
clips inside the reel; a face-cam/avatar presenter; per-line multiple voices; lip-sync;
automatic translation or transliteration (the agent writes `say` and `text`).

## 15. Builder-check answers (2026-10-19)

Layout → §5.2 *Layout*. Server timings → measured, §6. Fonts → deployment-supplied, §6.
Worked examples → original, fictional, §10. Digits inside names → still an error, measured,
§6/§9.

## 16. Left to the builder

Exact easing curves and durations within the stated ranges; the CSS of each component;
glow-blob motion; SFX levels; how the orb shader is approximated in CSS/SVG; storyboard
still times beyond §7; module layout inside the package (mirror `dialoguereel`).
