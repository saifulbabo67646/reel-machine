# techreel: funny AI and tech explainers

`techreel` makes a vertical 1080x1920 reel about an AI or tech topic: a new tool, a news
item or a concept ("What is RAG?", "Why small models run on a phone"). A conversational
voice talks over dense motion graphics: app and browser mock-ups, terminals, rolling
counters, rubber stamps, emoji reactions, kinetic headings and word-by-word captions,
with a recurring hero (a chrome orb, a laptop, a phone, a robot) that reacts to the story.
It ends on a "comment KEYWORD" box and a follow card.

The research and the writing are the agent's. The recipe checks the script, voices it,
animates it, renders it and checks the file. It never fetches the web or calls an LLM.

## Ask your agent for one

Connect reel-machine's MCP server to your agent ([MCP.md](MCP.md)), bind a real voice
(Supertonic, below), then ask in plain words:

> Make a techreel about https://github.com/someone/some-tool in Hindi.
>
> Make a techreel explaining vector databases, in English, look violet.
>
> 量子化についてのtechreelを日本語で作って。

The agent reads the guide, does the research, writes the script, checks it, renders a
storyboard and then the reel. You get `reel.mp4` and a `post.md` to paste as the caption.

## Run it

```bash
REEL_TTS=fake REEL_HYPERFRAMES_WORKERS=1 uv run reel run techreel \
  -i @reelmachine/recipes/techreel/examples/en-what-is-rag.json
```

The examples are in `storyboard` mode: one sheet of stills, `post.md` and the resolved
script, in about a minute and for nothing. For the video add `-i '{"mode": "full"}'`
(later `-i` values win). The fake voice speaks in tone bursts; for real speech run a
Supertonic server and add `REEL_TTS=supertonic` and a Supertonic voice:

```bash
REEL_TTS=supertonic REEL_HYPERFRAMES_WORKERS=1 uv run reel run techreel \
  -i @reelmachine/recipes/techreel/examples/hi-pocket-llm.json \
  -i '{"mode": "full", "voice": "supertonic:M1"}'
```

What you need: ffmpeg and ffprobe, Node 22 or newer (HyperFrames runs through `npx`),
Chrome, and the fonts below. A storyboard needs the renderer too.

## The examples

| file | language | look | hero | what it shows |
|---|---|---|---|---|
| `reelmachine/recipes/techreel/examples/hi-pocket-llm.json` | Hindi | `mint` | orb | a new-tool reel about a fictional open-source app: Devanagari `say`, Hinglish `text`; the orb locked and split; repo, chips, terminal, window, receipts, versus, error, flash; the default ending |
| `reelmachine/recipes/techreel/examples/en-what-is-rag.json` | English | `violet` | robot | a concept reel: analogies drawn literally (an open-book exam, a library, a pipeline, the office where three people know); the robot asleep and searching; chat, nodes, people, equation, checklist, svg, image; an authored ending with a callback to the hook |
| `reelmachine/recipes/techreel/examples/ja-tiny-models.json` | Japanese | `ember` | phone | small, quantised models: kana numbers in `say`, digits in `text`, cues on runs of characters; the phone's screen; counter, equation, bars, dialog, heartbeat, a drawn compression bag; the default ending with a `next` tease |

All three lint clean (a test keeps them that way), run about 44 s each with Supertonic and use
`fake:default` so they run anywhere. Their facts are invented for the examples
(`https://example.com/...` sources); a real reel's are researched.

## The agent workflow

Every step is an MCP tool ([MCP.md](MCP.md)). None of them waits for a render.

1. `describe_recipe("techreel")`. Returns the schema, the Hindi example and the `guide`:
   the workflow, how to research, the formula (hook, pain, reveal, explain/features,
   honest, cta), humour rules with examples in three languages, which visual draws which
   analogy, the `say`/`text` rules and number spellings per language, every component with
   its fields and an example, the heroes and their states, and the ending. The guide lives
   in `reelmachine/recipes/techreel/guide.py` (its component reference is generated from
   the models) and is the source of truth for writing; this page shows the shape.
2. Research. The agent reads the link or topic itself (README, release notes, official
   site) and notes every number with its source. Nothing is invented; the URLs go in
   `sources`.
