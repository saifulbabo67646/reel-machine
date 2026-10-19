# dialoguereel: two characters act out a language lesson

`dialoguereel` makes a vertical 1080x1920 reel in which two cute characters act out a
short conversation in the language being learned: ordering coffee, paying at a konbini,
asking the way. Each line is spoken in its character's voice and shown with furigana, a
romanised reading and a meaning. Key words are underlined and collected on a recap card,
and the reel ends on a "How do you say:" quiz and a follow call to action.

It is for teachers and channels that post daily short lessons for beginners (JLPT N5,
TOPIK 1, CEFR A1). The script is written by an agent, not by the recipe. The recipe checks
it, draws the cast, voices it and renders it.

## Run it

```bash
REEL_TTS=fake REEL_HYPERFRAMES_WORKERS=1 uv run reel run dialoguereel \
  -i @reelmachine/recipes/dialoguereel/examples/konbini-n5-ja.json
```

Both examples are in `storyboard` mode: one sheet of stills, `post.md` and the resolved
script, in under a minute and for nothing. For the video add `-i '{"mode": "full"}'`
(later `-i` values win). The fake voice speaks in tone bursts; for real speech set
`REEL_TTS=macos` and give each character a macOS voice that speaks the language
(`macos:Kyoko` for Japanese, `macos:Yuna` for Korean; `say -v '?'` lists them).

What you need: ffmpeg and ffprobe, Node 22 or newer (HyperFrames runs through `npx`) and
Chrome. A storyboard needs the renderer too.

## The examples

| file | language | level | look | what it shows |
|---|---|---|---|---|
| `reelmachine/recipes/dialoguereel/examples/konbini-n5-ja.json` | Japanese | N5 | `pastel` | Onigiri buys a bento from Cat at the konbini: ください, いります/いりません, `listen_first`, one slow line, ruby on every kanji |
| `reelmachine/recipes/dialoguereel/examples/cafe-topik1-ko.json` | Korean | TOPIK 1 | `night` | Boba orders an iced americano from Bunny in 해요체: 주세요, 가져갈게요, 카드로 할게요, Revised Romanization readings |

Both lint clean (a test keeps them that way) and use `fake:default` voices.

## The agent workflow

Every step is an MCP tool ([MCP.md](MCP.md)). None of them waits for a render.

1. `describe_recipe("dialoguereel")`. Returns the schema, an example and the `guide`: the
   workflow, about 30 situations by level, how to write natural lines, hook titles,
   endings, emotions, the level ladders and the character contract. The guide lives in
   `reelmachine/recipes/dialoguereel/guide.py` and is the source of truth for writing;
   this page shows the shape.
2. Pick a level and one everyday situation. It becomes the `title` (the hook) and `scene`
   (one emoji).
3. `make_cast` with two characters. It returns a `castId` and starts a `cast` job whose
   `cast.png` shows both. Look at it before writing lines for them.
4. Write 5 to 9 lines with `cast` set to the `castId`.
5. `probe`. Checks the script, the lint, the voices, the renderer and any image
   characters, and spends nothing. Fix every `key-missing` and `level` finding.
6. `start_job` in `storyboard` mode and look at `storyboard.jpg`.
7. `start_job` in `full` mode. Paste `post.md` as the caption.

## The script model

| field | default | meaning |
|---|---|---|
| `cast` | required | a `castId` from `make_cast`, or the cast itself (`{"characters": [...]}`) |
| `lines` | `[]` | 3-12 lines (up to 30 parse; lint wants 3-12) |
| `title` | `""` | the hook shown at the start; required outside `cast` mode |
| `language` | `ja` | the language spoken |
| `meaning_language` | `en` | the language of `meaning` and the on-screen labels (en, ja, ko, es, zh) |
| `level` | `""` | `N5`, `TOPIK 1`, `A1`, ...; shown on screen and in the follow line |
| `scene` | none | one emoji for the setting |
| `look` | `pop` | `pop`, `pastel` or `night` |
| `flow` | `standard` | `standard` or `listen_first` |
| `quiz` | `true` | `true`, a 0-based line index, or `false` |
| `mode` | `storyboard` | `cast`, `storyboard` or `full` |
| `lint` | `strict` | `strict` or `warn` |
| `music`, `name`, `outdir` | none | a music file, the output file's stem, its folder |

