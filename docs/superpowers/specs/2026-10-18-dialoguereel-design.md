# dialoguereel — two cute characters, one short conversation, any language

Status: drafted 2026-10-18 from the brainstorming conversation, pending the user's review
Branch: `saifulbabo67646/reel-video-module-integration` (same commit as `feat/mapreel`)

## 1. Intent

**Outcome.** A new recipe, `dialoguereel`, turns a short scripted conversation between two
AI-generated cute characters into a vertical (1080×1920, 30 fps) language-learning reel.
Every line is spoken by its character's own voice and shown three ways: the target-language
script (with furigana where needed), its reading/romanization, and its meaning in the
viewer's language. It is exposed through the MCP server so an agent can make the reel and
the user can post it to their social accounts.

**Who it is for (user's words).** People learning Japanese at JLPT N5, N4 or N3. A short
everyday conversation at their level teaches new words, whole sentences, and improves
listening. The module must not be limited to Japanese: Korean, Spanish, English or any
language must work. *(My call: other languages use their own level ladders — TOPIK 1–6
for Korean, CEFR A1–C2 for others — and the level is a free label the caller sets.)*

**Who drives it.** The MCP-calling agent writes the dialogue (same model as mapreel and
doodle): it picks a situation at the requested level, writes natural lines, supplies
readings, meanings and key words, designs the two characters, probes, reviews a
storyboard, and starts the build. There is no LLM inside the engine. Characters are
AI-generated images (user decision); everything else was left to me.

**Success.**
- An agent goes from "an N5 conversation about chores, in Japanese" to a finished MP4 and
  a ready-to-paste post caption without writing any rendering code.
- A learner watching it can hear each line, read it, see how to say it, know what it
  means, and leaves with 2–6 key words in a recap.
- The same characters can be reused across many reels (a channel's recurring cast).
- The same inputs render the same frames; after the cast exists, rendering needs no
  network except TTS.

**The reference** (`~/Downloads/AQP2hQgys-…mp4`): 720×1280, 18.6 s, 7 lines, ~2.5 s
each. A saturated blue background; a kawaii cupcake and a kawaii mug (sticker style,
thick white outline, pastel, blush cheeks) stand side by side in the lower third and
bob/tilt slightly. Top third: English meaning (small, blue), Japanese line in hiragana
(white, bold), romaji (blue). The previous line fades out as the next comes in. ~16
voice bursts for 7 lines (some lines are spoken more than once).

**Why it holds viewers (my analysis, used as design drivers).** A tiny story rather than
a word list (people stay to see how it ends); three layers per line so everyone learns
something; friendly moving characters; fast pace (a line every ~2.5 s); save-worthy as a
mini phrasebook.

**Deliberate differences from the reference.** It has no hook, no level label, no word
highlighting, no indication of who is speaking, no recap and no ending. We add all of
these, plus listening-first and slow-replay options and an end-of-reel quiz.

## 2. Decisions taken

| question | decision | by |
|---|---|---|
| Audience | Learners at beginner–lower-intermediate (JLPT N5–N3 and equivalents) | user |
| Languages | Any; Japanese gets extra checks from `reelmachine/data/jlpt_vocab.json` | user / me |
| Characters | Code-drawn SVG first: a built-in library shipped in the codebase, plus characters the agent writes itself as SVG following a documented contract; AI-generated images kept as an option, not relied on | user (2026-10-18 revision) |
| Character kinds | `builtin`, `svg` (agent-written), `image` (AI-generated via `openai`, or caller PNGs) | me |
| Default | `builtin` — free, instant, offline, fully animatable | me |
| Who writes the dialogue | The MCP-calling agent, guided by `describe_recipe`'s guide and gated by `probe`'s lint | me (matches mapreel) |
| Cast size | Exactly two characters in v1 | me |
| Voices | Existing `narration` providers (`elevenlabs`, `cartesia`, `macos`, `fake`), one voice per character, one TTS call per line | me |
| Renderer | HyperFrames (HTML → MP4), same toolchain as mapreel/explainer3d | me |
| Furigana / readings | Supplied by the caller (ruby markup); no tokenizer dependency | me |
| Posting to social platforms | Out of scope; we produce `post.md` (caption, vocab list, hashtags) to paste | me |

## 3. The script model

A reel is:

```jsonc
{
  "title": "Helping Mom at home",          // hook text, in the meaning language
  "language": "ja",                        // BCP-47 of the dialogue
  "meaning_language": "en",                // BCP-47 of translations (default "en")
  "level": "N5",                           // free label; known ladders get checks
  "scene": "🧺",                           // optional emoji shown on the hook card
  "cast": "cast_3f9a…",                    // id from make_cast, or an inline cast object (below)
  "look": "pop",                           // pop | pastel | night
  "flow": "standard",                      // standard | listen_first
  "quiz": true,                            // end with a "your turn" question (default true)
  "mode": "full",                          // storyboard | full
  "lint": "strict",                        // strict | warn
  "music": null,                           // optional bed, same option as mapreel
  "lines": [
    {
      "speaker": "kid",                    // a cast character id
      "text": "手伝{てつだ}いましょうか。",    // target script; {..} = ruby on the preceding kanji run
      "reading": "Tetsudai mashou ka.",    // romanization; required for non-Latin scripts (lint)
      "meaning": "Shall I help?",
      "keys": [ {"text": "手伝う", "reading": "tetsudau", "meaning": "to help"} ],
      "emotion": "happy",                  // optional: neutral | happy | surprised | sad | thinking
      "slow": false                        // optional: replay this line at 0.75× speed
    }
  ]
}
```

**Ruby markup.** `漢字{かんじ}` attaches the reading in braces to the run of kanji just
before it. Text without braces renders plainly. Hiragana-only lines (as in the reference)
are fine. For other languages braces are not used.

**Key words.** `keys[].text` is the dictionary form. The line must contain it or a form
of it; the matcher looks for the key's text or its stem (its text minus a final kana for
Japanese verbs/adjectives; a case-insensitive prefix match for Latin scripts). An
unmatched key is a warning and is shown in the recap but not highlighted in the line.

**Inline cast** (alternative to a cast id): `{"characters": [ <character>, <character> ]}`
(see §4). It is resolved through the same path as `make_cast`.

## 4. Characters (the cast)

A character is `{id, name, voice, side, source}` where `source` is one of three kinds:

```jsonc
{"kind": "builtin", "name": "cupcake", "colors": {"primary": "#f7a8d8"}}   // shipped library
{"kind": "svg", "svg": "<svg viewBox=\"0 0 400 400\">…</svg>"}           // agent-written
{"kind": "image", "provider": "openai", "description": "a cheerful onigiri"} // AI or caller PNGs
```

**Why SVG first (user's direction, my reasoning).** Code-drawn characters cost nothing,
render instantly and offline, look identical every time, and — unlike a flat image — have
separate parts the renderer can move: real blinks, a mouth that opens and closes with the
voice, expressions, arm waves. The agent using the MCP server is strong at writing SVG and
motion graphics, so it can design a new character for a reel on the spot. AI images stay
as an option for a look SVG can't reach.

### 4.1 The SVG character contract

Every `builtin` and `svg` character follows one contract, documented in the guide with a
complete worked example:

- Root `<svg viewBox="0 0 400 400">`, character standing on the bottom edge, centred,
  facing the viewer, ~300–360 px tall inside the box.
- Required groups (by `id`): `body` (everything static), `eyes-open`, `eyes-closed`,
  `mouth-closed`, `mouth-open`.
- Optional groups: `mouth-smile`, `eyes-happy` (^ ^), `eyes-surprised`, `blush`,
  `arm-left`, `arm-right` (rotated around their `data-pivot="x,y"` for waves), `sweat`,
  `sparkle`. Missing optional groups fall back (e.g. `happy` without `eyes-happy` → open
  eyes + `mouth-smile` or `mouth-closed` + a bigger bounce).
- The renderer animates the whole character (bob, hop, squash-and-stretch, tilt) and
  toggles/moves the groups: blinks every 2–5 s at random (seeded), mouth flaps on voice
  timings, emotions swap eye/mouth groups and show `blush`/`sweat`/`sparkle`.
- House style (guide, not enforced): kawaii sticker look — thick white outer outline
  (≥10 px) with a dark inner line, flat pastel fills with one soft shade, big head,
  dot eyes, small mouth, pink blush ovals.
- Safety (enforced by `probe`, since agent SVG is rendered in a headless browser): ≤ 60 KB;
  parsed with `defusedxml`; no `<script>`, `<foreignObject>`, `on*` attributes, external
  `href`/`xlink:href` (only `#…` fragment refs), `<image>` only as `data:image/png|jpeg`;
  `<style>` allowed but no `@import`/`url(` to anything but `#…`. Violations are errors
  naming the element.
- `probe` also checks the required ids, and renders nothing — the storyboard is how the
  agent sees its character.

### 4.2 The built-in library

Shipped under `reelmachine/recipes/dialoguereel/characters/*.svg`, hand-written to the
contract, ~10 characters to start: `cupcake`, `mug` (the reference's pair), `onigiri`,
`dango`, `boba`, `cat`, `shiba`, `penguin`, `bunny`, `kimchi-jar`. Each declares
recolourable fills via CSS variables (`--primary`, `--secondary`, `--accent`) so
`colors` can tint it, and has all optional groups. `describe_recipe` lists them with a
one-line description and a default suggested role.

### 4.3 Image characters (optional)

- `openai` — `gpt-image-1`, `background: transparent`, 1024×1024; key from a new setting
  `REEL_OPENAI_API_KEY`; model name configurable. Shared style prompt from the look
  ("kawaii sticker, thick white outline, flat pastel colours, simple face, blush cheeks,
  full body, centred") plus the description. Poses `neutral` first, then `talk`,
  `happy`, `surprised` by editing the neutral image so the character stays the same.
- caller PNGs — `{"kind": "image", "poses": {"neutral": url, "talk": url, …}}`, `neutral`
  required. Local filesystem paths only for local (stdio/CLI) runs; over HTTP only URLs,
  with mapreel's URL safety checks.
- Image characters animate by whole-body motion and pose swaps (talk ↔ neutral for mouth
  flaps); no blinks. A failed pose falls back to `neutral` with a warning.
- Images are normalised to PNG, trimmed to their alpha bounding box, max 1024 px.

### 4.4 Casts, caching, credits

`make_cast` turns two characters into a `cast_id` (a content hash of the sources; voices
are on the cast record but not in the hash). Casts are stored **per caller** under
`store.root()/<caller>/casts/<cast_id>/` (my call: private to their maker). Any later reel
by the same caller can reuse the `cast_id`, so a channel keeps its pair. Builtin and SVG
casts are resolved instantly and never spend credits. Image generation counts against
the caller's per-provider monthly quota in `CallerPolicy` (`openai`); `make_cast` / the
`cast` stage check it before spending; `variant` (int) regenerates a different take.

## 5. What the reel looks like (timeline)

1. **Hook (≤1.5 s).** Background in the look colour; level badge ("N5 · 🇯🇵") top-left;
   `scene` emoji and `title` big in the middle (e.g. "Helping Mom at home 🧺"), a small
   "Can you understand this?" line; characters drop in from below with a squash-and-bounce
   and a pop SFX. No speech.
2. **Listen first (only when `flow: listen_first`).** The whole dialogue plays with the
   text area blurred and a banner "👂 Listen first — how much did you catch?"; then a
   0.6 s "Now with subtitles" card, then the dialogue section.
3. **Dialogue, per line.**
   - The speaker hops (squash-and-stretch) and turns slightly toward the other; the
     listener gets a small tilt/nod; the speaker's mouth flaps between `neutral` and
     `talk` on the TTS word/character timings; `emotion` swaps to that pose for the line
     and pops a small matching emoji (❤️ 💦 ❓ ✨).
   - A speech bubble with a tail pointing at the speaker holds the target text (big,
     bold, ruby above kanji) and the reading below it. Text appears as the voice
     starts; each word/character run lights up as it is spoken (karaoke).
   - Key words are underlined in an accent colour; when one is spoken a small chip
     ("手伝う · to help") pops near it.
   - The meaning fades in at about 60 % of the line's speech, so the ear and the script
     come first (listening value) but nobody is lost.
   - The previous line shrinks and fades into a two-line history above the bubble, like
     a chat, so the viewer can follow the conversation.
   - `slow: true` replays the line at 0.75× with a "🐢 slow" chip.
   - Gap between lines 0.35 s.
4. **Recap (≈3 s).** "Words from this video" — up to 6 key-word cards (script with
   ruby, reading, meaning, level tag for Japanese).
5. **Quiz (when `quiz` is true, ≈4 s).** "How do you say: '<meaning>'?" for the line
   with the most key words (or `quiz: <line index>`), a 3-2-1 ring, then the answer with
   its audio, and "Comment your answer 👇 / Follow for daily N5".
6. **Loop.** The last frame matches the hook's background and character positions so
   the reel loops cleanly.

A thin progress bar runs along the top for the whole reel.

**Looks.** `pop` (saturated electric blue like the reference, white text, cyan
accents), `pastel` (cream background, soft colours), `night` (deep navy, neon accents).
Fonts: Noto Sans JP / KR / SC and a Latin display font, bundled or fetched once into the
asset cache, chosen by `language`.

**Pacing targets.** 15–45 s total (the dialogue section; `listen_first` is excluded from
the length warning); 4–10 lines (the lint's hard limits are 3–12). Line length: ≤ 22 characters for CJK
scripts, ≤ 42 characters for others.

## 6. Pipeline

Stages follow mapreel's pattern; each is cached by content hash.

1. **`script`** — validate, parse ruby, match keys, resolve the cast id or inline cast,
   run the lint.
2. **`cast`** — make or load the cast through the `characters` provider (skipped when
   the cast id is already cached).
3. **`narration`** — one TTS call per line with that character's voice and `language`;
   slow replays use ffmpeg `atempo` (the narration interface has no speed option);
   word or character timings drive karaoke and mouth flaps; compute the timeline. For
   scripts without spaces (Japanese, Chinese), the shared narration code gains an
   additive option to keep character-level spans instead of collapsing them into
   space-separated words; karaoke then highlights per ruby/kana run.
4. **`compose`** — one HyperFrames HTML project: background, characters with pose swaps
   and code animation, bubbles, ruby text, karaoke, key chips, history, recap, quiz,
   progress bar; SFX (pop, whoosh, tick, ding) reusing explainer3d's synthesised effects;
   optional music bed ducked under voice.
5. **`render`** — HyperFrames → `reel.mp4`, or one still per section/line tiled into
   `storyboard.jpg` in storyboard mode (storyboard mode uses the `fake` voice timings if
   no voice is configured).
6. **`verify`** — duration matches the summed voice plus fixed sections, audio present,
   no silent stretch longer than 1.5 s outside the quiz countdown, each line's text
   visible during its speech.

**Artifacts:** `reel.mp4`, `captions.srt` (target text + meaning), `storyboard.jpg`,
`script.resolved.json` (timings, poses, key matches), `post.md` (a caption in the meaning
language: hook line, the dialogue with readings and meanings, the vocab list, 5–10
hashtags such as `#learnjapanese #JLPTN5 #日本語`), `cast/` previews.

## 7. MCP tools

The generic tools (`describe_recipe`, `probe`, `start_job`, `get_job`, `get_artifact`)
run the recipe. One tool is added in `reelmachine/mcp/tools.py` / `server.py`:

- **`make_cast(characters, look="pop", variant=0)`** — returns immediately with the
  deterministic `cast_id` (computed from the hash). For builtin/SVG-only casts it
  validates them (contract + safety) and returns `ready: true` plus a `job_id` for a
  short preview job that renders each character's states (neutral, talking, happy,
  blink) to PNG artifacts. For image casts it returns `cached: true` with the pose links,
  or a `job_id` for the generation job. Generation (8 image calls,
  possibly minutes) runs as a normal job: `dialoguereel` with `mode: "cast"` runs only the
  `script`-validation of the cast and the `cast` stage, and publishes each pose PNG as a
  job artifact, so `get_job` / `get_artifact` and the existing signed artifact URLs work
  unchanged (my call). A cached cast's previews are served by re-publishing them as
  artifacts of a trivial completed job. `probe` never spends image credits; a
  `start_job` with an uncached inline cast does (it runs the `cast` stage).

**Guide** (in `describe_recipe`): the workflow (pick level and situation → `make_cast`
→ write lines → `probe` → storyboard → full); a situation bank for N5–N3 (~30: konbini,
train, restaurant, doctor, school, chores, shopping, directions, weekend plans, part-time
job, …); rules for natural, level-appropriate lines (short, polite vs casual chosen
deliberately, one new grammar point per reel); 2–6 key words; hook title formulas for
learners ("Can you understand this N5 conversation?", "What Japanese moms say every
morning", "3 phrases for the konbini"); CTA patterns; how to write `post.md`-friendly
titles. The guide is written for Japanese first with notes for Korean and Spanish.

## 8. Lint (in `probe`)

Errors in `full` mode unless `lint: "warn"`; warnings carry a fix hint.

Errors:
1. A `speaker` not in the cast; the cast not exactly two characters.
2. Fewer than 3 or more than 12 lines; a line with empty `text` or `meaning`.
3. Empty `title` (no hook).

Warnings:
4. Missing `reading` on a line whose script is not Latin.
5. Line too long for its script (see pacing targets).
6. Estimated total length outside 15–45 s (estimate from characters per second per
   language).
7. A key word not found in its line.
8. **Level check (Japanese, level N5–N1):** a key word whose JLPT level (from
   `jlpt_vocab.json`, matched on the key's text or ruby-stripped reading) is harder than
   the reel's level; a key word absent from the list is "unrated", not an error.
9. Fewer than 2 or more than 6 key words in the whole reel.
10. Ruby braces that do not follow a kanji run, or unbalanced braces.
11. The same speaker on more than 3 consecutive lines (it should feel like a
    conversation).

The reference's dialogue is a regression fixture (speakers alternate, kid first):
おかあさん。/Okaasan./Mom · てつだいましょうか。/Tetsudai mashou ka./Shall I help? ·
なにをしますか？/Nani o shimasu ka?/What should I do? · せんたくをしてくれる？/Sentaku o
shite kureru?/Can you do the laundry? · それとさらをあらって。/Sore to sara o
aratte./And wash the dishes. · ごみもだしてください。/Gomi mo dashite kudasai./Please take
out the trash too. · いまからします。/Ima kara shimasu./I'll do it now. With no title and no
keys it must fail rule 3 and warn on rule 9.

## 9. Errors

Bad schema, unknown speaker, unknown cast id → `InvalidInput` at `probe`/`script`.
Image generation failure or missing key → a clear provider error naming the setting
(`REEL_OPENAI_API_KEY`), raised by `make_cast` or the `cast` stage; a single pose
failing is a warning. TTS failures → the existing voice errors. Missing Node or
HyperFrames → the renderer's `missing()` report.

## 10. Testing

No network or render in the default suite.

- **Model and ruby:** parsing, kanji-run attachment, malformed braces, inline vs id
  casts.
- **Keys and level:** key matching for Japanese verb stems and Latin prefixes; JLPT
  lookups with a small fixture; unrated words.
- **Lint:** one failing and one passing example per rule; the reference fixture.
- **SVG contract:** each built-in passes the contract and safety checks; a malicious
  sample (script, `onload`, external href, `foreignObject`, oversize) is refused with the
  element named; missing optional groups fall back correctly.
- **Cast:** `fake` image provider and caller PNGs; cache hit by hash; `variant` changes the hash;
  `openai` request shape against a stubbed HTTP client (no network); pose-failure
  fallback.
- **Timeline:** fake narration with known timings → line windows, meaning-reveal time,
  karaoke spans, mouth-flap frames, slow replays, quiz and recap durations.
- **MCP:** `make_cast` payloads and error shapes; recipe listed and described.
- **post.md:** content for a fixture reel.
- **Real render** (slow marker, existing CI job with Node and HyperFrames): a storyboard
  and a short MP4 through `verify` with the `fake` cast and voice.

## 11. Delivery

Three plans, each ending in working, committed code:

1. **Foundation** — script model, ruby, the SVG contract and validator, the first
   built-ins (`cupcake`, `mug`), casts and `make_cast` for builtin/SVG, narration and timeline, compose (hook, dialogue with speaker animation,
   bubbles, karaoke, history), render, storyboard, `script.resolved.json`. Ends with a
   reference-style reel.
2. **Learning and retention** — the rest of the built-in library, the character-writing
   section of the guide (contract, worked example, style rules), key words and chips, recap, quiz, listen-first, slow
   replays, emotion poses, the full lint with the JLPT check, the guide and situation
   bank, SFX.
3. **Polish** — image characters (`openai` and caller PNGs) with quota, `pastel` and `night` looks, `post.md`, `captions.srt`, `verify`, docs
   (`docs/DIALOGUEREEL.md`, `docs/MCP.md`, `docs/RECIPES.md`), an example reel in
   Japanese and one in Korean or Spanish.

## 12. Out of scope (v1)

Posting directly to TikTok/Instagram/YouTube; an in-engine LLM that writes dialogue;
automatic furigana/romanization (no tokenizer); more or fewer than two characters;
background scenes or props; a visual character editor; true lip-sync beyond mouth flaps; level checks for languages
other than Japanese.
