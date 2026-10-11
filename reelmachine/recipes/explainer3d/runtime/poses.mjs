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
      // the vessel pivots on its base, so its neck ends up over the container
      const h = ctx.dims[a.actor][1] * w[a.actor].scale;
      const neck = add3(spot, [h * Math.sin(TILT), h * Math.cos(TILT), 0]);
      fx.push({ type: "stream", seed: a.seed, from: neck, to: interior(ctx, w, into, level),
                t: (p - 0.25) * (a.t1 - a.t0), colour: a.params.colour });
    } else {
      const q = easeInOutCubic((p - 0.75) / 0.25);
      position = lerp3(spot, home, q); tilt = TILT * (1 - q);
    }
    if (p >= 1) { position = [...home]; tilt = 0; }
    let world = pourFill(w, into, a.params.fill_add, a.params.colour, clamp01((p - 0.3) / 0.45));
    world = setActor(world, a.actor, { position });
    // negative about Z tips the neck toward +x, where the container is
    if (tilt > 0) fx.push({ type: "tilt", actor: a.actor, angle: -tilt });
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

  // ---- cinematic. `over` is seconds past the action's end: lingering effects (a splash
  // that is still falling, crumbs still settling) keep animating after p reaches 1.
  leap(w, a, ctx, p, over) {
    const P = a.params, c = point(ctx, w, P.spot), sign = P.direction === "right" ? 1 : -1, dur = a.t1 - a.t0;
    const from = [c[0] - (sign * P.span) / 2, -0.5, c[2]], to = [c[0] + (sign * P.span) / 2, -0.6, c[2]];
    const u = slowmo(p, P.slowmo);
    const at = (k) => { const q = lerp3(from, to, k); q[1] += 4 * (P.height + 0.55) * k * (1 - k); return q; };
    const pos = at(u), ahead = at(Math.min(1, u + 0.01)), back = at(Math.max(0, u - 0.01));
    const fx = [];
    const age = p * dur + over, landU = 0.97, landP = unslowmo(landU, P.slowmo);
    fx.push({ type: "splash", at: [from[0], 0, from[2]], age, size: 1.3, linger: true, seed: a.seed });
    if (p >= landP) fx.push({ type: "splash", at: [to[0], 0, to[2]], age: (p - landP) * dur + over, size: 1.1, linger: true, seed: a.seed + 1 });
    if (p < 1) {
      fx.push({ type: "orient", actor: a.actor, dir: [ahead[0] - back[0], ahead[1] - back[1], ahead[2] - back[2]] });
      fx.push({ type: "wiggle", actor: a.actor, phase: age * 14, amp: 0.12 });
      fx.push({ type: "drops", seed: a.seed, path: { from, to, height: P.height + 0.55 }, u, age });
    }
    const end = p >= 1;
    return { world: setActor(w, a.actor, { position: end ? to : pos, visible: !end && pos[1] > -0.45 }), fx };
  },
  slam(w, a, ctx, p, over) {
    const ref = a.params.on, dur = a.t1 - a.t0;
    const dest = ref == null ? [...ctx.homes[a.actor]] : ref in ctx.anchors ? point(ctx, w, ref) : top(ctx, w, ref);
    const hit = 0.45, from = add3(dest, [0, a.params.drop, 0]);
    const position = p >= 1 ? dest : p < hit ? lerp3(from, dest, easeInCubic(p / hit)) : dest;
    const fx = [];
    if (p >= hit) {
      const age = (p - hit) * dur + over;
      fx.push({ type: "impact", age, strength: 0.012, linger: true }, { type: "dust", at: dest, age, size: 0.6, linger: true, seed: a.seed });
      if (p < 1) fx.push({ type: "squash", actor: a.actor, k: Math.exp(-age * 10) * Math.sin(age * 40) * 0.25 });
    }
    return { world: setActor(w, a.actor, { position, visible: true, scale: 1 }), fx };
  },
  burst(w, a, ctx, p, over) {
    const P = a.params, into = P.into, dur = a.t1 - a.t0, f = w[into].fill, level0 = f.level;
    const above = add3(top(ctx, w, into), [0, 0.09, 0]), inside = interior(ctx, w, into, level0);
    const pop = 0.55, colour = P.colour || ctx.colours[a.actor];
    let world = w;
    const fx = [];
    if (p < pop) {
      const position = p < 0.4 ? hop(w[a.actor].position, above, easeInOutCubic(p / 0.4), 0.18) : above;
      const wob = p >= 0.4 ? 1 + 0.12 * Math.sin((p - 0.4) * dur * 30) : 1;
      world = setActor(world, a.actor, { position, visible: true, scale: wob });
    } else {
      const age = (p - pop) * dur + over, q = p >= 1 ? 1 : clamp01((p - pop) / 0.3);
      world = setFill(world, into, { level: Math.min(1, level0 + P.fill_add * q), surface: "crumbs", colour });
      world = setActor(world, a.actor, { position: inside, visible: false, scale: 0 });
      fx.push({ type: "impact", age, strength: 0.02, linger: true }, { type: "flash", age, amount: 0.35, linger: true });
      if (age < 1.2) fx.push({ type: "crumbs", seed: a.seed, from: above, into, age, colour, linger: true });
    }
    if (p >= 1) world = setFill(world, into, { level: Math.min(1, level0 + P.fill_add), surface: "crumbs", colour });
    return { world, fx };
  },
  drizzle(w, a, ctx, p) {
    const P = a.params, into = P.into, f = w[into].fill, q = p >= 1 ? 1 : clamp01((p - 0.1) / 0.75);
    const level = Math.min(1, f.level + P.fill_add * q);
    let world = setFill(w, into, p >= 0.1 ? { level, glaze: P.colour } : { level });
    const fx = [];
    if (p > 0.05 && p < 0.92) {
      const surface = interior(ctx, world, into, level);
      fx.push({ type: "honey", from: add3(top(ctx, w, into), [0, 0.7, 0]), to: surface, into, colour: P.colour,
                thin: clamp01((0.92 - p) / 0.1), grow: clamp01((p - 0.05) / 0.06), t: p * (a.t1 - a.t0) });
    }
    if (p < 1 && p >= 0.1) fx.push({ type: "coil", into, amount: q });
    if (p >= 1) world = setFill(w, into, { level: Math.min(1, f.level + P.fill_add), glaze: P.colour });
    return { world, fx };
  },
  smash(w, a, ctx, p, over) {
    const P = a.params, into = P.into, dur = a.t1 - a.t0, f = w[into].fill, level0 = f.level;
    const hover = add3(top(ctx, w, into), [0, 0.28, 0]), inside = interior(ctx, w, into, level0), hit = 0.62;
    let world = w;
    const fx = [];
    if (p < hit) {
      let position;
      if (p < 0.35) position = lerp3(w[a.actor].position, hover, easeInOutCubic(p / 0.35));
      else if (p < 0.55) position = add3(hover, [0, 0.03 * Math.sin((p - 0.35) * 30), 0]);
      else if (p < 0.58) position = add3(hover, [0, 0.08 * easeOutCubic((p - 0.55) / 0.03), 0]);
      else position = lerp3(add3(hover, [0, 0.08, 0]), inside, easeInCubic((p - 0.58) / 0.04));
      world = setActor(world, a.actor, { position, visible: true, scale: 1 });
      if (p >= 0.35) fx.push({ type: "spin", actor: a.actor, angle: (p - 0.35) * dur * 2.2 });
    } else {
      const age = (p - hit) * dur + over, q = p >= 1 ? 1 : clamp01((p - hit) / 0.12);
      world = setActor(world, a.actor, { position: inside, visible: false, scale: 0 });
      world = setFill(world, into, { level: Math.min(1, level0 + P.fill_add * q), surface: "paste", colour: P.colour, glaze: null });
      fx.push({ type: "impact", age, strength: 0.03, linger: true }, { type: "flash", age, amount: 0.6, linger: true },
              { type: "ring", at: top(ctx, w, into), age, linger: true },
              { type: "chunks", seed: a.seed, at: add3(top(ctx, w, into), [0, 0.02, 0]), age, colour: P.colour, floorY: w[into].position[1], linger: true },
              { type: "dust", at: w[into].position, age, size: 1.2, linger: true, seed: a.seed + 3 });
      if (p < 1) fx.push({ type: "squashFill", into, k: Math.exp(-age * 6) * Math.sin(age * 30) * 0.15 });
    }
    if (p >= 1) world = setFill(setActor(w, a.actor, { position: inside, visible: false, scale: 0 }), into,
      { level: Math.min(1, level0 + P.fill_add), surface: "paste", colour: P.colour, glaze: null });
    return { world, fx };
  },
  pop_out(w, a, ctx, p) {
    const P = a.params, from = P.from, f = w[from].fill, to = point(ctx, w, P.to);
    let world = setFill(w, from, { level: p >= 1 ? Math.max(0, f.level - P.take) : Math.max(0, f.level - P.take * clamp01(p / 0.9)) });
    world = setActor(world, a.actor, { position: to, visible: true, scale: 1 });
    const fx = p < 1 ? [{ type: "popRow", actor: a.actor, from: interior(ctx, w, from, f.level), p, dur: a.t1 - a.t0 }] : [];
    return { world, fx };
  },
  throw(w, a, ctx, p, over) {
    const to = point(ctx, w, a.params.to), start = w[a.actor].position, dur = a.t1 - a.t0, land = 0.6;
    const sunk = [to[0], -0.4, to[2]];
    const fx = [];
    let position, visible = true;
    if (p < land) {
      const k = p / land;
      position = lerp3(start, [to[0], 0, to[2]], k);
      position[1] = start[1] + (0 - start[1]) * k + 4 * 0.7 * k * (1 - k);
    } else {
      position = sunk; visible = false;
      fx.push({ type: "splash", at: [to[0], 0, to[2]], age: (p - land) * dur + over, size: 0.6, linger: true, seed: a.seed });
    }
    if (p >= 1) { position = sunk; visible = false; }
    return { world: setActor(w, a.actor, { position, visible }), fx };
  },
  splash(w, a, ctx, p, over) {
    return { world: w, fx: [{ type: "splash", at: point(ctx, w, a.params.spot), age: p * (a.t1 - a.t0) + over, size: a.params.size, linger: true, seed: a.seed }] };
  },
  sink(w, a, ctx, p) {
    const from = point(ctx, w, a.params.from), to = point(ctx, w, a.params.to);
    const position = p >= 1 ? to : lerp3(from, to, easeOutCubic(p));
    const fx = p < 1 ? [{ type: "bubbles", seed: a.seed, from, to, p, dur: a.t1 - a.t0 }] : [];
    return { world: setActor(w, a.actor, { position, visible: true }), fx };
  },
  school(w, a, ctx, p) {
    const P = a.params, c = center(ctx, w, P.around), eatAt = 0.78;
    let world = setActor(w, a.actor, { position: c, visible: true, scale: 1 });
    if (P.eat && P.around in w && p >= eatAt) world = setActor(world, P.around, { visible: false, scale: 0 });
    const fx = p < 1 ? [{ type: "school", actor: a.actor, center: c, radius: P.radius, p, dur: a.t1 - a.t0, eat: P.eat, eatAt, seed: a.seed }] : [];
    if (P.eat && p >= eatAt && p < 1) fx.push({ type: "burstBubbles", at: c, age: (p - eatAt) * (a.t1 - a.t0), seed: a.seed });
    return { world, fx };
  },
};