3. Write the beats: 6 to 10 of them, 45 to 90 s, one idea each, a joke every two or three
   lines, a visual cued on the word that names it.
4. `probe`. Checks the lint, the drawings and image sources, the voice and the renderer,
   estimates the length per beat, and spends nothing. Fix every error; read the warnings
   as a reviewer's notes.
5. `start_job` in `storyboard` mode and look at `storyboard.jpg` (stills at each beat's
   middle and last cue). Fix a crowded stage, a stamp over the wrong thing, a caption that
   says too much.
6. `start_job` in `full` mode. Read the job's verification, then paste `post.md` as the
   caption.

## The script model

| field | default | meaning |
|---|---|---|
| `title` | required | the reel's name; its words seed `post.md`'s hashtags and the default comment keyword (not shown on screen) |
| `beats` | `[]` | up to 40 beats; the guide asks for 6 to 10 and the lint for 30 to 150 s |
| `language` | `en` | the language spoken (BCP-47: `hi`, `en`, `ja`, ...) |
| `voice` | `fake:default` | one narrator, `<provider>:<voice>`, e.g. `supertonic:M1` |
| `look` | `mint` | `mint`, `ember`, `violet` or `mono` |
| `accent` | none | `#rrggbb` to override the look's accent colour |
| `hero` | none | `{kind, text?, svg?, src?}`: `orb`, `laptop`, `monitor`, `chip`, `phone`, `robot`, `svg` (your drawing) or `image` (a URL) |
| `creator` | none | `{name, handle, avatar?}` for the follow card |
| `sources` | `[]` | every fact's primary source URL; into `post.md` and `reel.json`, never on screen |
| `music` | none | `"auto"` (a synthesised bed) or a local audio file |
| `sfx` | `true` | sound effects on stamps, flashes, transitions |
| `mode` | `storyboard` | `storyboard` or `full` |
| `lint` | `strict` | `strict` (errors stop a `full` build) or `warn` |
| `name`, `outdir` | none | the output file's stem, its folder |

A **beat** is `{id?, kind, heading?, kicker?, lines, visuals, transition}`:

- `kind` is `hook`, `pain`, `reveal`, `explain`, `feature`, `honest`, `cta` or `other`.
- `heading` is the big kinetic text at the top; `*word*` paints a word in the accent
  colour. `kicker` is the small label above it ("FEATURE 01/03").
- `lines` are 1 to 4 short lines (up to 8 parse), each `{say, text?}`.
- `transition` into the beat: `cut`, `slide`, `flash` or `zoom`.
- A `cta` beat also takes `keyword` (the word to comment), `dm` (the reply chip) and
  `next` (a tease for the next reel).

A **line** is what the voice says and what the caption shows:

- `say` is exactly what the voice reads, in the voice's script, with numbers and names
  with digits written as words, and no `*` markup.
- `text` is the caption: any script, digits kept, `*accent*` words marked. It defaults to
  `say`.

```json
{"say": "सिर्फ़ चार जीबी रैम में, बिना इंटरनेट के।", "text": "Sirf *4 GB* RAM mein, bina internet ke."}
```

