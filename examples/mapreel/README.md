# mapreel examples

Two scripts for the `mapreel` recipe. See [docs/MAPREEL.md](../../docs/MAPREEL.md) for the
model and the agent workflow.

- `food.json`: "Foods you'd never eat". A ranked countdown with a versus, a country and a
  regions segment. Five items show real Wikimedia Commons photos: Marmite, takoyaki,
  lutefisk, natto and casu martzu. Their licences go to `sources.md`.
- `brands.json`: five countries and their brands, as text badges.

Both files keep `"voice": "fake:default"` and `food.json` keeps `"mode": "storyboard"`,
so probes and CI never call a voice. Pick the voice on the command line instead. Later
`-i` values win. Always set `REEL_TTS` on the command line too, because it overrides
whatever `.env` sets.

## Fake voice (free; only the map data and images come from the network)

The stills and `sources.md` only, in under a minute:

```bash
REEL_TTS=fake REEL_HYPERFRAMES_WORKERS=1 uv run reel run mapreel -i @examples/mapreel/food.json
```

The video with one tone per word instead of a voice, to check timing and layout:

```bash
REEL_TTS=fake REEL_HYPERFRAMES_WORKERS=1 uv run reel run mapreel \
  -i @examples/mapreel/food.json -i '{"mode": "build"}'
```

## macOS voice (free, local)

A real voice from macOS `say`. Use this to watch a finished reel on a Mac:

```bash
REEL_TTS=macos REEL_HYPERFRAMES_WORKERS=1 uv run reel run mapreel \
  -i @examples/mapreel/food.json -i '{"mode": "build", "voice": "macos:Samantha"}'
```

`say -v '?'` lists the other voices.

## Cartesia voice (production, billed per character)

Needs `CARTESIA_API_KEY`. `<voice-id>` is a voice's id from the Cartesia voice library:

```bash
REEL_TTS=cartesia REEL_HYPERFRAMES_WORKERS=1 uv run reel run mapreel \
  -i @examples/mapreel/food.json -i '{"mode": "build", "voice": "cartesia:<voice-id>"}'
```

Run `probe` first: `details.cost.ttsCharacters` is what the voice will bill.

Each build writes `reel.mp4`, `storyboard.jpg` and `sources.md` under `out/`
(`REEL_OUTDIR`). The first build downloads the map data and the Commons images into
the shared cache; later builds reuse them. Paste `sources.md` into the video
description: it credits every fact's source and every image's author and licence.