// slow motion: the middle of the arc (the apex) plays slower by `s`; u(p) is monotonic and
// meets p at 0 and 1. The middle 64% of the time covers (1 - s) of its usual distance.
const SM_A = 0.18, SM_B = 0.82;
function smKnots(s) {
  const width = (SM_B - SM_A) * (1 - s);
  return [0.5 - width / 2, 0.5 + width / 2];
}
export function slowmo(p, s) {
  const [ua, ub] = smKnots(s);
  if (p < SM_A) return (p / SM_A) * ua;
  if (p < SM_B) return ua + ((p - SM_A) / (SM_B - SM_A)) * (ub - ua);
  return ub + ((p - SM_B) / (1 - SM_B)) * (1 - ub);
}
export function unslowmo(u, s) {
  const [ua, ub] = smKnots(s);
  if (u < ua) return (u / ua) * SM_A;
  if (u < ub) return SM_A + ((u - ua) / (ub - ua)) * (SM_B - SM_A);
  return SM_B + ((u - ub) / (1 - ub)) * (1 - SM_B);
}
function center(ctx, w, ref) {
  if (ref in ctx.anchors) return [...ctx.anchors[ref]];
  const a = w[ref];
  return [a.position[0], a.position[1] + (ctx.dims[ref][1] * a.scale) / 2, a.position[2]];
}

export function applyAction(world, action, ctx, p, over = 0) {
  const move = MOVES[action.move];
  if (!move) throw new Error(`unknown move ${action.move}`);
  return move(world, action, ctx, clamp01(p), over);
}

// The scene at local time t: every started action applied in start order, each at its own progress.
export function stateAt(shot, t) {
  let world = shot.world, fx = [];
  for (const action of shot.actions) {
    if (t < action.t0) continue;
    const p = action.t1 > action.t0 ? Math.min(1, (t - action.t0) / (action.t1 - action.t0)) : 1;
    const out = applyAction(world, action, shot.ctx, p, Math.max(0, t - action.t1));
    world = out.world;
    fx = fx.concat(p < 1 ? out.fx : out.fx.filter((f) => f.linger));
  }
  return { world, fx };
}
