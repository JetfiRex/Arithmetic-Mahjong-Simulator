"""Count-based shape screening and bounded, explicitly heuristic potentials.

The screen covers the current built-in calculator. Pattern templates use its
arithmetic predicate; traditional/special shape checks reuse its checkers.
Potentials are ranking aids, not shanten, calibrated probabilities, or EV.
"""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from calculator_base.arithmetic_mahjong import ArithmeticMahjongChecker
from calculator_base.special_winning_checker import SpecialWinningChecker
from calculator_base.traditional_mahjong import TraditionalMahjongChecker
from bots.common_bot_helper.observation import TILE_VALUES, TILE_INDEX

_ARITHMETIC = ArithmeticMahjongChecker()
_TRADITIONAL = TraditionalMahjongChecker()
_SPECIAL = SpecialWinningChecker()


def values_from_counts(counts):
    return tuple(v for v, n in zip(TILE_VALUES, counts) for _ in range(n))


def counts_from_values(values):
    counter = Counter(values)
    if any(value not in TILE_INDEX for value in counter):
        raise ValueError('Unknown tile value')
    return tuple(counter[v] for v in TILE_VALUES)


def subtract_counts(counts, values):
    result = list(counts)
    for value in values:
        result[TILE_INDEX[value]] -= 1
    if min(result) < 0:
        raise ValueError('Action consumes unavailable hand tiles')
    return tuple(result)


def _pattern(values):
    return tuple(sorted(Counter(TILE_INDEX[v] for v in values).items()))


@lru_cache(maxsize=1)
def arithmetic_patterns():
    patterns = {_pattern((v,) * 4) for v in TILE_VALUES}
    numbers = [v for v in TILE_VALUES if isinstance(v, int)]
    for a in numbers:
        for b in numbers:
            for op, result in (('+', a + b), ('×', a * b)):
                if result in TILE_INDEX:
                    values = (a, op, b, result)
                    if _ARITHMETIC.is_valid_formula(values):
                        patterns.add(_pattern(values))
    return tuple(sorted(patterns))


@lru_cache(maxsize=1)
def traditional_patterns():
    # This is only a feature catalogue; legal HU always goes through RuleAdapter.
    patterns = {_pattern((v,) * 3) for v in TILE_VALUES}
    for start in (*range(1, 8), *range(11, 18)):
        patterns.add(_pattern((start, start + 1, start + 2)))
    return tuple(sorted(patterns))


@lru_cache(maxsize=1)
def progression_patterns():
    numbers = [v for v in TILE_VALUES if isinstance(v, int)]
    return tuple(_pattern(tuple(start + step * k for k in range(7) for _ in range(2)))
                 for start in numbers for step in range(1, 5)
                 if all(start + step * k in TILE_INDEX for k in range(7)))


@lru_cache(maxsize=32768)
def _arithmetic_partition(counts, groups):
    if groups == 0:
        return not any(counts)
    if sum(counts) != 4 * groups:
        return False
    first = next(i for i, n in enumerate(counts) if n)
    for pattern in _arithmetic_by_tile()[first]:
        if all(counts[i] >= n for i, n in pattern):
            rest = list(counts)
            for i, n in pattern:
                rest[i] -= n
            if _arithmetic_partition(tuple(rest), groups - 1):
                return True
    return False


@lru_cache(maxsize=1)
def _arithmetic_by_tile():
    # The first remaining tile must occur in the next group. Index once rather
    # than repeatedly scanning every formula at every recursion node.
    return tuple(tuple(p for p in arithmetic_patterns() if any(i == tile for i, _ in p))
                 for tile in range(len(TILE_VALUES)))


@lru_cache(maxsize=16384)
def possible_shape(counts, melds=()):
    """Exact boolean screen for current built-in shape families, ignoring fan.

    Special checks intentionally include meld values: this matches the current
    top-level calculator, rather than imposing a new closed-hand restriction.
    """
    groups = 3 - len(melds)
    if groups >= 0 and sum(counts) == groups * 4 + 2:
        for i, n in enumerate(counts):
            if n >= 2:
                rest = list(counts)
                rest[i] -= 2
                if _arithmetic_partition(tuple(rest), groups):
                    return True
    values = values_from_counts(counts)
    if not melds and _TRADITIONAL.can_win_traditional(list(values))[0]:
        return True
    all_values = values + tuple(v for meld in melds for v in meld.tiles)
    # 连七对 is a subset of 七小对, so the latter screens both families.
    return (_SPECIAL.can_win_qi_xiao_dui(list(all_values))[0]
            or _SPECIAL.can_win_qing_long(list(all_values))[0])


