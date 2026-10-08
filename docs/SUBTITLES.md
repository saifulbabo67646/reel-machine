# Subtitles (the `storyreel` chain)

`storyreel` needs the film's script: the creative steps read it, and the micro-clip
planner uses its timestamps as the film's clock. `vidvault` downloads the film but ships
no subtitles, so the recipe asks the `reelmachine.subtitles` providers in order
(`REEL_SUBTITLES_ORDER`, default **embedded → subdl → opensubtitles**). A provider whose
`missing()` is non-empty is skipped with a recorded reason; every attempt (skipped,
found nothing, or failed) is carried in the job output, and when nothing works the error
lists them (`PROVIDER_UNAVAILABLE`, `details.attempts`).

## The providers

### `embedded` — the film's own streams (keyless)

Extracts a text subtitle track that is already inside the downloaded film
(`ffmpeg -map 0:s:N -c:s srt`). This is the best source when it exists: no key, no quota,
no download, and its timestamps were authored for **exactly** the file the micro-clips
are cut from — embedded subtitles are reported as `synced: true` because they cannot
drift. Text codecs (`subrip`, `ass`/`ssa`, `webvtt`, `mov_text`, …) convert directly;
bitmap tracks (PGS, VobSub) are skipped, since they would need OCR. Tracks tagged with
the wanted language are preferred, then the default track; a *forced* track — signs and
alien dialogue only — is chosen only when it is all there is.

### `subdl` — TMDB-native, generous free tier

[SubDL](https://subdl.com/developers) is searched by `tmdb_id` (a series episode searches
by the series id + season/episode). Set `REEL_SUBDL_API_KEY` (free account →
<https://subdl.com/panel/api>). Free tier: 2 000 searches + 50 downloads a day.
Download is the API's `?format=file` route, falling back to the zip it serves (the zip
is unpacked and the first `.srt`/`.ass` inside wins).

### `opensubtitles` — the official API

[OpenSubtitles.com](https://opensubtitles.stoplight.io/docs/opensubtitles-api) is the
largest catalogue and the ecosystem's agreed migration target (the legacy
`api.opensubtitles.org` XML-RPC was shut down for third-party apps in early 2026 — do
not build on it). Set `REEL_OPENSUBTITLES_API_KEY` (free account →
<https://www.opensubtitles.com/en/consumers>); adding
`REEL_OPENSUBTITLES_USERNAME`/`REEL_OPENSUBTITLES_PASSWORD` logs in for the registered
quota. Free: 20 downloads a day registered (5 anonymous). Films search by `tmdb_id`;
series episodes by `parent_tmdb_id` + season + episode, which is what the API asks for.
A `406`/`407` is reported as a quota error with the reset hint.

## Timing: the one real pitfall

A downloaded subtitle was timed against *some* release — ours may differ by an intro
logo, a frame rate or an extended cut, and every timestamp in a clip plan would then be
off. The stage handles it in this order:

1. embedded subtitles need nothing (same file by construction);
2. `ffsubsync` — when it is on PATH, the subtitle is aligned to our film's audio track
   (`REEL_SUBTITLES_SYNC=auto`, the default; `off` disables the attempt);
3. `subtitle_offset_ms` — a caller who knows the shift says it outright (applied last);
4. otherwise the output records `synced: false` and verification **warns**, so a drifted
   clip plan is visible rather than mysterious.

Install the aligner with `pipx install 'ffsubsync[auditok]'` (it is never a hard
dependency of the engine).

## Adding a fourth source

Implement `SubtitleProvider` (`reelmachine/recipes/storyreel/subtitles/__init__.py`):
`name`, `missing()`, `describe()`, `fetch(request, *, dest_dir) -> SubtitleFetch | None`.
Return `None` for "no match for this title" (the chain moves on) and raise
`ProviderUnavailable` for "this provider is broken right now" (the chain records it and
moves on). Register it:

```toml
[project.entry-points."reelmachine.subtitles"]
wyzie = "mypack.subtitles.wyzie:WyzieProvider"
```

and list it in `REEL_SUBTITLES_ORDER` if the default order is not what you want. The
stage normalises whatever it writes (zip, ASS, Windows encodings) to UTF-8 SRT.
