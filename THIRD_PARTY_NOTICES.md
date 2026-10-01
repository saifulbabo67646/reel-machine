# Third-party notices

reel-machine ships one vendored runtime and one set of neutral default style packs.
Anything a deployment adds — a style pack carrying third-party visual IP, a voice, a font
— carries its own licence, and the manifest records the provenance and licence of every
asset a job produced.

## Vendored: srt-whiteboard-animation

Used by `doodle`'s stroke mode through
`reelmachine/recipes/doodle/scenes/stroke.py`.

- Project: https://github.com/geeklee/srt-whiteboard-animation
- Vendored commit: `696a7243c0e6ffb6827676e539c2ca5ebae2bf6b`
- Licence: MIT (verbatim copy at
  `reelmachine/vendor/srt-whiteboard-animation/LICENSE`; provenance in that directory's
  `UPSTREAM.md`)
- Vendored files: `scripts/*.py` and `assets/drawing-hand.png`, unmodified.

```
MIT License

Copyright (c) 2025 geeklee

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bundled style packs

`reelmachine/data/styles/*.json` are this project's own neutral defaults, released under
CC0-1.0. They contain no third-party visual IP.

Third-party visual IP must never be hard-coded in core. A style pack may add it — a
character style, a palette, a hand — but only as a pack that declares its licence and
provenance, which then flows into the manifest:

```json
{
  "id": "example.character-style",
  "licence": {"name": "CC-BY-4.0", "url": "...", "attribution": "..."},
  "provenance": {"provider": "example", "source": "..."}
}
```

## Fonts

No fonts are bundled. Style packs reference font names (for example `Amiri Quran` for
Arabic, `Helvetica` for Latin); the deployment installs them under their own licences.
`reel doctor` and the render output will show a fallback if a font is missing.

## Corpus data

- **Nadeshiko** (anime/J-Drama search): the API is AGPL-3.0; the indexed media is not.
  Cuts are made from copies the caller controls.
- **Quran**: the Arabic text is public domain; translations remain the copyright of their
  publishers. The quranic recipe records the reciter and translation in every manifest,
  and the alquran.cloud / Quran Foundation APIs are attributed there.
- **Voices**: cloud narration (ElevenLabs, Cartesia) is billed and licensed per vendor and
  per voice; the chosen voice and provider are recorded in the manifest. The bundled fake
  voice is CC0.

## Runtime dependencies

The default install depends on `httpx`, `numpy`, `pydantic`, `rich`, `scipy` and `typer`.
Optional extras: `mcp` (the MCP server), `stroke` (`opencv-python-headless`, `Pillow`,
`av`), `program` (`Pillow`), `s3` (`boto3`). Each is used under its own licence.
