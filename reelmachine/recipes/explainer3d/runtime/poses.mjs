// Object and process moves: (world, action, ctx, p) -> { world, fx }.
// The end state at p = 1 must equal moves.py exactly; tests/test_explainer3d.py checks it.
import { add3, clamp01, easeInCubic, easeInOutCubic, easeOutBack, easeOutCubic, fillY, lerp3, mixHex, spring } from "./math.mjs";

export const OFFSETS = { sky: [0, 3, 0], left: [-2.5, 0.5, 0], right: [2.5, 0.5, 0], front: [0, 0.5, 2.5] };

const point = (ctx, w, ref) => (ref in ctx.anchors ? [...ctx.anchors[ref]] : [...w[ref].position]);
function top(ctx, w, ref) {
  if (ref in ctx.anchors) return [...ctx.anchors[ref]];
  const a = w[ref];
  return [a.position[0], a.position[1] + ctx.dims[ref][1] * a.scale, a.position[2]];
}
function interior(ctx, w, ref, level) {
  const a = w[ref];
  return [a.position[0], a.position[1] + fillY(ctx.dims[ref][1], level), a.position[2]];
}
const setActor = (w, id, patch) => ({ ...w, [id]: { ...w[id], ...patch } });
const setFill = (w, id, patch) => setActor(w, id, { fill: { ...w[id].fill, ...patch } });
function pourFill(w, id, add, colour, q) {
  const f = w[id].fill, level0 = f.level;
  const level = Math.min(1, level0 + add * q);
  const mixed = level0 <= 0 ? colour : mixHex(f.colour, colour, (add / (level0 + add)) * q);
  return setFill(w, id, { level, colour: q > 0 ? mixed : f.colour });
}
// a parabolic hop from a to b, lifted at the midpoint
const hop = (a, b, t, lift) => {
  const p = lerp3(a, b, t);
  p[1] += Math.sin(Math.PI * t) * lift;
  return p;
};

