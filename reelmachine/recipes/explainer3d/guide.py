"""What describe_recipe tells an agent about authoring explainer3d reels."""

from __future__ import annotations

from typing import Any

from .camera import CAMERA_MOVES
from .characters import CLIPS
from .moves import MOVES


def _params(model: type) -> dict[str, Any]:
    schema = model.model_json_schema()
    out = {}
    for name, prop in schema.get("properties", {}).items():
        entry = {k: prop[k] for k in ("default", "enum", "minimum", "maximum", "type") if k in prop}
        if "anyOf" in prop:
            entry["type"] = " | ".join(p.get("type", "?") for p in prop["anyOf"])
        out[name] = entry
    return out


def build_guide() -> dict[str, Any]:
    return {
        "workflow": [
            "1. Pick a topic with a payoff people want (a secret, a result, a transformation).",
            "2. find_models for the cast; use look 'cinematic' and a set environment ('lake', or 'studio').",
            "3. Write shots: open on the payoff (shot 1, a big move at 0 s), then the steps, a re-hook past "
            "20 s on long reels, the payoff again, and a call to action ('Save this…').",
            "4. probe: fix every warning (hook, beat gaps, length); then start_job with mode 'storyboard', "
            "look at storyboard.jpg, then mode 'build'.",
        ],
        "length": ("Decide the length from the material, not the other way round: each big moment holds a "
                   "viewer ~2.7 s, so count yours — probe reports bigMoments and suggestedLength and warns "
                   "when a target means padding. 9-10 big moments ≈ 25 s; a minute needs ~20 genuinely new "
                   "ones. `length` (15-90 s) is optional; when given, the build stretches shot tails up to "
                   "1.6x to land on it. Shorter usually finishes better."),
        "narration": ("Markup: *word* highlights a caption word; [shown|spoken] shows one thing while the "
                      "voice says another — [$1|one dollar], [WATCH 👀|watch]. A shot with no narration "
                      "needs a duration (0.8-8 s): a beat for the visuals alone. `card` puts a big line on "
                      "screen (\"SAVE THIS 📌\"). Keep lines short: 2-7 words land hardest."),
        "looks": {"cinematic": "HDRI light, bloom, depth of field, colour grade, punch-in cuts, shake on "
                               "impacts, flash, punch captions, title card, progress bar — the viral look",
                  "diorama-day": "bright toy diorama", "studio-clean": "plain studio"},
        "sets": ("`set` (and per shot `shot.set`) is a preset or a set built from parts. Presets: lake, "
                 "underwater, kitchen, living-room, workshop, studio, cafe, meadow-day. Parts: sky "
                 "(polyhaven:<hdri> — a 360° photo of a real place that lights the set; find_assets kind=sky), "
                 "background ('sky', 'blur' or #rrggbb), floor/walls ({texture | colour, size, repeat, sides}), "
                 "stage (stone-slab | counter | table | none, stage_texture), water, fog, props "
                 "([{model, at: [x,y,z] | anchor, yaw°, size}]) and marks ({\"sink\": [x,y,z]} — named spots "
                 "usable as anchors). Fields next to a preset change it; a shot.set WITHOUT a preset changes "
                 "the reel's set, WITH one replaces it. Lake anchors: lake.near/far/left/right; underwater: "
                 "underwater.surface/center/floor."),
        "anchors": ["stage.center", "stage.near_left", "stage.near_right", "stage.front", "stage.back",
                    "stage.left", "stage.right", "ground", "sky",
                    "lake.near", "lake.far", "lake.left", "lake.right",
                    "underwater.surface", "underwater.center", "underwater.floor"],
        "models": ("Cast members may be shapes or CC0 models: {\"model\": \"polyhaven:<id>\" | "
                   "\"khronos:BarramundiFish\", \"size\": metres (longest side) or \"height\", "
                   "\"container\": true for bowls, \"count\": n for a school or a row}. Search with find_models."),
        "characters": ("People: a cast member {\"character\": \"quaternius:female\" | \"quaternius:male\" | "
                       "\"mannequin\", \"height\": 1.7, \"outfit\": {\"top\", \"bottom\", \"shoes\": #rrggbb, "
                       "\"sleeves\": long|short, \"skin\": #rrggbb tint}, \"hair\": long|buns|parted|buzzed|"
                       "buzzed_female|none, \"hair_colour\", \"beard\": bool}. Stylised CC0 bodies with 43 animations. "
                       "Moves: walk_to (gait walk|jog|sprint), turn_to, act (clip), talk, sit (on a chair prop's mark), "
                       "stand_up, pick_up (item rides in the right hand), put_down. Indoor presets have marks front, "
                       "left, right, door on the floor. People are ~1.7 m: frame them from 2.5-3.5 m (orbit radius, "
                       f"track offset z). Clips for act: {sorted(CLIPS)}."),
        "components": ("When nothing on the menu fits, build it. Without code: a cast asset {\"parts\": [{shape: box|"
                       "cylinder|sphere|cone|torus|capsule|plane, size: [x,y,z], at, rotate (deg), material, colour, "
                       "glow}]}. With code (only if the deployment sets REEL_EXPLAINER3D_CUSTOM_CODE): "
                       "\"components\": {name: {code}} where code is `export function create({ THREE, params, rng, "
                       "size }) { …; return { object, update(t, info) { … } }; }` — build once in create, move only in "
                       "update, and make every frame a function of t alone (use the given rng, never Math.random, "
                       "Date, timers, fetch, imports, globals). Use it as a cast asset {\"component\": name, \"size\": "
                       "[x,y,z], \"params\"} or a shot effect {\"component\", \"at\": [s, e], \"params\"}. probe lints "
                       "it and dry-runs it (seven frames, drawn twice in different orders) and names the line that broke."),
        "moves": {name: {"category": m.category, "description": m.description, "actor": m.actor,
                         "params": _params(m.params), "example": m.example}
                  for name, m in MOVES.items()},
        "cameras": {name: {"description": c.description, "params": _params(c.params), "example": c.example}
                    for name, c in CAMERA_MOVES.items()},
        "music": "\"auto\" synthesises an upbeat bed that ducks under the voice and muffles underwater.",
    }
