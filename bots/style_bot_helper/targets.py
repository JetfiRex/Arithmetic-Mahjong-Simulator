"""Small, inventory-feasible big-hand catalogue with stable target selection.

Targets cover seven pairs, progression pairs and dragon, not every high-fan
arithmetic decomposition. Fan-table floors are estimates until oracle-verified.
"""

from dataclasses import dataclass
from functools import lru_cache

from bots.math_bot_helper.features import counts_from_values, progression_patterns, seven_pairs_target
from bots.common_bot_helper.observation import TILE_VALUES
from fan_calculator.fan_base import FanType, MAX_FAN

TARGET_VERSION = 'special-targets-v1'


@dataclass(frozen=True)
class BigTarget:
    family: str
    whole: tuple[int, ...]
    fan_floor: int
    payment: int | None = None
    verified_melds: tuple | None = None

    @property
    def key(self):
        return self.family, self.whole


def fixed_counts(melds):
    return counts_from_values(v for m in melds for v in m.tiles)


def target_missing(target, hand, melds, stock):
    fixed = fixed_counts(melds)
    goal = tuple(w - m for w, m in zip(target.whole, fixed))
    if (sum(fixed) + (3 - len(melds)) * 4 + 2 != 14
            or (target.family in ('七小对', '连七对')
                and (sum(target.whole) != 14 or any(n not in (0, 2) for n in target.whole)))
            or any(n < 0 or n > h + u for n, h, u in zip(goal, hand, stock))):
        return None
    return sum(max(0, n - h) for n, h in zip(goal, hand))


@lru_cache(maxsize=1)
def fixed_targets():
    targets = []
    for pattern in progression_patterns():
        whole = [0] * len(TILE_VALUES)
        for i, n in pattern:
            whole[i] = n
        targets.append(BigTarget('连七对', tuple(whole), FanType.LIAN_QI_DUI.fan_value))
    for start in range(10):
        for pair in TILE_VALUES:
            targets.append(BigTarget('清龙', counts_from_values((*range(start, start + 12), pair, pair)), FanType.QING_LONG.fan_value))
    return tuple(targets)


def _seven_pairs(hand, melds, stock):
    fixed = fixed_counts(melds)
    size = 14 - sum(fixed)
    if size != (3 - len(melds)) * 4 + 2:
        return None
    result = seven_pairs_target(hand, fixed, tuple(h + u for h, u in zip(hand, stock)))
    if result is None:
        return None
    _, concealed = result
    whole = tuple(n + m for n, m in zip(concealed, fixed))
    return BigTarget('七小对', whole, FanType.QI_XIAO_DUI.fan_value)


def choose_target(hand, melds, stock, wall_remaining, min_fan, previous=None):
    """Retain a feasible target unless another improves its heuristic by >1.25.

    Conservative own-draw horizon: at most four missing copies, further limited
    by floor(wall/4). This is an exit rule, not a completion probability.
    """
    horizon = min(4, max(0, wall_remaining // 4))
    def merit(target):
        missing = target_missing(target, hand, melds, stock)
        if missing is None or missing > horizon or target.fan_floor < min_fan:
            return None
        return 1.5 * min(MAX_FAN, target.fan_floor) - 2 * missing
    candidates = list(fixed_targets())
    seven = _seven_pairs(hand, melds, stock)
    if seven:
        candidates.append(seven)
    if previous is not None:
        candidates.append(previous)
    ranked = [(merit(t), t) for t in candidates]
    ranked = [(s, t) for s, t in ranked if s is not None]
    if not ranked:
        return None, 'No catalogue target within visible inventory and remaining-draw horizon'
    score, best = max(ranked, key=lambda item: item[0])
    if previous is not None:
        old = merit(previous)
        if old is not None and old + 1.25 >= score:
            return previous, 'Retained feasible target'
    return best, 'Selected feasible catalogue target'