@lru_cache(maxsize=16384)
def _full_groups(counts, limit, family):
    if not limit:
        return 0
    patterns = arithmetic_patterns() if family == 'arithmetic' else traditional_patterns()
    best = 0
    for pattern in patterns:
        if all(counts[i] >= n for i, n in pattern):
            rest = list(counts)
            for i, n in pattern:
                rest[i] -= n
            best = max(best, 1 + _full_groups(tuple(rest), limit - 1, family))
            if best == limit:
                break
    return best


def _partial_support(counts, unseen, patterns, missing_slots, size):
    if missing_slots <= 0:
        return 0.0
    supports = []
    for pattern in patterns:
        matched = sum(min(counts[i], n) for i, n in pattern)
        if not 1 < matched < size:
            continue
        missing = [(i, max(0, n - counts[i])) for i, n in pattern]
        if any(unseen[i] < n for i, n in missing):
            continue
        # Availability downweights barely possible groups, without treating
        # unseen inventory as a known wall. Overlapping partials are optimistic.
        availability = min((unseen[i] / (unseen[i] + n) for i, n in missing if n), default=1)
        supports.append((matched / size) ** 3 * 2 * availability)
    return min(2.5, sum(sorted(supports, reverse=True)[:missing_slots]))


def _group_score(counts, meld_count, unseen, family):
    size, slots = (4, 3 - meld_count) if family == 'arithmetic' else (3, 4)
    if slots < 0:
        return 0.0
    patterns = arithmetic_patterns() if family == 'arithmetic' else traditional_patterns()
    best = 0.0
    # Pair reservation prevents a complete group and the pair from claiming
    # the same physical copies. Partial support remains an optimistic proxy.
    for pair in (None, *(i for i, n in enumerate(counts) if n >= 2)):
        rest = list(counts)
        if pair is not None:
            rest[pair] -= 2
        rest = tuple(rest)
        full = _full_groups(rest, slots, family)
        pair_score = 2.0 if pair is not None else (.4 if any(n and unseen[i] for i, n in enumerate(rest)) else 0)
        score = (4 * meld_count if family == 'arithmetic' else 0) + size * full + pair_score
        score += _partial_support(rest, unseen, patterns, slots - full, size)
        best = max(best, score)
    return best


@dataclass(frozen=True)
class ShapePotential:
    arithmetic: float
    traditional: float
    seven_pairs: float
    progression_pairs: float
    dragon: float

    @property
    def best(self):
        return max(self.arithmetic, self.traditional, self.seven_pairs, self.progression_pairs, self.dragon)

    @property
    def family(self):
        return max(('算术麻将', self.arithmetic), ('传统麻将', self.traditional),
                   ('七小对', self.seven_pairs), ('连七对', self.progression_pairs), ('清龙', self.dragon),
                   key=lambda pair: pair[1])[0]


@lru_cache(maxsize=8192)
def shape_potential(counts, melds, unseen):
    arithmetic = _group_score(counts, len(melds), unseen, 'arithmetic')
    traditional = _group_score(counts, 0, unseen, 'traditional') if not melds else 0.0
    combined = list(counts)
    for meld in melds:
        for tile in meld.tiles:
            combined[TILE_INDEX[tile]] += 1
    seven = progression = dragon = 0.0
    if sum(combined) == 13:
        seven = sum(n // 2 for n in combined) * 2
        seven += min(1.0, sum(.2 for i, n in enumerate(combined) if n % 2 and unseen[i]))
        for pattern in progression_patterns():
            if all(combined[i] + unseen[i] >= n for i, n in pattern):
                progression = max(progression, .95 * sum(min(combined[i], n) for i, n in pattern))
        for pair in (None, *(i for i, n in enumerate(combined) if n >= 2)):
            rest = combined.copy()
            if pair is not None:
                rest[pair] -= 2
            for start in range(10):
                indices = [TILE_INDEX[v] for v in range(start, start + 12)]
                if all(rest[i] or unseen[i] for i in indices):
                    score = sum(bool(rest[i]) for i in indices) + (2 if pair is not None else .4)
                    dragon = max(dragon, float(score))
    return ShapePotential(arithmetic, traditional, seven, progression, dragon)
