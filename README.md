# reel-machine

Search Japanese dialogue with the **[Nadeshiko API](https://nadeshiko.co)**, cut the
matching scenes out of **your own** streaming library with ffmpeg, and render a
subtitled vertical reel — original audio, Japanese line, English translation.

```
Japanese word ──▶ Nadeshiko /v1/search ──▶ anime + episode + startTimeMs/endTimeMs
                                              │
                                              ▼
                      align against YOUR copy (ffmpeg + cross-correlation)
                                              │
                                              ▼
                     cut · subtitle (JA+EN) · join · burn ──▶ out/reel.mp4
```

---

## The one thing that will bite you

**Nadeshiko's timestamps are not relative to your file.**

`Segment.startTimeMs` is an offset into the *external source video* the episode
was indexed from — `Segment.externalVideoId` is a YouTube id. Your library's
copy is a different release: different encode, often a longer intro, sometimes
a TV cut vs a Blu-ray cut, occasionally a different frame rate.

So `ffmpeg -ss startTimeMs` against your file lands near the scene, or nowhere
at all. This tool **measures** the mapping instead of assuming it:

1. Every segment ships with `urls.audioUrl` — a ground-truth audio clip of
   exactly that line, from the indexed source.
2. Decode your episode and that clip to RMS envelopes (codec/bitrate/volume
   independent) and slide the clip across the episode with FFT
   normalised cross-correlation.
3. One anchor gives a constant offset; several anchors spread across the
   episode give an **affine** map `local = a·src + b`, so a frame-rate
   difference or inserted footage shows up as a slope rather than silently
   desyncing the end of the reel.
**Cost:** aligning one episode decodes its whole audio track, and for an HLS
source that means downloading the whole episode (a 24-minute 1080p episode is
roughly 150 MB, once — the resulting envelope is cached in `.work/cache/`).
Cutting afterwards only re-reads the few seconds it needs. If you are running
this across a large library over a metered connection, that is the number to
plan around.

4. Anchors that disagree (a repeated catchphrase matching twice) are dropped by
   a median-residual filter, and the whole episode is refused if the survivors
   still disagree by more than 250 ms — better a failed run than a wrong cut.

Measured drift is printed per episode and stored in the plan and the manifest.

```console
$ uv run reel align --media V1StGXR8_Z5d --ep 3 --word 彼女
source: https://your-server/hls/bakuman/ep-003/master.m3u8
        1420.0s · 1920x1080 · h264/aac
reference clips: 6/6
┏━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┓
┃ segment     ┃  src ms ┃ local ms ┃ predicted ┃ residual ┃   ncc ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━┩
│ K3n8Qz1pLd2a │  200725 │   203225 │    203225 │       +0 │ 0.812 │
│ 9dPqR4mXvT1s │  611004 │   613504 │    613504 │       +0 │ 0.774 │
│ Zq2Wm8NcB4vk │ 1188320 │  1188820 │   1188820 │       +0 │ 0.690 │
└─────────────┴─────────┴──────────┴───────────┴──────────┴───────┘
aligned — offset +2500 ms, 6 anchor(s), conf 0.74
mapping: local_ms = 1.000000 * src_ms +2500  (constant offset +2500 ms)
```

That `+2500 ms` is your copy's longer intro. Cut with it and the line lands
exactly.

---

## Install

```bash
cd reel-machine
uv sync                                # creates .venv from uv.lock and installs `reel`
cp .env.example .env                   # then edit
```

Everything runs through `uv run`, which uses the locked environment — no manual
activating, no global installs:

```bash
uv run reel doctor --selftest -v
uv run pytest
```

`uv.lock` is committed, so a fresh checkout resolves to exactly the versions
this was built and tested against. `.python-version` pins the interpreter
(3.13) and `uv` will fetch it if you don't have it.

Requires **ffmpeg** on `PATH` (built and tested against ffmpeg 8.0).
`ffprobe` is used when present; if your build does not ship one (standalone
static builds often don't), the tool falls back to parsing `ffmpeg -i` and
tells you so in `reel doctor`.

Get an API key at <https://nadeshiko.co/user/developer>.

---

## Quick start

```bash
# 1. Prove the pipeline works with zero setup (synthetic episode, known offset)
uv run reel doctor --selftest -v

# 2. What does the corpus have for this word?
uv run reel search 彼女 -n 15

# 3. Search + select + align. Costs API quota, downloads nothing heavy.
uv run reel plan 彼女 -c 5

# 4. Cut, subtitle, render.
uv run reel build 彼女 -c 5 --aspect vertical
```

### What `--selftest` proves

It builds a synthetic episode, fabricates segments whose timestamps are shifted
by a **known** +2500 ms, and asserts the whole chain end to end — so a green
run means the alignment genuinely measured the offset rather than echoing its
input:

```console
$ uv run reel doctor --selftest -v
  anchor 1/3 f2432d0a4be1: src 3500 ms -> local 6020 ms [accept] score 0.49 margin +0.08 (clear) clip 870 ms drift +2520 ms
  anchor 2/3 02a87d90bb5d: src 49444 ms -> local 51965 ms [accept] score 0.48 margin +0.02 (ambiguous) clip 900 ms drift +2521 ms  (windowed)
  anchor 3/3 9d8a4dcad5dc: src 92426 ms -> local 94950 ms [accept] score 0.62 margin +0.24 (clear) clip 1735 ms drift +2524 ms  (windowed)
  alignment: offset +2519 ms, 3 anchor(s), conf 0.49
  expected offset: +2500 ms   measured: +2519 ms
  duration: container 5.930s vs cues 5.900s (drift +30 ms)
  streams:  video 5.930s · audio 5.920s
selftest passed — alignment, cut, subtitle and mux all verified
```

Note anchor 2: it is marked **ambiguous** (a rival peak scored nearly the same)
and is only accepted because anchor 1 had already pinned the offset and the
search was windowed around it. That is the mechanism that keeps a repeated
catchphrase from pulling a cut into the wrong scene.

Outputs land in `out/`:

| file | what |
|---|---|
| `<word>-<stamp>.mp4` | the reel |
| `<word>-<stamp>.json` | manifest: every cut, its drift, its source, its confidence |
| `.work/render/<name>/<name>.ass` | the subtitle file (tweak and re-burn for free) |
| `.work/render/<name>/<name>.srt` | bilingual SRT |

Re-rendering with a different aspect ratio, font or pre/post roll costs **no
API quota** — the plan is cached and re-runs hit the disk cache.

---

## Connecting your streaming server

Streaming hosts rarely serve a stable, guessable URL. The usual shape is:

```
https://fetch.example.top/anime/<animeHash>/<episodeHash>/master.m3u8?token=<signed>
```

with an opaque hash per title, another per episode, and a **time-limited signed
token**. A plain URL template cannot express that, so the provider composes the
URL from three pieces:

| piece | setting | why |
|---|---|---|
| path shape | `REEL_HLS_TEMPLATE` | placeholders for the parts that vary |
| opaque ids | `REEL_HLS_MAP` | hashes cannot be derived — record them once per title |
| fresh token | `REEL_HLS_RESOLVER_CMD` *or* `REEL_HLS_TOKEN_CMD` | the signature expires |

```env
REEL_SOURCE=hls
REEL_HLS_TEMPLATE=https://fetch.example.top/anime/{animeHash}/{episodeHash}/master.m3u8?token={token}
REEL_HLS_QUALITY=1080
REEL_HLS_MAP=site-map.json
REEL_HLS_RESOLVER_CMD=python3 scripts/mint_url.py

REEL_SOURCE_REFERER=https://megaplay.buzz/
REEL_SOURCE_ORIGIN=https://megaplay.buzz
REEL_SOURCE_USER_AGENT=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36
```

`site-map.json` keys a title by `mediaPublicId`, AniList id, or slug:

```json
{
  "97886": {
    "animeHash": "abb207957b0abc1d85a7e32ab1c4359c",
    "episodes": { "10": "dfa8b25e59b272cefaa749f2f61c73e4" }
  }
}
```

Both resolver commands are run as a subprocess and receive the episode context
as `REEL_*` environment variables (`REEL_MEDIA_PUBLIC_ID`, `REEL_EPISODE`,
`REEL_ANILIST`, `REEL_SLUG`, `REEL_NAME_EN`, …). The **last** non-empty stdout
line is used, so a script may log before printing the value. `_CMD` variants
mean you never have to paste a URL into a config file.

#### Hosts that disguise their segments

A second real host showed a shape worth knowing about: a **media** playlist (no
renditions at all), no query token — the credentials live in the path — and
every fMP4/CMAF segment named `seg.jpg`. ffmpeg's HLS demuxer rejects those
outright:

```
URL https://…/seg.jpg is not in allowed_segment_extensions,
consider updating hls.c and submitting a patch to ffmpeg-devel
```

`reelmachine/ffmpeg.py` handles it automatically for any `.m3u8` URL by widening
the extension whitelist and switching off `extension_picky` (a disguised
extension can never match the demuxer's real one). It probes the binary first,
because `extension_picky` only exists in ffmpeg 6.0+ and an unknown private
option is a hard error. Set `REEL_HLS_EXTENSION_PICKY=true` to opt back into
ffmpeg's strict default.

Both `Referer`-enforcing hosts and open ones are fine — headers are simply sent
when configured.

#### Why a token at all

The signature is not a cookie or a session — it is part of the URL:

```
token = base64url("<expiry_unix>|<animeHash>/<episodeHash>") + "." + base64url(HMAC-SHA256(...))
```

The secret is server-side, so a token cannot be forged or cached; it has to be
minted per run. That is what the resolver seam is for.

Test the configuration before running a full alignment — this fetches the
playlist and reports the renditions, without downloading any media:

```bash
uv run reel probe --title "Blood Blockade Battlefront" --ep 10
uv run reel probe --url "https://fetch.example.top/.../master.m3u8?token=..."
```

If the token has expired you get a `403` with that explanation rather than a
confusing ffmpeg error several steps later.

Other template placeholders: `{slug}` `{nameEn}` `{nameRomaji}` `{nameJa}`
`{anilist}` `{tmdb}` `{tvdb}` `{imdb}` `{mediaPublicId}` `{season}` `{ep}`
`{ep2}` `{ep3}` `{quality}` `{animeHash}` `{episodeHash}` `{token}`.

If the resolved URL is a *master* playlist, the best rendition at or below
`REEL_HLS_QUALITY` is selected; if it is already a media playlist it is used
as-is. `Referer` / `Origin` / `Cookie` / `User-Agent` are sent on the playlist
fetch **and** handed to ffmpeg, which needs them again for every segment
request — a host that 403s on a missing `Referer` is a normal case, not an
edge case, and there is a test that stands up such an origin.

### vidvault: download first, then cut

If streaming is not workable, the download route is simpler and more reliable:
fetch the episode once, then cut from the local file (and re-cut for free
forever after).

```env
REEL_SOURCE=vidvault
REEL_TMDB_API_KEY=<free key from themoviedb.org/settings/api>
REEL_VIDVAULT_QUALITY=720
REEL_VIDVAULT_DIR=.work/downloads
REEL_VIDVAULT_CONNECTIONS=8
```

The chain, reproduced from the site's own frontend (no account needed):

```
Nadeshiko media ──▶ TMDB id ──▶ GET  /api/get-token      (~60 s lifetime)
                                 POST /api/download-proxy (x-request-token)
                                 ◀── renditions: mp4Data.links[] · mkvData
                                     mkvV2Data · mkvV3Data
                                 ──▶ download the signed CDN url ──▶ local file
```

There is **no browser-side step to reproduce**: the frontend's own code is a bare
`fetch('/api/get-token')` followed by `fetch('/api/download-proxy', {headers:
{'x-request-token': …}})` — the same two calls this provider makes. There is no
captcha, no Turnstile/hCaptcha, no signed nonce, and no Cloudflare interstitial
(`cf-mitigated` is absent; the API answers the same payload to a minimal header
set, a full desktop-Chrome header set, mobile Safari and curl alike). A headless
browser would issue byte-identical requests from the same IP and receive
byte-identical responses, so it buys nothing here — the two things that actually
break this source are handled in Python instead:

**1. The payload gets reshaped.** Renditions moved from
`mp4Data.downloadInfo.data.downloads[]` (rich: integer `size`, `duration`,
`vipLocked`) to `mp4Data.links[]` (lean: `url`, `quality: "720p"`, `size:
"289 MB"`) with siblings `mkvData` / `mkvV2Data` / `mkvV3Data`. When only the old
shape is parsed, every episode resolves to *zero* streams and the error is the
misleading "vidvault returned no usable stream (0 entries)". `parse_streams()`
now reads every generation, and `coerce_size()` accepts bytes, a byte string, or
a human label.

**2. The CDN throttles per connection.** Measured against the live host: one
connection sustains ~0.17–0.25 MB/s regardless of what is asked of it, while
aggregate throughput scales with concurrency (2 → 0.52, 4 → 1.04, 8 → 1.78 MB/s),
and long single-stream transfers are **cut off part-way**. So the episode is
fetched as 4 MB byte ranges across `REEL_VIDVAULT_CONNECTIONS` workers, each
range retried independently and banked in a `.part.json` checkpoint, so a
dropped connection costs one range instead of the whole file. `=1` forces the
old single-stream path, and a host that ignores `Range` falls back to it
automatically. End to end against the live CDN: a 78 MB episode completes in
~100 s with no truncation.

**Bridging the ids is the hard part.** Nadeshiko identifies media by AniList id;
vidvault keys on TMDB; and `externalIds.tmdb` is populated for only about 1 in
20 titles (measured: anilist 19/20, tmdb 1/20, imdb 0/20). So the bridge is a
title search, which needs two corrections:

* **Season folding.** Nadeshiko treats each season as its own entry
  (`Rent-a-Girlfriend Season 2`); TMDB folds them into one show. The season
  number is parsed out of the title and removed before searching, so
  `Rent-a-Girlfriend Season 3` becomes show `92683` season `3`. `& Beyond`,
  `Part 2` and `Cour 2` are handled too.
* **Numbering drift.** Once the season is right the episode number is *assumed*
  to line up. That assumption is checked twice: vidvault reports each stream's
  `duration`, Nadeshiko reports `Episode.lengthSeconds`, and a large gap is
  surfaced as a warning; the audio alignment is the final gate and simply
  refuses rather than cutting the wrong scene. The newer payloads carry no
  `duration` at all, so the cross-check only fires when one is present —
  `ffprobe` backfills the real duration from the downloaded file.

Measured on 19 live titles: **18 resolved** to a confident TMDB match with
correct season numbers.

#### Things this source will do to you

| behaviour | why it matters |
|---|---|
| the request token lives ~60 s | it is minted immediately before each lookup, never cached across episodes |
| `download-proxy` **caches its responses** | a reply can carry CDN links minted hours earlier; age is treated as a preference, not a veto (`REEL_VIDVAULT_MAX_LINK_AGE_S`) |
| link age is only knowable on **old** links | the newer URLs are opaque path tokens (`/dl/<token>`) with no `?t=` timestamp, so `age_s()` is `None`, `REEL_VIDVAULT_MAX_LINK_AGE_S` cannot veto them, and recovery from a dead link rests entirely on the retry |
| the payload **changes shape without notice** | it silently went from `downloadInfo.data.downloads[]` to `links[]`; if `reel probe` reports `0 entries` for a title that certainly exists, suspect the parser before the network |
| the CDN 429s non-browser clients | the site's `User-Agent`/`Referer` are sent on every request *and* to ffmpeg; **dropping `Referer` is a hard 404**, not a soft failure |
| the CDN throttles **each connection** to ~0.25 MB/s | a single-stream fetch of one episode takes ~8 minutes and often truncates; hence `REEL_VIDVAULT_CONNECTIONS` |
| rendition labels are unreliable | a "480p" link served 720×408, and "1080p" came back *smaller* than "720p" (80 MB vs 303 MB). `REEL_VIDVAULT_QUALITY` picks by label, so check sizes with `reel probe` |
| `mp4Data` links are actually **Matroska** | `video/x-matroska` with EBML magic, despite the key and the `.mp4` local filename; ffmpeg demuxes by content, so cuts are unaffected |
| dubs exist, and an explicit **Japanese tag wins** | the CDN tags some renditions (`mkvV2Data`) with a language and serves dubs — one title carried Hindi and English and no Japanese at all. `pick_stream` therefore hard-prefers a Japanese-tagged rendition over a higher-resolution untagged one: a dub cannot be aligned at all, so it wastes the whole download, whereas dropping a resolution only costs sharpness. `reel probe` shows the tag per rendition |
| but the tag is **not** the whole story | untagged `mp4Data.links` renditions are a gamble — one title's 720p carried `hin`/`eng`/`jpn` while its 480p carried `hin`/`eng` and no Japanese. The tag is the only *signal* the API gives, so it is trusted when present; when it is absent, `pick_japanese_track` still picks the Japanese track out of the file, and a file with none is refused by the alignment rather than cut |
| the two CDN workers differ wildly in speed | the tagged Japanese renditions come from the `mk2` worker and the untagged ones from `tdm`; measured here, `mk2` ranged from **0.03 to 0.39 MB/s** across samples while `tdm` reached 0.4–1.6 MB/s per connection and **6–17 MB/s** once range-parallel. `mk2` is merely slow and bursty rather than dead, which is exactly what the checkpoint, per-range retry and re-mint are for — a 213 MB episode did stall for minutes at ~3 KiB/s and still resume |
| **renditions are tried in order, not chosen once** | the fast host serves untagged renditions that may be dubs; the Japanese-tagged ones sit on a worker measured at ~3 KiB/s. `rank_streams` orders candidates by quality ladder, then preferred host, then Japanese tag, and `resolve` walks that list — probing each untagged candidate's audio with a ~6 MB ranged fetch before committing. On *DARLING in the FRANXX* this turns a 118 MB tagged rendition on the slow host into an 80 MB **1080p** rendition on the fast one, verified `['eng', 'jpn']` in 3 s |
| a stalled transfer must fail, not hang | two separate bounds, because they catch different failures: a socket timeout for silence, and a 48 KiB/s throughput floor for a link that keeps trickling and never finishes. Without the floor a 3 KiB/s link hangs indefinitely — which is how two drama downloads wedged until killed by hand |
| a mis-picked season wastes a download | run `reel probe --title "…" --ep N` first; it reports the match, the renditions and the size without downloading |

### What a reel looks like

Vertical 1080×1920, three bands:

```
┌──────────────────────────────┐
│  ◉文   Yakusoku  N4          │  vocabulary card — romaji, meaning,
│       promise, agreement     │  kana, kanji.  The lesson, so it is
│       やくそく                │  shown for the whole reel.
│       ┌────────┐             │
│       │ 約束   │             │
│       └────────┘             │
├──────────────────────────────┤
│                              │
│        the video clip        │  sharp, over a blurred fill
│                              │
├──────────────────────────────┤
│  It's true that I might have │  English
│  made such a promise …       │
│   たしかに  むかし   やくそく  │  furigana
│  確かに昔 そんな約束を…        │  Japanese, target word in gold
│  tashikani mukashi sonna …   │  romaji
└──────────────────────────────┘
```

**The cut is the line, not the scene.** `Segment.startTimeMs` describes one
line, and the surrounding dialogue is fetched separately — that context is
*still* fetched and still used as alignment anchors, but it is no longer
shown. Cutting the whole scene meant several seconds of video playing after
its caption had gone, so the word was over before the clip was. The cut is now
the line plus `REEL_CUT_PAD_MS` on each side (`REEL_CUT_MODE=scene` restores the
old behaviour).

**The reel is filled by adding clips, not by lengthening them.** A tight cut is
only a few seconds, so the planner keeps taking the next-best candidate until
it has ~`REEL_TARGET_MS` of material, capped by `REEL_MAX_SEGMENTS`:

```env
REEL_CUT_MODE=line
REEL_CUT_PAD_MS=220
REEL_TARGET_MS=20000
REEL_MAX_SEGMENTS=12
```

The selection decides this from the search results alone — a candidate's clip
length is its line duration plus padding and roll — so no alignment is spent
working out how many clips are needed. `TARGET_OVERSHOOT` (1.2) lines up a
fifth again more than the target, because a clip or two usually fails to align
and the finished reel should still clear 20 s.

**Furigana is placed by hand, because libass has no ruby.** The API's tokens
carry per-token readings (`r`), so each kanji run is annotated with its own
reading, positioned over its own characters. Long lines wrap first, and a token
straddling the wrap point is dropped rather than annotated over the wrong
characters. The romaji line underneath is rebuilt from those same readings, one
token at a time: converting the joined readings would run the words together
(`タシカニ` → `tashikani` instead of `tashika ni`).

**Those positions are only as good as the font metrics behind them, so the
metrics are measured rather than assumed.** A CJK glyph is conventionally one em
wide, and the first version of this assumed exactly that — which put every
reading progressively further left of the character it annotated, because
`Hiragino Sans` renders CJK at **0.798 em**, `YuGothic` at 0.625 and
`Hiragino Mincho ProN` at 0.854, and an ASCII space is ~0.26 em rather than the
half-em a naive model guesses. `measure_metrics()` renders a small calibration
frame through the same `ass` filter the reel uses, one sample per row — two
lengths of the same character, so subtracting one ink width from the other
isolates the advance without needing the glyph's side bearings, with spaces
sandwiched between kanji since a run of spaces has no ink to measure — and
derives the per-character widths from the pixels. It is cached per (font, size),
costs one small render per build, and falls back to sane defaults if ffmpeg or
numpy is unavailable.

Verified against a rendered frame: for `約束` the reading `やくそく` lands at
x 420–491 against the highlighted word at 420–496, a 2.5 px offset — the
residue of the two glyph sets having different side bearings. A word ending in
a small kana sits differently by design: `待って`'s `まって` is centred on the
three character *cells*, and since `っ` has narrow ink in a full-width cell, the
reading appears ~20 px right of the ink centre while still being correctly
placed over the word.

Two calibration traps worth knowing if this is ever touched again: the `ass`
filter leaves a **1 px dark seam down the right edge** of the frame, which reads
as ink and inflated every measurement until it was excluded; and sample lines
must be on separate rows, or the longer one simply overprints the shorter and
the difference comes out as zero.

Wrapping also breaks between tokens where it can (`break_points`), so a compound
is not split across two rows — a split token loses its furigana, because a
reading is only drawn for tokens wholly inside a row.

#### Making more reels from the same download

A download is the expensive part, so an episode should pay for more than one
reel. `reel mine` reads all the dialogue in episodes you already have and lists
every graded word in them:

```bash
uv run reel mine ZEx1_WvHw0HN:15,Y9vPllKKaA77:13,6Qtve3Rr95G1:7 --level N5,N4
```

```
┌────────┬───────┬──────────┬──────────┬─────────────────────────────┬──────┐
│ word   │ level │ reading  │ romaji   │ meaning                     │ said │
│ 言う   │ N5    │ いう     │ Iu       │ to say, to utter, to declare│   24 │
│ 思う   │ N4    │ おもう   │ Omou     │ to think, to consider       │   21 │
│ 会う   │ N5    │ あう     │ Au       │ to meet, to encounter       │   13 │
│ 待つ   │ N5    │ まつ     │ Matsu    │ to wait                     │   15 │
└────────┴───────┴──────────┴──────────┴─────────────────────────────┴──────┘

[next] build any of these from the same files:
  uv run reel build <word> --only ZEx1_WvHw0HN:15,… --per-media 3
```

That is the inverse of `reel build`: `build` asks "which episodes say this
word?", `mine` asks "which words does this episode say?". The API only offers
the first, so it is replayed per episode behind a media filter.

Two things make the list usable rather than a wall of particles:

* **Part of speech.** The tokeniser labels every token, and only nouns, verbs,
  adjectives and adverbs are kept (`CONTENT_POS`). Without that filter the top
  three "words" in any episode are `こと`, `そう` and `さん`.
* **Dictionary forms.** Tokens carry `d`, so `よかった` is counted and shown as
  `よい` rather than as whatever inflection happened to be spoken.

Vocabulary data is split by what each source is good at. **Levels and readings
are bundled** (`reelmachine/data/jlpt_vocab.json`, 8,430 entries) so the card
works offline and instantly; the data is the tanos.co.uk JLPT lists (CC-BY
Jonathan Waller) via [Bluskyo/JLPT_Vocabulary](https://github.com/Bluskyo/JLPT_Vocabulary).
**Meanings are looked up on demand** from jisho.org and cached in
`.work/cache/dictionary.json` — no Japanese-English dictionary is small enough
to be worth shipping, and a reel needs a handful of words. Lookups are tiered
(exact headword → kana headword → reading match, common entry first), because a
kana query otherwise returns homophones: `もう` came back as `猛`
("greatly energetic") and `ある` as nothing at all. `REEL_DICTIONARY_OFFLINE=true`
skips the lookup entirely; the card then loses its meaning but still renders.

Romaji is generated, not looked up: it is a pure function of the kana reading.

### J-Drama, and reels that mix it with anime

Nadeshiko indexes **J-Drama alongside anime**, and `filters.category` is a real
filter rather than a default — asking for `["ANIME"]` genuinely excludes drama.
Both are searched by default:

```bash
uv run reel build 約束 --category jdrama            # drama only
uv run reel build 約束 --category anime,jdrama --per-category 2   # a real mix
uv run reel mine <id>:<ep> --category jdrama        # mine a drama episode
```

`--category` accepts `anime`, `jdrama` (or `drama`) and `youtube`, and
`REEL_CATEGORY` sets the default.

**A mix needs `--per-category`.** The two corpora are wildly lopsided: for one
word measured here, the top 50 hits were **47 anime to 3 J-Drama**. Ranking
alone therefore produces an anime reel that merely happens to contain a drama
line, so `--per-category N` caps how many clips any one corpus may contribute
while `--per-media` keeps doing the same per title. `reel plan` prints the
resulting mix:

```
hits: 150  usable: 150  selected: 4  aligned: 4
corpus: anime 2  jdrama 2
```

Drama needs no separate download path — J-Drama on TMDB is ordinary `tv`, and
the vidvault provider keys on TMDB ids, so it resolves drama episodes exactly
as it does anime. It is also *easier* to bridge than anime: J-Drama titles
usually carry `externalIds.tmdb`, the direct-id path that only about 1 in 20
anime has. Measured on six drama titles: four resolved to a downloadable
Japanese stream straight from the TMDB id, one matched by title search, one had
no streams.

Verified end to end: `The Makanai: Cooking for the Maiko House`, `I Cannot
Reach You` and `The Naked Director` all resolved to 720p Japanese streams.

### Or a local library

```env
REEL_SOURCE=local
REEL_LOCAL_ROOT=/Volumes/Media/Anime
REEL_LOCAL_TEMPLATES={nameEn}/Season {season:02d}/{nameEn} - {ep2}.mkv,{nameEn}/{nameEn} - {ep2}.mkv
```

Falls back to fuzzy directory matching (difflib over the show name) plus episode
number patterns if the templates miss.

---

## Commands

Every command is run as `uv run reel <command>`:

| command | purpose | API cost |
|---|---|---|
| `reel doctor [--selftest]` | check ffmpeg, config, key, quota | 0 |
| `reel search <word>` | browse segments with timestamps | 1/page |
| `reel words <w1> <w2> …` | which words exist (max 100 per call) | 1 |
| `reel titles <name>` | find `mediaPublicId` by title | 1 |
| `reel probe --title … --ep N` | resolve the source, list renditions, never downloads | 1–2 |
| `reel plan <word>` | search + select + align → `plan.json` | 1/page |
| `reel build <word>` | plan + cut + render | 1/page |
| `reel align --media … --ep N` | debug one episode's drift | 2–3 |
| `reel quota` / `reel cache [--clear]` | usage and cache | 0 |

Useful flags on `build`: `--count`, `--per-media`, `--rating SAFE`,
`--aspect vertical|square|original`, `--pre 350`, `--post 450`,
`--watermark "@yourchannel"`, `--name`.

---

## API notes worth knowing

* **Auth** — `Authorization: Bearer <api_key>`. Rate limit **150 req/min**,
  monthly quota **5,000 req** (the client has a sliding-window limiter and a
  persisted `quota.json` ledger).
* **`POST /v1/search`** is the workhorse. `take` maxes at **50**, so a client
  must cursor-walk; `sort.mode` accepts `RELEVANCE | ASC | DESC | TIME_ASC |
  TIME_DESC | RANDOM` (+`seed` for reproducible random picks).
* **`query.search`** understands boolean operators, wildcards and quoted
  phrases; `exactMatch: true` disables fuzzy matching. Input type changes the
  strategy: kanji searches surface/dictionary/normalised forms *without* the
  reading field (so homophones don't pollute results), kana also matches the
  reading, and romaji/English/Spanish boosts the translations.
* **`filters.languages`** — omit for "Japanese + all translations", `[]` for
  Japanese only, `["EN"]` for Japanese + English.
* **`filters.segmentDurationMs` / `segmentLengthChars`** are how you keep
  one-word grunts out of a reel. This tool filters again client-side.
* **`include: ["media"]`** returns a `media` map keyed by `mediaPublicId` — that
  is where `externalIds` (and therefore your library join key) lives.
* **`GET /v1/media/segments/{id}/context?take=N`** returns the dialogue around a
  line, which is what you want when a single line is too short to stand alone.
* **Reference clips are public and exactly sized.** `urls.audioUrl` serves from
  `cdn.nadeshiko.co/media/<anilistId>/<episode>/<hash>.mp3` with no
  authentication, and the clip's real duration matches
  `endTimeMs - startTimeMs` to within a couple of milliseconds (verified against
  live data). That is what makes the alignment measurable: the API hands you an
  exact excerpt of the line, you find it in your own copy.
* **`POST /v1/search/words`** (≤100 words) returns per-media match *counts* —
  cheap triage to decide which words are worth rendering, without pulling
  segments.
* **`401`/`403`** mean a bad key or missing `READ_MEDIA` scope; **`429`**
  distinguishes short-term rate limiting from monthly quota exhaustion and the
  client raises `QuotaExceeded` for the latter.

The typed client lives in `reelmachine/nadeshiko.py`; models mirror the
OpenAPI v2.4.19 schemas in `reelmachine/models.py`.

---

## Layout

```
pyproject.toml     deps + `reel` entry point (uv-managed)
uv.lock            committed lockfile — `uv sync` reproduces the tested env
.python-version    pins 3.13
reelmachine/
  nadeshiko.py     API client: retries, rate limit, quota ledger, disk cache
  models.py        pydantic mirrors of the OpenAPI schemas
  align.py         the interesting part: envelope cross-correlation + drift fit
  ffmpeg.py        probe / decode-to-numpy / cut / one-pass concat+ass render
  subtitles.py     ASS (JA above EN, target word recoloured) + bilingual SRT
  reel.py          build_plan() then render()
  tmdb.py          Nadeshiko title -> TMDB id + season (the download-site bridge)
  sources/         base.py (provider ABC) · local.py · hls.py · vidvault.py · mock.py
  cli.py           typer commands
tests/             32 tests; alignment maths needs no network or ffmpeg
```

### Design decisions

* **Alignment on RMS envelopes, not raw samples** — survives re-encoding,
  bitrate, channel layout and volume differences between releases.
* **A strong peak is not enough; it must be unique.** A line that occurs twice
  correlates equally well at both places, so a match is only accepted on score
  *and* on beating the best sidelobe. When there is no room in the search window
  to measure a sidelobe, the match is reported as `unjudged` rather than
  quietly trusted.
* **The first anchor constrains every later one.** Its offset becomes a
  prediction, later anchors are searched inside a window around it, and the
  affine fit is redone after each acceptance.
* **One ffmpeg pass for the final render** — clips are normalised at cut time,
  then joined, blurred-padded, subtitled and loudness-normalised in a single
  filtergraph. The concat-demuxer + second-encode route is where A/V drift and
  subtitle drift creep in.
* **Clip durations are frame-quantised and A/V-matched at cut time.** Every clip
  is padded (`tpad` clones the last frame, `apad` adds silence) and trimmed to
  exactly the same duration on both streams. Otherwise the concat filter, which
  positions each stream independently, accumulates the per-clip difference into
  real drift by the end of the reel.
* **Refuse rather than guess** — an episode whose anchors disagree is marked
  `unaligned` and skipped, and `build` exits non-zero if nothing aligned.
* **Plan/render split** — quota is spent once; look-and-feel is free to iterate.

### Gotchas already handled

These cost real debugging time; they are encoded in the code and guarded by the
selftest so they cannot silently come back.

| symptom | cause | where |
|---|---|---|
| Subtitles drift later through the reel | concat binds an audio pad to the wrong stream when inputs are referenced by a bare label | `ffmpeg.build_reel_video` routes every audio input through a labelled `aformat`/`asetpts` filter |
| Reel is ~13% shorter than the clips | a bare label list between `;` separators is parsed as an empty filter name | labels are attached to the `concat` filter that consumes them |
| `No such filter: ''` | as above | as above |
| Every clip ~80 ms short *only on a real host* | HLS inputs start at a non-zero PTS (`start: 0.083401`) and an output `-t` is measured against it | `cut_clip` rebases with `setpts`/`asetpts=PTS-STARTPTS` |
| Every clip one frame too long | `-t` keeps the frame that *starts* before the limit | `cut_clip` also bounds video with `-frames:v N` |
| `is not in allowed_segment_extensions` | CDNs disguise fMP4 as `.jpg` | `ffmpeg.hls_input_args` widens the whitelist and disables `extension_picky` |
| Audio a few tens of ms short per clip | AAC flush adds frames past the output `-t` | `cut_clip` pads and cuts both streams to one duration |
| A repeated line pulls a cut into the wrong scene | score alone cannot distinguish two identical matches | `align.locate` sidelobe margin + windowed re-search |
| `ffprobe: No such file or directory` | standalone static ffmpeg builds | `ffmpeg.probe` falls back to parsing `ffmpeg -i` |
| `REEL_ASPECT` is a whole sentence | an inline `# comment` swallowed into the value | `config.parse_env_value` strips unquoted inline comments |

The first three of those were only reproducible against a real streaming host;
the local fixtures had `start=0`, exact segment names and local seeking. That is
the argument for testing the seams against a real source early.

### Known residual

With AAC, each clip's audio comes out ~1 encoder frame (≈19 ms) longer than its
video, and no filter combination removes it — `atrim`, `-shortest` and
trimming all land on the same number. Measured on a 3-clip reel: total drift vs
the cue timeline **+70 ms**, video-to-audio mismatch **10 ms**. Both are well
below the ~100 ms where subtitle or lip-sync error becomes visible, and the
selftest fails the build at 200 ms / 250 ms respectively.

---

## Legal

Nadeshiko indexes third-party media; the AGPL-3.0 licence on the API covers the
API, not the anime. This tool downloads and re-encodes video from a server you
control and produces derivative clips — make sure you have the rights to
whatever you publish. Check the source site's terms before automating against
it.
