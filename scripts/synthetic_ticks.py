"""Seeded synthetic Merton jump-diffusion price path for offline runs and tests.

Randomness comes only from random.Random(seed).random(), turned into normals
with Box-Muller and into jump counts with a multiplicative Poisson sampler. The
same seed gives the same ticks on the same platform; elsewhere, values can
differ in the last bits because math functions come from the C library.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

SECS_PER_YEAR = 365.25 * 24.0 * 3600.0


@dataclass(frozen=True)
class PathSpec:
    seed: int = 7
    n_ticks: int = 4_225
    s0: float = 60_000.0
    dt_seconds: float = 3_600.0
    sigma: float = 0.5
    lambda_: float = 30.0
    mu_j: float = -0.02
    delta_j: float = 0.03
    start_us: int = 1_700_000_000_000_000


def _normal(rng: random.Random) -> float:
    u1 = 1.0 - rng.random()  # in (0, 1], so log(u1) is finite
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _poisson(rng: random.Random, mean: float) -> int:
    threshold = math.exp(-mean)
    count = 0
    product = rng.random()
    while product > threshold:
        count += 1
        product *= rng.random()
    return count


def merton_ticks(spec: PathSpec = PathSpec()) -> list[tuple[float, int]]:
    """(price, epoch_us) pairs at a fixed spacing, starting at spec.s0."""
    rng = random.Random(spec.seed)
    dt_years = spec.dt_seconds / SECS_PER_YEAR
    dt_us = round(spec.dt_seconds * 1e6)
    k = math.exp(spec.mu_j + 0.5 * spec.delta_j**2) - 1.0
    drift = (-spec.lambda_ * k - 0.5 * spec.sigma**2) * dt_years
    vol = spec.sigma * math.sqrt(dt_years)

    price = spec.s0
    ts = spec.start_us
    ticks = [(price, ts)]
    for _ in range(spec.n_ticks - 1):
        x = drift + vol * _normal(rng)
        for _ in range(_poisson(rng, spec.lambda_ * dt_years)):
            x += spec.mu_j + spec.delta_j * _normal(rng)
        price *= math.exp(x)
        ts += dt_us
        ticks.append((price, ts))
    return ticks