const MOVES = {
  appear(w, a, ctx, p) {
    return { world: setActor(w, a.actor, { visible: true, scale: p >= 1 ? 1 : easeOutCubic(p) }), fx: [] };
  },
  scale_pop(w, a, ctx, p) {
    return { world: setActor(w, a.actor, { visible: true, scale: p >= 1 ? 1 : easeOutBack(p) }), fx: [] };
  },
  fly_in(w, a, ctx, p) {
    const ref = a.params.to;
    const to = ref == null ? [...ctx.homes[a.actor]] : ref in ctx.anchors ? point(ctx, w, ref) : top(ctx, w, ref);
    const from = add3(to, OFFSETS[a.params.from]);
    return { world: setActor(w, a.actor, { visible: true, position: p >= 1 ? to : lerp3(from, to, spring(p)) }), fx: [] };
  },
  drop_into(w, a, ctx, p) {
    const into = a.params.into, start = w[a.actor].position;
    const level0 = w[into].fill.level;
    const above = add3(top(ctx, w, into), [0, 0.45, 0]);
    const inside = interior(ctx, w, into, level0);
    let position, scale = 1, visible = true;
    if (p < 0.55) position = hop(start, above, easeInOutCubic(p / 0.55), 0.3);
    else if (p < 0.8) position = lerp3(above, inside, easeInCubic((p - 0.55) / 0.25));
    else {
      const q = (p - 0.8) / 0.2;
      position = inside; scale = 1 - q; visible = q < 1;
    }
    if (p >= 1) { position = inside; scale = 0; visible = false; }
    const colour = a.params.colour || ctx.colours[a.actor];
    let world = pourFill(w, into, a.params.fill_add, colour, clamp01((p - 0.75) / 0.25));
    world = setActor(world, a.actor, { position, scale, visible });
    return { world, fx: [] };
  },
  place_on(w, a, ctx, p) {
    const to = top(ctx, w, a.params.on);
    const position = p >= 1 ? to : hop(w[a.actor].position, to, spring(p, 7), 0.3);
    return { world: setActor(w, a.actor, { visible: true, position }), fx: [] };
  },
  spin_showcase(w, a, ctx, p) {
    const r0 = w[a.actor].rotationY, position = [...w[a.actor].position];
    if (p < 1) position[1] += Math.sin(Math.PI * p) * 0.08;
    const turned = r0 + a.params.turns * 2 * Math.PI * (p >= 1 ? 1 : easeInOutCubic(p));
    return { world: setActor(w, a.actor, { rotationY: turned, position }), fx: [] };
  },
  exit(w, a, ctx, p) {
    const start = w[a.actor].position, gone = add3(start, OFFSETS[a.params.to]);
    return { world: setActor(w, a.actor, { position: p >= 1 ? gone : lerp3(start, gone, easeInCubic(p)), visible: p < 1 }), fx: [] };
  },
  pour(w, a, ctx, p) {
    const home = w[a.actor].position, into = a.params.into;
    const spot = add3(top(ctx, w, into), [-0.22, 0.35, 0]);
    const TILT = 1.9;
    let position, tilt;
    const fx = [];
    if (p < 0.25) {
      const q = easeInOutCubic(p / 0.25);
      position = lerp3(home, spot, q); tilt = q * TILT;
    } else if (p < 0.75) {
      position = spot; tilt = TILT;
      const level = Math.min(1, w[into].fill.level + a.params.fill_add * clamp01((p - 0.3) / 0.45));
      fx.push({ type: "stream", seed: a.seed, from: add3(spot, [0.08, 0.02, 0]), to: interior(ctx, w, into, level),
                t: (p - 0.25) * (a.t1 - a.t0), colour: a.params.colour });
    } else {
      const q = easeInOutCubic((p - 0.75) / 0.25);
      position = lerp3(spot, home, q); tilt = TILT * (1 - q);
    }
    if (p >= 1) { position = [...home]; tilt = 0; }
    let world = pourFill(w, into, a.params.fill_add, a.params.colour, clamp01((p - 0.3) / 0.45));
    world = setActor(world, a.actor, { position });
    if (tilt > 0) fx.push({ type: "tilt", actor: a.actor, angle: tilt });
    return { world, fx };
  },
  sprinkle(w, a, ctx, p) {
    const into = a.params.into, f = w[into].fill;
    const world = setFill(w, into, { level: Math.min(1, f.level + a.params.fill_add * clamp01((p - 0.2) / 0.7)) });
    const fx = p > 0.1 && p < 0.9
      ? [{ type: "grains", seed: a.seed, origin: add3(top(ctx, w, into), [0, 0.6, 0]),
           floorY: interior(ctx, w, into, f.level)[1], t: (p - 0.1) * (a.t1 - a.t0), colour: a.params.colour }]
      : [];
    return { world, fx };
  },
  mash(w, a, ctx, p) {
    const f = w[a.actor].fill;
    const colour = a.params.colour ? mixHex(f.colour, a.params.colour, p) : f.colour;
    const world = setFill(w, a.actor, { surface: p >= 0.15 ? "paste" : f.surface, colour });
    const fx = [];
    if (p > 0.05 && p < 0.95) {
      const base = interior(ctx, w, a.actor, f.level);
      fx.push({ type: "pestle", position: [base[0], base[1] + 0.02 + Math.abs(Math.sin(6 * Math.PI * p)) * 0.18, base[2]] });
    }
    return { world, fx };
  },
  steam(w, a, ctx, p) {
    const fx = p < 1 ? [{ type: "steam", seed: a.seed, origin: top(ctx, w, a.actor), t: p * (a.t1 - a.t0), intensity: a.params.intensity }] : [];
    return { world: w, fx };
  },
};

export function applyAction(world, action, ctx, p) {
  const move = MOVES[action.move];
  if (!move) throw new Error(`unknown move ${action.move}`);
  return move(world, action, ctx, clamp01(p));
}

// The scene at local time t: every started action applied in start order, each at its own progress.
export function stateAt(shot, t) {
  let world = shot.world, fx = [];
  for (const action of shot.actions) {
    if (t < action.t0) continue;
    const p = action.t1 > action.t0 ? Math.min(1, (t - action.t0) / (action.t1 - action.t0)) : 1;
    const out = applyAction(world, action, shot.ctx, p);
    world = out.world;
    if (p < 1) fx = fx.concat(out.fx);
  }
  return { world, fx };
}
