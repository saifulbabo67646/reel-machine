# explainer3d Plan 3 — any set, characters, agent-written components, length from the material

**Why:** plan 2 made reels look good, but only on a lake or underwater, with fishing-shaped
moves and no people. Scripts need kitchens, houses, workshops, streets — and humans — and
when the menu has no piece for an idea, the agent (the brain) must be able to build it.
The user also wants length decided by the agent from the material, not padded to a target.

**User decisions (2026-10-11):** free stylised CC0 characters first (AI-generated realistic
characters, e.g. Meshy, later); agent-written code components included, opt-in per
deployment.

## 1. Sets are data, presets are just saved sets

`set` (reel-wide) and `shot.set` (per shot) accept a preset name **or** a set object:

```json
{"preset": "kitchen"}
{"sky": "polyhaven:<hdri>", "background": "sky" | "#rrggbb" | "blur",
 "floor": {"texture": "polyhaven:<texture>", "size": [6, 6]},
 "walls": {"texture": "polyhaven:<texture>", "height": 2.6, "sides": ["back", "left"]},
 "stage": "stone-slab" | "table" | "counter" | "none",
 "water": {"colour": "#0b2a2e"},
 "fog": {"colour": "#cfe6f5", "density": 0.05},
 "props": [{"model": "polyhaven:<id>", "at": [x, y, z] | "<anchor>", "yaw": 30, "size": 0.8}],
 "marks": {"sink": [1.2, 0.9, -0.6]},
 "sun_angle": 80}
```

- Presets ship as JSON in `sets/`: `lake`, `underwater` (plan 2's sets, re-expressed),
  `kitchen`, `living-room`, `workshop`, `studio`, `meadow-day`. A preset may be extended:
  `{"preset": "kitchen", "props": [...]}` adds to it.
- `sky` is a Poly Haven HDRI: about 700 real places, indoor and outdoor; it lights the scene
  and, unless `background` says otherwise, is the backdrop.
- `marks` are named spots the script can use like anchors (`"to": "sink"`).
- `find_assets(query, kind)` searches models, skies and textures (`find_models` stays, as
  `kind: "model"`).
- The cinematic look (post, captions, punch-in, shake, sound) applies to every set.

## 2. Characters (stylised CC0 humans that act)

- Cast asset `{"character": "quaternius:<name>" | "mannequin", "height": 1.75}`.
  Characters come from Quaternius *Universal Base Characters*, animations from
  *Universal Animation Library* (both CC0, one 65-joint rig). Fetched once through
  itch.io's free-download flow into the asset cache; `reel explainer3d import <zip>` is the
  fallback when that flow is unavailable.
- Moves: `walk_to` (to a mark/anchor/point; `gait`: walk | jog | sprint; foot speed follows
  distance), `turn_to`, `act` (any clip by friendly name: idle, talk, wave-as-interact,
  pick_up, push, fix, crouch, dance, jump, punch, sit, sit_talk, stand_up, swim…),
  `pick_up` / `put_down` (a prop rides the right hand between them), `sit`, `talk`
  (talking idle while the line plays).
- Determinism: animations are evaluated with `AnimationMixer.setTime` from the action's
  local time; crossfades between consecutive clips are closed-form (0.25 s).
- Python owns end states: position, facing, pose (standing/sitting), what is held.

## 3. Components the agent builds

**Compound assets (always on, data only).** A cast asset may be a list of primitive parts —
box, cylinder, sphere, cone, torus, capsule, plane — each with size, position, rotation,
material and colour. A fishing rod, a dishwasher, a sign: built from pieces, moved by the
normal moves.

**Code components (opt-in: `REEL_EXPLAINER3D_CUSTOM_CODE=1`).** The agent writes ES-module
source in `components`:

```json
"components": {"gauge": {"code": "export function create({ THREE, params, rng }) { ...; return { object, update(t) {...} }; }"}}
```

used as a cast asset (`{"component": "gauge", "params": {...}}` — an actor the normal moves
can carry) or as a shot effect (`"effects": [{"component": "laser", "at": [0.2, 0.8]}]`).

Rules, enforced: the module gets `THREE`, its `params`, a seeded `rng`, the shot time `t`
and read-only scene info; it must be a pure function of `t` (no `Date`, `performance`,
`Math.random`, timers, `fetch`, sockets, storage, `eval`/`Function`, dynamic `import`);
≤ 64 KB. Checked three ways: a static lint in `probe` (names the component and the
forbidden call), `node --check` for syntax, and at build a determinism check (one frame
rendered twice, out of order, must match). Shot pages carry a Content-Security-Policy that
blocks every other origin. A component that throws fails its shot with the component's
name and message. Off by default because it runs caller code on the deployment's machine.

## 4. Length comes from the material

`probe` counts the script's big moments and reports `details.beats` and
`details.suggestedLength` (about 2.7 s per big moment, never under the narration). When a
`length` asks for much more than the material supports, it warns: "9 big moments support
about 25 s; padding to 55 s will feel slow — add moments or shorten". The guide tells the
agent to choose the length from the story, and `length` stays optional.

## Order of work

1. Length advice in probe + guide.
2. Sets as data, presets, `find_assets`, rooms/floors/walls/props/marks.
3. Compound assets.
4. Characters: fetch + measure, cast, moves, runtime mixer, cross-checked end states.
5. Code components: schema, lint, CSP, loader, determinism check, errors.
6. Demo: an indoor reel with a character and an agent-written component; docs and guide.

Every task test-first; Python end states cross-checked against the JS runtime; renders
checked by storyboard and by eye.