A line is `{speaker, text, reading, meaning, keys, emotion, slow}`. `speaker` is a cast
id. `text` takes furigana in braces after a kanji run: `袋{ふくろ}はいりますか。`.
`reading` is the romanised line, for any non-Latin script. `keys` (up to 4 per line,
2 to 6 per reel) are `{text, reading, meaning}`; a key must appear in the line as written
or by its stem. `emotion` is `neutral`, `happy`, `surprised`, `sad` or `thinking`.

```json
{"speaker": "customer", "text": "このお弁当{べんとう}をください。", "reading": "Kono obentou o kudasai.",
 "meaning": "This bento, please.",
 "keys": [{"text": "お弁当{べんとう}", "reading": "obentou", "meaning": "boxed lunch"},
          {"text": "ください", "reading": "kudasai", "meaning": "please (give me)"}]}
```

Korean keys match whole words, so key the form the line uses (`가져갈게요`, `카드로`),
not a bare noun followed by a particle.

### The lint

`probe` reports every finding in `details.lint`. Errors stop only a `full` build under
`"lint": "strict"`; everywhere else every finding is a suggestion, listed in
`script.resolved.json`.

| rule | when | severity |
|---|---|---|
| `no-title` | no `title` | error |
| `unknown-speaker` | a line's speaker is not in the cast | error |
| `line-count` | fewer than 3 or more than 12 lines | error |
| `quiz-line` | `quiz` names a line that does not exist | error |
| `ruby` | furigana that does not attach to kanji | warning |
| `no-reading` | a non-Latin line with no `reading` | warning |
| `long-line` | more than 22 characters of CJK or Hangul, 42 of anything else | warning |
| `key-missing` | a key is not found in its line | warning |
| `level` | a Japanese key is harder than the reel's N level (from `reelmachine/data/jlpt_vocab.json`) | warning |
| `same-speaker` | one character speaks more than 3 lines in a row | warning |
| `key-count` | fewer than 2 or more than 6 keys in the reel | warning |
| `length` | the reel would run under 15 s or over 45 s (not counting the listen pass) | warning |

Only Japanese keys are level-checked. For Korean and CEFR levels, choose the words with
care.

## Characters

A cast is exactly two characters on opposite sides, each `{id, name, voice, side, source}`.
`voice` is `<provider>:<voice>` and must name the deployment's narration provider. Voices
are not part of the `castId`; calling `make_cast` again with new voices keeps the id.

- **Built-in**: `{"kind": "builtin", "name": "onigiri"}`, optionally with `colors`
  (`primary`, `secondary`, `accent` as `#rrggbb`). The ten built-ins are `cupcake`, `mug`,
  `onigiri`, `dango`, `boba`, `cat`, `shiba`, `penguin`, `bunny` and `kimchi-jar`
  (`reelmachine/recipes/dialoguereel/characters/index.json`).
- **SVG**: `{"kind": "svg", "svg": "<svg ...>"}`. One root with `viewBox="0 0 400 400"`,
  at most 60 kB, with the required groups (`body`, `eyes-open`, `eyes-closed`,
  `mouth-closed`, `mouth-open`) and optional ones (`arm-left`, `arm-right`, `mouth-smile`,
  `eyes-happy`, `eyes-surprised`, `blush`, `sweat`, `sparkle`). No scripts, no `<style>`
  element, no animation, no external links. `guide.characters` has the full contract, the
  house style and an example.
- **Image, generated**: `{"kind": "image", "provider": "openai", "description": "a cheerful
  onigiri with a nori scarf", "variant": 0}`. The cast stage draws four transparent PNG
  poses (`neutral`, `talk`, `happy`, `surprised`) once and keeps them; later reels reuse
  them. The model has no seed, so raise `variant` (up to 99) to draw a new take.
  `provider: "fake"` draws local stand-ins for tests.
