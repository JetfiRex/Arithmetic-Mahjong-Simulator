"""Public-inventory danger/feeding INDICES, not calibrated deal-in chances.

This ruleset has no furiten. A prior discard never certifies a safe tile.
No sampled or real opponent hand, response intent, or future wall is consulted.
"""

from dataclasses import dataclass
from functools import lru_cache
from math import comb

from bots.math_bot_helper.analysis import unseen_counts
from bots.math_bot_helper.features import arithmetic_patterns, traditional_patterns
from bots.common_bot_helper.observation import TILE_VALUES, TILE_INDEX

RISK_VERSION = 'public-structure-risk-v1'


def at_least(total, copies, hand_size, needed):
    """Hypergeometric marginal under exchangeable unknown allocation."""
    if not 0 <= copies <= total or not 0 <= hand_size <= total or needed < 0:
        raise ValueError('Invalid unknown population')
    if not needed:
        return 1.0
    denom = comb(total, hand_size)
    return sum(comb(copies, n) * comb(total - copies, hand_size - n)
               for n in range(max(needed, hand_size - (total - copies)), min(copies, hand_size) + 1)) / denom


def contains_upper(stock, size, required):
    """Expected matching subsets, an upper bound on containing a multiset.

    Summing this over different formulas is an index, not a union probability.
    """
    total, need = sum(stock), sum(n for _, n in required)
    if need > size or size > total or any(stock[i] < n for i, n in required):
        return 0.0
    ways = 1
    for i, n in required:
        ways *= comb(stock[i], n)
    return min(1.0, ways * comb(size, need) / comb(total, need)) if need else 1.0


@lru_cache(maxsize=1)
def _completions():
    formulas, traditional = [], []
    for index, value in enumerate(TILE_VALUES):
        def remaining(pattern):
            return tuple((i, n - int(i == index)) for i, n in pattern if n - int(i == index))
        formulas.append(tuple(remaining(p) for p in arithmetic_patterns()
                              if any(i == index for i, _ in p) and len(p) > 1))
        traditional.append(tuple(remaining(p) for p in traditional_patterns() if any(i == index for i, _ in p)))
    return tuple(formulas), tuple(traditional)


@dataclass(frozen=True)
class TileRisk:
    tile: int | str
    danger: float
    feeding: float


@dataclass(frozen=True)
class RiskTable:
    tiles: tuple[TileRisk, ...]
    pressure: float
    note: str = 'Uncalibrated public-structure indices; old discards are not safe guarantees'

    def for_tile(self, tile):
        return self.tiles[TILE_INDEX[tile]]


def assess_risk(observation, stock=None):
    stock = unseen_counts(observation) if stock is None else tuple(stock)
    total = sum(stock)
    if len(stock) != len(TILE_VALUES) or any(type(n) is not int or n < 0 for n in stock):
        raise ValueError('Invalid unseen inventory')
    # Tiny hand-crafted fixtures may omit other hands. These remain usable but
    # do not create imaginary opponent slots beyond the visible inventory.
    opponents = observation.players[1:]
    initial_wall = max(1, sum(observation.tile_supply) - 52)
    late = max(0.0, min(1.0, 1 - observation.wall_remaining / initial_wall))
    pressure = sum(.25 + .3 * len(p.melds) + .3 * late for p in opponents) / 3
    formulas, trad = _completions()
    tiles = []
    for index, value in enumerate(TILE_VALUES):
        danger = feeding = 0.0
        for p in opponents:
            size = min(p.hand_count, total)
            if not size:
                continue
            pairs = at_least(total, stock[index], size, 1)
            formula = min(1.0, sum(contains_upper(stock, size, r) for r in formulas[index]))
            traditional = min(1.0, sum(contains_upper(stock, size, r) for r in trad[index])) if not p.melds else 0
            # Open hands have fewer unknown groups. This is a threat cue, not a
            # claim that anyone is known to be ready. Special shapes retain a
            # small residual even when an ordinary same-tile pair is impossible.
            threat = .25 + .3 * len(p.melds) + .3 * late
            support = pairs + (.4 * formula if len(p.melds) < 3 else 0) + .2 * traditional + .05
            danger += threat * support
            if len(p.melds) < 3:
                feeding += at_least(total, stock[index], size, 3)
                if observation.wall_remaining:
                    feeding += .25 * at_least(total, stock[index], size, 4)
                if p.seat == 1:
                    feeding += formula  # only the next relative seat can CHI
        tiles.append(TileRisk(value, danger, feeding))
    return RiskTable(tuple(tiles), pressure)