A **visual** is one of 25 components, each with `at` (a cue: `{line, word?, frac?}`,
default the beat's start), `exit` (a cue, or `"beat"`, the default) and `place`
(`stack`, `left`, `right`). A cue's `word` is a word of that line's caption, matched
without case or punctuation; in Japanese it is a run of characters as it appears.

```json
{"kind": "counter", "to": 4, "suffix": " GB", "label": "RAM KAAFI HAI", "at": {"line": 1, "word": "4"}}
```

### Components and heroes

`describe_recipe("techreel").guide.components` lists every component with its required
and optional fields and an example: `title`, `counter`, `stamp`, `emoji`, `chips`,
`window`, `terminal`, `repo`, `equation`, `chat`, `dialog`, `error`, `receipts`,
`checklist`, `people`, `versus`, `nodes`, `bars`, `flash`, `heartbeat`, `comment`,
`follow`, `image`, `svg`, and `hero` (which moves the hero rather than adding a block).
Stamps, emoji, error boxes and flashes are overlays: they take no stage space.

The hero persists across beats. A `{"kind": "hero", "state": ...}` visual moves it:
`center`, `small-top`, `corner`, `hidden`, `pulse`, `shake`, `rings`, `screen` (a device
shows `text`), `sleep`, `locked` and `split` (the orb or robot splits into 2 to 6 labelled
dots). `guide.heroes` and `guide.visuals.analogies` say which picture fits which idea.

Agent drawings (`svg` visuals and an `svg` hero) follow dialoguereel's safety rules: at
most 60 KB, a viewBox, no script, `foreignObject`, event attributes or external
references, embedded images as `data:` PNG/JPEG only.

Images (`image` visuals, an `image` hero, the creator's `avatar`) are https URLs, or local
paths on local (CLI or stdio) runs only. They are downloaded once through a guard that
refuses private addresses, normalised to PNG of at most 1600 px and **kept per caller
without expiry**: to refresh an image, change its URL (for example add `?v=2`).

### The ending

A `cta` beat with no visuals of its own (hero moves aside) gets the standard ending: the
comment box typing `keyword` (default: the title's first word) at line 0 with the `dm`
chip, the creator's follow card at line 1 (needs `creator`), and `next` as a title on the
last line. Author `comment` and `follow` visuals to place them yourself.

## Languages and voices

Any language the voice speaks works; Hindi, English and Japanese are tested, with their
number spellings and humour examples in the guide.

- **Hindi**: `say` in Devanagari, English tech words transliterated ("लैपटॉप", "सर्वर");
  `text` in Hinglish Latin script ("Sirf *4 GB* RAM mein"). Hindi numbers 11 to 99 are
  irregular words: look each one up (19 उन्नीस, 25 पच्चीस).
- **English**: `text` is usually left out; give it only to show digits or accents
  (say "fourteen gigabytes", text "*14 GB*").
- **Japanese**: `say` with numbers in kana ("よんビット") and Latin acronyms in katakana
  ("エーアイ"); `text` only to show digits ("*4ビット*"). Captions chunk at 14 characters,
  so break lines with 、 and 。.

### Supertonic

Supertonic 3 is the recommended voice: free, self-hosted, and it speaks en, hi, ja and
more.

| variable | default | meaning |
|---|---|---|
| `REEL_TTS` | `fake` | set `supertonic` to bind it |
| `REEL_SUPERTONIC_URL` | `http://127.0.0.1:8787` | where the server listens |
| `SUPERTONIC_API_KEY` | none | the server's key, for a server on another machine on the LAN; leave unset for a keyless loopback server |

Voices are `supertonic:F1` to `supertonic:F5` (female) and `supertonic:M1` to
`supertonic:M5` (male); every voice speaks every language. The examples were rendered
with `supertonic:M1` (Hindi, English) and `supertonic:F1` (Japanese). The voice must name
the bound provider: with `REEL_TTS=supertonic`, a `fake:default` voice is a lint error
(`voice-provider`) and `probe`'s `voice` check fails. Each line is spoken once and cached
by (provider, voice, language, `say`), so re-rendering after a visual change costs no
speech.

`macos`, `elevenlabs` and `cartesia` work too (`CARTESIA_API_KEY` or `ELEVENLABS_API_KEY`;
cloud voices bill per character, and `probe` reports the count).

### Measured pitfalls

Measured on a Supertonic 3 server; the lint and the guide encode each one.

- **Digits are misread in every language**: "176" came out "1 in 76" in English and
  nonsense in Hindi. Names with digits too: "GPT-4o" became "GPT-40", "H100" "H-Wonder".
  So a digit in `say` is a lint error: spell it ("G P T four o", "H one hundred",
  "जीपीटी फ़ोर ओ") and keep the digits in `text`.
- **Hindi needs Devanagari**: Hinglish in Latin letters under `hi` comes out garbled
  (Whisper heard German). `say` in Devanagari; the Latin goes in `text` (lint
  `hindi-latin` warns when `say` is mostly Latin).
- **Latin letters inside Devanagari**: a Hindi voice does not spell Latin acronyms. "AI"
  in a Devanagari `say` comes out as a long "एएएए": write "एआई". Write an acronym as
  spaced letters: "एल एल एम", not "एलएलएम", which runs together.
- **A sentence end inside a line is a long pause**: `?`, `.` or `...` in the middle of a
  `say` makes the voice stop for 1.0 to 1.4 s, enough to fail verify's `silence` check
  ("Your chatbot? Still confident." paused 1.1 s twice). Use a comma or a colon in `say`
  and keep the question mark in `text`, or end the line there and start a new one. A line
  break costs only the 120 ms line gap.
- **Acronyms said as words**: "RAG" in capitals came out "Reg" or "Rage"; "Rag" is said
  as "rag". Write how it is said in `say` and the capitals in `text`.
- **Japanese kanji the voice stumbles on**: 量子化 and 圧縮袋 were misread; write them
  in kana in `say` ("りょうしか", "あっしゅくぶくろ") and keep the kanji in `text`.

## Fonts

No fonts are bundled ([THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)). HyperFrames
supplies Archivo Black, Montserrat, JetBrains Mono and Noto Sans JP; the Devanagari and
the Hiragino faces are the operating system's.

| OS | what to install |
|---|---|
| macOS | nothing: Kohinoor Devanagari and Hiragino Sans ship with the system |
| Debian, Ubuntu | `sudo apt install fonts-noto-core` (Noto Sans Devanagari); `fonts-noto-cjk` if Japanese should not depend on HyperFrames' copy |
| Fedora | `sudo dnf install google-noto-sans-devanagari-fonts` (and `google-noto-sans-cjk-fonts` for Japanese) |

A missing Devanagari font shows as boxes in the storyboard: look at it before a `full`
render.

## Looks

`look` changes colours only. One look per reel; `accent` overrides the accent colour.

- `mint` (default): near-black with a teal accent. Clean, techy; suits tools and news.
- `ember`: warm charcoal with an orange accent. Energetic; suits hardware and hot takes.
- `violet`: deep indigo with a violet accent. Suits concepts and AI explainers.
- `mono`: black and white. Serious; suits a sober story or a brand accent via `accent`.

## Music and sound

`sfx` (on by default) adds designed effects: a whoosh on transitions and flashes, a pop
as each block enters, a thud on stamps and error boxes, a ding when a counter lands or the
follow card flips, a tick as a terminal command or the comment keyword types; at most 80
per reel. `music: "auto"` adds a synthesised bed; a
local audio file works on local runs. Music ducks under the voice. All of it is mixed and
mastered with the voice in one pass; the examples leave music off.

## Artifacts

| artifact | in | what it is |
|---|---|---|
| `storyboard.jpg` | storyboard | stills at each beat's middle and last cue |
| `script.resolved.json` | storyboard, full | the script and every lint finding |
| `post.md` | storyboard, full | a caption to paste: the hook, a summary line per beat, the call to action, the sources and 8 to 12 hashtags. Built from the captions (`text`), never from a Hindi `say` |
| `reel.mp4` | full | the 1080x1920, 30 fps video |
| `captions.srt` | full | the captions as spoken, chunk by chunk |
| `reel.json` | full | the manifest: duration, look, voice, renderer, music, sources |

With `name`, the video, captions and manifest take that stem (`pocket-llm.mp4`). The
rendered video is cached by its document, so a re-run with the same script and voice only
re-mixes the audio.

## The lint

`probe` reports every finding in `details.lint`. Errors stop only a `full` build under
`"lint": "strict"`; everywhere else every finding is a suggestion, listed in
`script.resolved.json`.

| rule | when | severity |
|---|---|---|
| `no-beats` | the reel has no beats | error |
| `voice-provider` | `voice` is not from the bound provider | error |
| `empty-say` | a line's `say` is empty | error |
| `digit-in-say` | a digit in `say` | error |
| `markup-in-say` | a `*` in `say` | error |
| `cue-line` | a cue names a line the beat does not have | error |
| `svg` | a drawing breaks the safety rules | error |
| `image-local` | a local image path on a hosted (HTTP) run | error |
| `image-scheme` | an image that is neither https nor a local path | error |
| `cue-word` | a cue's `word` is not in its line's caption (it falls back to the line's start) | warning |
| `text-mismatch` | `text` is under 0.4x or over 2.5x the length of `say` | warning |
| `hindi-latin` | a Hindi `say` mostly in Latin letters | warning |
| `stamp-long` | a stamp over 16 characters | warning |
| `active-tab` | a window's `active` is not one of its tabs | warning |
| `hook-slow` | the hook has no visual, or its first line runs over 3.5 s | warning |
| `beat-long` | a beat with over 4 lines or over 9 s | warning |
| `static` | over 3.5 s inside a beat with nothing new on screen | warning |
| `crowded` | more than 4 visuals on screen at once (the hero does not count) | warning |
| `length` | the reel runs under 30 s or over 150 s | warning |
| `structure` | the first beat is not a hook, the last not a cta, or there is no honest beat | warning |
| `cta-no-creator` | the default ending has no `creator` for its follow card | warning |
| `no-sources` | `sources` is empty | warning |

## Verify

After a `full` build, `verify` inspects the file: format (1080x1920 with video and
audio), duration within 100 ms of the schedule, an audible voice, no silence in the voice
longer than 1 s between the first beat and the last, something on the stage at each beat's
midpoint (compared with the bare background), and the manifest naming the bound renderer.
The report is saved in the job's manifest; a failed check does not fail the job, so read
it before you publish. Storyboard jobs are not verified.

## Settings

| variable | default | meaning |
|---|---|---|
| `REEL_TTS` | `fake` | the narration provider: `fake`, `macos`, `cartesia`, `elevenlabs`, `supertonic` |
| `REEL_SUPERTONIC_URL` | `http://127.0.0.1:8787` | the Supertonic server |
| `SUPERTONIC_API_KEY` | none | its key, if it has one |
| `REEL_HYPERFRAMES_WORKERS` | auto | `1` streams frames straight to the encoder: slower, light on memory |
| `REEL_LOCAL_FILES` | `true` | read local image and music paths; the MCP server turns it off over HTTP |
| `REEL_OUTDIR` | `out/` | where `full` mode writes `reel.mp4` (or the script's `outdir`) |

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `probe`'s `voice` check fails, or lint `voice-provider` | the script's voice is not from the bound provider: set `REEL_TTS=supertonic` and a `supertonic:` voice (or match whatever is bound) |
| narration fails with a connection error | the Supertonic server is not running at `REEL_SUPERTONIC_URL`; for a server on another machine set the URL and `SUPERTONIC_API_KEY` |
| the voice reads a number or model name strangely | a number slipped into a name or was spelled too tersely: spell it as it should sound ("R T X forty ninety") |
| Hindi sounds like another language | `say` is in Latin letters: write it in Devanagari (lint `hindi-latin`) |
| "AI" becomes a long "एएएए" | write "एआई"; spaced letters for other acronyms ("जी पी यू") |
| boxes instead of Devanagari | no Devanagari font: install `fonts-noto-core` (Linux) |
| a visual enters at the start of a line, not on its word | lint `cue-word`: the word is not in the caption as written; use the caption's word (Japanese: the exact run of characters) |
| a caption word lights up slightly before it is spoken | Supertonic's word timings are estimates; see Known limitations |
| an image did not update after you changed the file at its URL | images are cached per caller with no expiry: change the URL (`?v=2`) |
| `image-local` error over MCP | a hosted server reads no local files: use an https URL |
| the stage looks crowded or a stamp covers the number | give earlier visuals an `exit`, keep stamps to one or two words, check the storyboard |
| verify `silence` fails | most often a `?` or `.` in the middle of a `say` (a pause over 1 s): use a comma there, or split the line; otherwise listen around the reported time |
| the render runs out of memory | `REEL_HYPERFRAMES_WORKERS=1` |

## Known limitations

- The orb hero is stylised (a shaded chrome sphere), not photoreal.
- Long Japanese titles and headings cannot wrap mid-word the way spaced scripts do: keep
  Japanese headings short.
- Supertonic's word timings are estimates (spread by length within each line), so
  captions may lead the voice by up to about 300 ms after commas.