- **Image, supplied**: `{"kind": "image", "poses": {"neutral": "https://.../n.png", ...}}`.
  `neutral` is required; a missing pose falls back to it. URLs must resolve to public
  hosts; local PNG paths work only on local (stdio/CLI) runs, not on a hosted (HTTP)
  server.

**Cost and quota.** A generated character costs 4 OpenAI image calls, once per cast: the
poses are kept, so later reels reuse them. The calls count against the caller's `openai`
quota (`"quotas": {"openai": 40}` in the tenants file), checked when the job is submitted.
`probe` reports `details.castImages` (`{provider, calls, cached}`) and never spends;
`make_cast` replies with `images: {calls, cached}`. Set `REEL_OPENAI_API_KEY`; the model
is `REEL_OPENAI_IMAGE_MODEL` (default `gpt-image-1`). Supplied poses cost nothing.

## Looks

`look` changes colours only, never the cast.

- `pop` (default): saturated electric blue. Bold and loud; suits most lessons.
- `pastel`: cream with dark text. Soft and calm; suits polite, cozy, daily-life scenes.
- `night`: deep navy with neon accents. Suits evenings, travel and anything moody.

## Flow, slow lines, recap and quiz

- `flow: "standard"` plays the hook, then each line with its subtitles, then the recap
  and the quiz.
- `flow: "listen_first"` plays the dialogue once without subtitles ("how much did you
  catch?"), then again subtitled. It doubles the dialogue's time, so keep it for short,
  easy reels (N5-N4, 5-7 lines).
- `slow: true` on a line replays it at 0.75x right after it is spoken. One or two per
  reel, on the hardest line.
- With at least one key, a recap card ("Words from this video") follows the dialogue.
  Japanese keys carry their JLPT level.
- `quiz: true` asks "How do you say:" with the meaning of the line with the most keys,
  counts down, then speaks the answer. A line index picks the line; `false` drops it.
  The reel then shows "Comment your answer" and "Follow for daily <level>".

## Artifacts

| artifact | in | what it is |
|---|---|---|
| `cast.png` | cast | both characters side by side |
| `cast-<id>-<pose>.png` | cast, image characters | each drawn pose |
| `storyboard.jpg` | storyboard | the hook, the listen pass, one still per line, the recap and the quiz |
| `script.resolved.json` | storyboard, full | the script, the resolved cast and every lint finding |
| `post.md` | storyboard, full | a caption: hook, the dialogue with readings and meanings, the words, a call to action, hashtags |
| `reel.mp4` | full | the 1080x1920 video |
| `captions.srt` | full | each line as a caption |
| `reel.json` | full | the manifest the video was rendered from |

## Verify

After a `full` build, `verify` inspects the file: format (1080x1920 with video and audio),
duration within 100 ms of the schedule, an audible voice, no silence longer than 1.5 s
inside the dialogue, each line's text visible on screen when its meaning shows, and the
manifest naming the bound renderer. The report is saved in the job's manifest; a failed
check does not fail the job, so read it before you publish. Cast and storyboard jobs are
not verified.

## Settings

| variable | default | meaning |
|---|---|---|
| `REEL_TTS` | `fake` | the narration provider: `fake`, `macos`, `cartesia`, `elevenlabs`, `supertonic` |
| `REEL_HYPERFRAMES_WORKERS` | auto | `1` streams frames straight to the encoder: slower, light on memory |
| `REEL_OPENAI_API_KEY` | none | needed only for generated image characters |
| `REEL_OPENAI_IMAGE_MODEL` | `gpt-image-1` | the image model |
| `REEL_LOCAL_FILES` | `true` | read local pose paths; the MCP server turns it off over HTTP |
| `REEL_OUTDIR` | `out/` | where `full` mode writes `reel.mp4` (or the script's `outdir`) |

Cloud voices need `CARTESIA_API_KEY` or `ELEVENLABS_API_KEY` and bill per character;
`probe` reports the count. `supertonic` is a self-hosted Supertonic 3 server at
`REEL_SUPERTONIC_URL` (`SUPERTONIC_API_KEY` if it has one): free, with estimated word timings.
