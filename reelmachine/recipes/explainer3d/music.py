"""Synthesised sound for cinematic reels: a music bed and impact effects.

Seeded, so a reel sounds the same every time, and generated here, so there is no licence
to track. The bed is a 128 bpm minor groove (kick, clap, hats, sub bass, a plucked hook);
it is low-passed wherever a shot is underwater. The voice ducks it later, in the mix.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from scipy.signal import butter, sosfilt

from ...config import Settings

SR = 48000
BPM = 128


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1.0, t / attack) * np.exp(-t / decay)


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return sosfilt(butter(2, [lo, hi], btype="band", fs=SR, output="sos"), x)


def _low(x: np.ndarray, f: float) -> np.ndarray:
    return sosfilt(butter(2, f, btype="low", fs=SR, output="sos"), x)


def _high(x: np.ndarray, f: float) -> np.ndarray:
    return sosfilt(butter(2, f, btype="high", fs=SR, output="sos"), x)


def _add(track: np.ndarray, sig: np.ndarray, at_s: float, gain: float = 1.0) -> None:
    i = int(at_s * SR)
    if 0 <= i < len(track):
        sig = sig[: len(track) - i]
        track[i:i + len(sig)] += sig * gain


def _write(dest: Path, mono: np.ndarray) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(mono))) or 1.0
    pcm = (np.clip(mono / peak * 0.9, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(np.repeat(pcm, 2).tobytes())
    return dest


def synth_music(
    dest: Path, *, total_ms: int, underwater: Sequence[tuple[int, int]] = (), settings: Settings | None = None,
    seed: int = 7,
) -> Path:
    rng = np.random.default_rng(seed)
    n = int(total_ms * SR / 1000)
    music = np.zeros(n)
    beat = 60 / BPM
    t_half = np.arange(int(0.5 * SR)) / SR

    kick_f = 45 + 110 * np.exp(-t_half * 30)
    kick = np.sin(2 * np.pi * np.cumsum(kick_f) / SR) * np.exp(-t_half * 7) * 0.9

    def clap() -> np.ndarray:
        noise = rng.standard_normal(int(0.25 * SR))
        return _band(noise, 900, 3500) * _env(len(noise), 0.001, 0.07) * 0.6

    def hat() -> np.ndarray:
        noise = rng.standard_normal(int(0.08 * SR))
        return _high(noise, 7000) * _env(len(noise), 0.001, 0.02) * 0.25

    def pluck(freq: float) -> np.ndarray:
        t = np.arange(int(0.32 * SR)) / SR
        tone = sum(np.sin(2 * np.pi * freq * h * t) / h for h in (1, 2, 3, 4))
        return _low(tone, 2800) * np.exp(-t * 9) * 0.18

    def bass(freq: float, dur: float) -> np.ndarray:
        t = np.arange(int(dur * SR)) / SR
        tone = np.tanh(2.2 * np.sin(2 * np.pi * freq * t)) * np.minimum(1, t / 0.005) * np.exp(-t * 2.5)
        return _low(tone, 400) * 0.45

    progression = [110.0, 110.0, 87.31, 87.31, 130.81, 130.81, 98.0, 98.0]
    scale = [440.0, 523.25, 587.33, 659.25, 783.99, 880.0]
    hook = [0, 2, 4, 2, 5, 4, 2, 1, 0, 2, 4, 5, 4, 2, 3, 1]
    b = 0
    while b * beat < total_ms / 1000:
        at = b * beat
        _add(music, kick, at)
        if b % 4 in (1, 3):
            _add(music, clap(), at)
        _add(music, hat(), at, 0.6)
        _add(music, hat(), at + beat / 2)
        if b % 2 == 0:
            _add(music, bass(progression[(b // 2) % len(progression)], beat * 2), at)
        _add(music, pluck(scale[hook[b % 16]]), at + beat / 2)
        b += 1

    if underwater:
        muffled = _low(music, 500) * 1.4
        x = np.arange(n)
        weight = np.zeros(n)
        for start_ms, end_ms in underwater:
            a, z = start_ms * SR / 1000, end_ms * SR / 1000
            weight = np.maximum(weight, np.clip((x - a) / (0.08 * SR), 0, 1) * np.clip((z - x) / (0.1 * SR), 0, 1))
        music = music * (1 - weight) + muffled * weight
    fade = min(n, int(0.3 * SR))
    music[n - fade:] *= np.linspace(1, 0, fade)
    return _write(dest, music)


# ---------------------------------------------------------------- impact effects
def _splash(rng: np.random.Generator) -> np.ndarray:
    n = int(1.4 * SR)
    noise = rng.standard_normal(n)
    body = _low(noise, 2500) * _env(n, 0.004, 0.25) * 0.9
    spray = _high(noise, 3000) * _env(n, 0.01, 0.5) * 0.35
    thump = np.sin(2 * np.pi * 70 * np.arange(n) / SR) * _env(n, 0.002, 0.12) * 0.8
    drops = np.zeros(n)
    for _ in range(25):
        at, f = int(rng.uniform(0.1, 1.1) * SR), rng.uniform(600, 1800)
        tt = np.arange(int(0.05 * SR)) / SR
        d = np.sin(2 * np.pi * (f + 3000 * tt) * tt) * np.exp(-tt * 80) * 0.15
        drops[at:at + len(d)] += d[: n - at]
    return body + spray + thump + drops


def _boom(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(1.2 * SR)) / SR
    return np.sin(2 * np.pi * np.cumsum(35 + 80 * np.exp(-t * 12)) / SR) * np.exp(-t * 3)


def _crunch(rng: np.random.Generator) -> np.ndarray:
    n = int(0.5 * SR)
    grains = (rng.random(n) < 0.02).astype(float) * rng.standard_normal(n) * 6
    return _band(rng.standard_normal(n) * 0.3 + grains, 800, 6000) * _env(n, 0.002, 0.15) * 0.7


def _gulp(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.6 * SR)) / SR
    return np.sin(2 * np.pi * np.cumsum(380 - 430 * t) / SR) * _env(len(t), 0.005, 0.18) * 0.9


def _bubbles(rng: np.random.Generator) -> np.ndarray:
    n = int(1.6 * SR)
    out = np.zeros(n)
    for _ in range(30):
        at, f = int(rng.uniform(0, 1.5) * SR), rng.uniform(400, 1400)
        tt = np.arange(int(0.05 * SR)) / SR
        b = np.sin(2 * np.pi * (f + 2500 * tt) * tt) * np.exp(-tt * 60) * 0.3
        out[at:at + len(b)] += b[: n - at]
    return out


def _whoosh(rng: np.random.Generator) -> np.ndarray:
    n = int(0.4 * SR)
    noise, out, steps = rng.standard_normal(n), np.zeros(n), 24
    for k in range(steps):
        s0, s1, f = k * n // steps, (k + 1) * n // steps, 500 + 3500 * k / steps
        out[s0:s1] = _band(noise, f * 0.7, min(f * 1.3, SR / 2 - 1))[s0:s1]
    return out * np.sin(np.linspace(0, np.pi, n)) ** 2 * 0.6


def _thud(rng: np.random.Generator) -> np.ndarray:
    n = int(0.35 * SR)
    knock = _band(rng.standard_normal(n), 150, 900) * _env(n, 0.001, 0.05)
    body = np.sin(2 * np.pi * 85 * np.arange(n) / SR) * _env(n, 0.001, 0.09)
    return knock * 0.8 + body


def _pop(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.12 * SR)) / SR
    return np.sin(2 * np.pi * np.cumsum(300 + 2200 * t / 0.12) / SR) * _env(len(t), 0.002, 0.04)


SOUND_DESIGN: dict[str, Callable[[np.random.Generator], np.ndarray]] = {
    "splash": _splash, "boom": _boom, "crunch": _crunch, "gulp": _gulp, "bubbles": _bubbles,
    "whoosh": _whoosh, "thud": _thud, "pop": _pop,
}


def synth_effect(name: str, dest: Path, seed: int = 3) -> Path:
    return _write(dest, SOUND_DESIGN[name](np.random.default_rng(seed)))
