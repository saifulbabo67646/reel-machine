# explainer3d Plan 2 — Cinematic reels with a length setting

**Goal:** the agent can make, through the explainer3d recipe, reels at the quality of the
"$1 bait for monster fish" spike (`out/spike-monster-fish-bait.mp4`): real CC0 models,
HDRI lighting, reflective water, post-processing, impact effects, an underwater set,
viral captions, sound design — at a target length from 15 to 90 seconds.

**Why this replaces the old Plan 2–3 order:** the user judged plan-1 output "maybe 10%
viral"; the spike proved three.js + HyperFrames reaches "wow" with these techniques. They
come first; Poly Pizza/Meshy/Tripo, blueprints and the JP conversation follow.

**Spec:** `docs/superpowers/specs/2026-10-11-explainer3d-design.md` (plan 1) + this file.
The user asked to build directly ("go ahead and build it into the module with length
setting"), so this document is the design record rather than a gate.

## Decisions

1. **Same architecture.** Shots stay separate HyperFrames projects, cached by content;
   world state still flows shot to shot; Python still owns every move's end state and the
   JS runtime every frame. Cinematic features are additive: plan-1 scripts render as before.
2. **Looks gain a renderer tier.** `look: "cinematic"` turns on HDRI image-based lighting,
   ACES tone mapping, bloom, depth of field, a colour grade, punch-in at each cut, camera
   shake on impacts, white impact flashes, hard cuts, punch captions, a title card, a
   progress bar. Old looks are untouched.
3. **Environments.** `set.environment` adds `lake` (sunset HDRI sky, reflective water at
   y = 0, a textured stone slab, CC0 dressing) and `underwater` (teal fog, caustics on sand,
   light shafts, motes). A shot may switch environment (`shot.set`), e.g. dive underwater.
4. **Models.** A cast asset may be `{"model": "polyhaven:<id>" | "khronos:<name>", "height"
   | "size", "container", "count"}`. Files are fetched once into a shared cache
   (`<cache>/explainer3d/assets`), hard-linked into each shot project, measured in Python
   from the glTF bounds (so anchors, tops and fills stay exact), licence CC0 recorded.
   `find_models` (MCP) searches the catalogue. Shapes still work and gain `count` (a row of
   balls).
5. **Moves.** New cinematic moves, each with a Python end state and a JS animation:
   `leap` (slow-motion jump out of water with splashes), `slam` (drop with squash, dust,
   shake), `burst` (explode into crumbs inside a container), `drizzle` (honey stream and
   coil), `smash` (rise, hover, slam: flash, shockwave, chunks, paste), `pop_out` (a row of
   balls pops out of a container), `throw` (arc into water, splash), `splash`, `sink`
   (underwater, with bubbles), `school` (fish circle a target; the lead lunges and eats it).
   Cameras add `track` (follow an actor) and `dive` (plunge to the water surface).
6. **Narration markup.** `*word*` highlights a caption word; `[shown|spoken]` shows one
   thing and says another (`[$1|one dollar]`). Spoken text goes to TTS; display tokens get
   the spoken words' timings.
7. **Beat shots.** A shot may have no narration and a `duration` (0.8–8 s) — the
   underwater payoff. `card` puts a big end-card line on screen ("SAVE THIS 📌").
8. **Length setting.** `length` (15–90 s) is the target. `probe` estimates the length and
   lints retention: hook in the first second (a big move in shot 1 within 0.5 s), no gap
   between beats over 3 s, a re-hook after 25 s on reels over 40 s, a call to action at the
   end. The build stretches shot tails (≤ 1.6×) to reach the target when narration runs
   short; the verifier checks the result is within max(3 s, 12 %).
9. **Sound.** `music: "auto"` synthesises a 128 bpm bed (seeded, licence-free) that ducks
   under the voice and is low-passed while underwater. SFX are synthesised (splash, boom,
   crunch, gulp, bubbles, whoosh, thud, pop, ding); every cut gets a whoosh. The cinematic
   look masters to −14 LUFS.
10. **Cartesia word timings.** Ask for `add_timestamps`; parse the columnar
    `{words, start, end}` shape (found by the spike). Captions sync for real.

## Tasks

1. Cartesia timestamps (request + parse both shapes) — test first.
2. Schema: optional narration, `duration`, `card`, `shot.set`, `length`, `music: "auto"`,
   ModelSpec, shape `count`; narration markup parser; timing (lead/tail per look, beat
   shots, fit to length) — tests first.
3. Assets: catalogue + fetch (Poly Haven, Khronos) into a shared cache, glTF/GLB bounds,
   dims for models, AssetsStage, `find_models` tool, recipe guide in `describe_recipe`.
4. Moves and cameras in Python (params, refs, end states) + retention lint in probe.
5. Runtime: vendor three addons; cinematic renderer (HDRI, water, post, underwater,
   models, fills: liquid/paste/crumbs/glaze, balls rows, fish clones); new moves' frames and
   fx; track/dive cameras; punch captions, title card, card, progress bar, flash, shake.
   Cross-check Python end states against JS for every move.
6. Audio: synthesised music + SFX, ducking, underwater low-pass, whoosh per cut, loudness.
7. Compose/verify: hard cuts for cinematic, length check, beat-gap check.
8. Demo: rebuild the monster-fish reel through the recipe at `length: 25` and `length: 55`;
   docs (README, MCP.md, THIRD_PARTY_NOTICES for Poly Haven/Khronos CC0).
