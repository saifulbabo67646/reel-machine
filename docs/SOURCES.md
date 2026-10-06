# Source providers (nadeshiko-cut)

Everything here was measured against real hosts; it is preserved because it is the
expensive part of the original implementation and it is still exactly how the providers
behave. Behaviour is unchanged by the refactor — `vidvault` in particular moved behind
the provider interface without a single behavioural edit.

## Connecting your streaming server (`hls`)

Streaming hosts rarely serve a stable, guessable URL:

```
https://fetch.example.top/anime/<animeHash>/<episodeHash>/master.m3u8?token=<signed>
```

A plain URL template cannot express that, so the provider composes the URL from three
pieces:

| piece | setting | why |
|---|---|---|
| path shape | `REEL_HLS_TEMPLATE` | placeholders for the parts that vary |
| opaque ids | `REEL_HLS_MAP` | hashes cannot be derived — record them once per title |
| fresh token | `REEL_HLS_RESOLVER_CMD` *or* `REEL_HLS_TOKEN_CMD` | the signature expires |

Both resolver commands run as subprocesses and receive the episode context as `REEL_*`
environment variables. The **last** non-empty stdout line is used, so a script may log
before printing the value.

### Hosts that disguise their segments

A real host served a media playlist (no renditions), credentials in the path, and every
fMP4/CMAF segment named `seg.jpg`. ffmpeg's HLS demuxer rejects those outright:

```
URL https://…/seg.jpg is not in allowed_segment_extensions
```

`reelmachine/ffmpeg.py` handles it for any `.m3u8` URL by widening the extension
whitelist and switching off `extension_picky`; it probes the binary first, because
`extension_picky` only exists in ffmpeg 6.0+ and an unknown private option is a hard
error. `REEL_HLS_EXTENSION_PICKY=true` restores ffmpeg's strict default.

`Referer`-enforcing hosts and open ones are both fine: headers are sent on the playlist
fetch **and** handed to ffmpeg, which needs them again for every segment request.

## vidvault: download first, then cut

```env
REEL_SOURCE=vidvault
REEL_TMDB_API_KEY=<free key from themoviedb.org/settings/api>
REEL_VIDVAULT_QUALITY=720
REEL_VIDVAULT_CONNECTIONS=8
```

The chain, reproduced from the site's own frontend (no account needed):

```
Nadeshiko media ──▶ TMDB id ──▶ GET  /api/get-token      (~60 s lifetime)
                                 POST /api/download-proxy (x-request-token)
                                 ◀── renditions (mp4Data.links[], mkv*, mkvV2Data, mkvV3Data)
                                 ──▶ download the signed CDN url ──▶ local file
```

There is no browser-side step to reproduce, no captcha and no Cloudflare interstitial; a
headless browser would issue byte-identical requests. What actually breaks this source:

**1. The payload gets reshaped.** Renditions moved from
`mp4Data.downloadInfo.data.downloads[]` (rich: integer `size`, `duration`, `vipLocked`) to
`mp4Data.links[]` (lean: `url`, `quality: "720p"`, `size: "289 MB"`) with siblings
`mkvData` / `mkvV2Data` / `mkvV3Data`. `parse_streams()` reads every generation and
`coerce_size()` accepts bytes, a byte string, or a human label.

**2. The CDN throttles per connection.** One connection sustains ~0.17–0.25 MB/s while
aggregate throughput scales with concurrency (2 → 0.52, 4 → 1.04, 8 → 1.78 MB/s), and
long single-stream transfers are cut off part-way. Episodes are fetched as 4 MB byte
ranges across `REEL_VIDVAULT_CONNECTIONS` workers, each range retried independently and
banked in a `.part.json` checkpoint.

**Bridging the ids is the hard part.** Nadeshiko identifies media by AniList id; vidvault
keys on TMDB; `externalIds.tmdb` is populated for about 1 in 20 titles. So the bridge is a
title search, with two corrections: **season folding** (`Rent-a-Girlfriend Season 3` →
show 92683 season 3, as are `& Beyond`, `Part 2`, `Cour 2`) and **numbering drift**
(cross-checked by duration and, finally, by the audio alignment, which simply refuses).
Measured on 19 live titles: **18 resolved** to a confident TMDB match.

### Things this source will do to you

| behaviour | why it matters |
|---|---|
| the request token lives ~60 s | it is minted immediately before each lookup, never cached across episodes |
| `download-proxy` **caches its responses** | a reply can carry CDN links minted hours earlier; age is treated as a preference, not a veto |
| link age is only knowable on **old** links | the newer URLs are opaque path tokens with no timestamp, so age cannot veto them |
| the payload **changes shape without notice** | if `reel probe` reports `0 entries` for a title that certainly exists, suspect the parser before the network |
| the CDN 429s non-browser clients | the site's `User-Agent`/`Referer` are sent on every request *and* to ffmpeg; dropping `Referer` is a hard 404 |
| the CDN throttles **each connection** | a single-stream fetch of one episode takes ~8 minutes and often truncates |
| rendition labels are unreliable | a "480p" link served 720×408 and "1080p" came back smaller than "720p" |
| `mp4Data` links are actually **Matroska** | ffmpeg demuxes by content, so cuts are unaffected |
| dubs exist, and an explicit **Japanese tag wins** | `pick_stream` hard-prefers a Japanese-tagged rendition over a higher-resolution untagged one |
| but the tag is **not the whole story** | `pick_japanese_track` still picks the Japanese track out of the file, and a file with none is refused rather than cut |
| the two CDN workers differ wildly in speed | tagged Japanese renditions come from `mk2` (0.03–0.39 MB/s), untagged from `tdm` (0.4–1.6 MB/s, 6–17 MB/s range-parallel) |
| **renditions are tried in order, not chosen once** | `rank_streams` orders candidates by quality ladder, then preferred host, then Japanese tag, and `resolve` walks that list |
| a stalled transfer must fail, not hang | a socket timeout for silence plus a 48 KiB/s throughput floor for a trickling link |
| a mis-picked season wastes a download | run `reel probe --title "…" --ep N` first |

## Local library

```env
REEL_SOURCE=local
REEL_LOCAL_ROOT=/Volumes/Media/Anime
REEL_LOCAL_TEMPLATES={nameEn}/Season {season:02d}/{nameEn} - {ep2}.mkv,{nameEn}/{nameEn} - {ep2}.mkv
```

Falls back to fuzzy directory matching (difflib over the show name) plus episode-number
patterns if the templates miss.
