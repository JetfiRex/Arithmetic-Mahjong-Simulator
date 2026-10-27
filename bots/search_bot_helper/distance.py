"""Inventory-constrained shape distance, with certified anytime bounds.

D(H) = min_W sum(max(W[t] - H[t], 0)). W is a concealed winning
SHAPE with the given fixed melds. This ignores min-fan, opponents and timing;
only the rule adapter can certify a payable win. No physical copy is reused.
"""

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

from bots.math_bot_helper.analysis import AnalysisBudget
from bots.math_bot_helper.features import arithmetic_patterns, traditional_patterns, counts_from_values, possible_shape, seven_pairs_target
from bots.common_bot_helper.observation import TILE_VALUES

DISTANCE_VERSION = 'shape-distance-v2-distinct-pairs'
INF = 99


@dataclass(frozen=True)
class DistanceResult:
    lower: int
    upper: int | None
    target: tuple[int, ...] | None
    family: str | None
    complete: bool
    nodes: int = 0

    @property
    def value(self):
        return self.upper if self.complete else None


@dataclass(frozen=True)
class Ukeire:
    tiles: tuple[tuple[int | str, int, int], ...]  # value, copies, new D
    checked_tiles: tuple[int | str, ...]
    complete: bool
    baseline: DistanceResult

    @property
    def copies(self):
        return sum(n for _, n, _ in self.tiles)


class _Interrupted(Exception):
    pass


@lru_cache(maxsize=1)
def _dragon_targets():
    targets = []
    for start in range(10):
        for pair in TILE_VALUES:
            targets.append(counts_from_values((*range(start, start + 12), pair, pair)))
    return tuple(targets)


def _special_target(hand, fixed, capacity):
    """Exact seven-pair knapsack plus all dragon targets; melds stay fixed."""
    size = 14 - sum(fixed)
    if size < 0:
        return INF, None, None
    best, target = seven_pairs_target(hand, fixed, capacity) or (INF, None)
    family = '七小对' if target is not None else None  # includes 连七对
    for whole in _dragon_targets():
        concealed = tuple(n - m for n, m in zip(whole, fixed))
        if all(0 <= n <= cap for n, cap in zip(concealed, capacity)):
            cost = sum(max(0, n - h) for n, h in zip(concealed, hand))
            if cost < best:
                best, target, family = cost, concealed, '清龙'
    return best, target, family


class DistanceAnalyzer:
    def __init__(self, cache_size=4096):
        if cache_size < 0:
            raise ValueError('cache_size must be nonnegative')
        self.cache_size = cache_size
        self.cache = OrderedDict()
        self.cache_hits = 0
        self.nodes = 0

    def analyze(self, hand, melds=(), unseen=None, *, budget=None):
        hand, melds = tuple(hand), tuple(melds)
        if len(hand) != len(TILE_VALUES) or any(type(n) is not int or n < 0 for n in hand):
            raise ValueError('Invalid hand counts')
        if not 0 <= len(melds) <= 3:
            raise ValueError('Invalid fixed meld count')
        fixed = counts_from_values(v for m in melds for v in m.tiles)
        if unseen is None:
            from calculator_base.constants import TILE_COUNTS
            unseen = tuple(TILE_COUNTS[v] - hand[i] - fixed[i] for i, v in enumerate(TILE_VALUES))
        unseen = tuple(unseen)
        if len(unseen) != len(hand) or any(type(n) is not int or n < 0 for n in unseen):
            raise ValueError('Invalid unseen counts')
        key = (DISTANCE_VERSION, hand, melds, unseen)
        if key in self.cache:
            self.cache_hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        budget = budget or AnalysisBudget()
        capacity = tuple(h + u for h, u in zip(hand, unseen))
        size = (3 - len(melds)) * 4 + 2
        if sum(hand) not in (size - 1, size):
            raise ValueError('Distance requires a post-discard or drawn ordinary-round hand')
        lower = max(0, size - sum(hand))
        if budget.expired():
            return DistanceResult(lower, None, None, None, False)
        if sum(hand) == size and possible_shape(hand, melds):
            return DistanceResult(0, 0, hand, '已成形', True)
        best, target, family = _special_target(hand, fixed, capacity) if sum(fixed) + size == 14 else (INF, None, None)
        nodes = 0

        def witness(patterns, groups, group_size, limit):
            # Nondecreasing group IDs remove permutation duplicates. Bounds use
            # remaining physical hand/capacity after BOTH pair and prior groups.
            eligible = [p for p in patterns if all(capacity[i] >= n for i, n in p)]
            pairs = sorted((i for i, cap in enumerate(capacity) if cap >= 2),
                           key=lambda i: max(0, 2 - hand[i]))
            dead = set()

            def visit(rest, caps, slots, first, left, selected):
                nonlocal nodes
                nodes += 1
                if nodes % 64 == 1 and budget.expired():
                    raise _Interrupted
                if not slots:
                    return selected
                if slots * group_size - sum(rest) > left:
                    return None
                k = (rest, caps, slots, first, left)
                if k in dead:
                    return None
                options = []
                for index in range(first, len(eligible)):
                    p = eligible[index]
                    if any(caps[i] < n for i, n in p):
                        continue
                    cost = sum(max(0, n - rest[i]) for i, n in p)
                    if cost <= left:
                        options.append((cost, index, p))
                if not options or min(c[0] for c in options) * slots > left:
                    return None
                for cost, index, p in sorted(options):
                    h, c = list(rest), list(caps)
                    for i, n in p:
                        h[i] = max(0, h[i] - n)
                        c[i] -= n
                    found = visit(tuple(h), tuple(c), slots - 1, index, left - cost, selected + (p,))
                    if found is not None:
                        return found
                dead.add(k)
                return None

            for pair in pairs:
                cost = max(0, 2 - hand[pair])
                if cost > limit:
                    continue
                h, c = list(hand), list(capacity)
                h[pair] = max(0, h[pair] - 2)
                c[pair] -= 2
                found = visit(tuple(h), tuple(c), groups, 0, limit - cost, (((pair, 2),),))
                if found is not None:
                    result = [0] * len(hand)
                    for p in found:
                        for i, n in p:
                            result[i] += n
                    return tuple(result)
            return None

        families = [(arithmetic_patterns(), 3 - len(melds), 4, '算术麻将')]
        if not melds:
            families.append((traditional_patterns(), 4, 3, '传统麻将'))
        try:
            for limit in range(lower, min(best, size + 1)):
                for patterns, groups, group_size, name in families:
                    if budget.expired():
                        raise _Interrupted
                    found = witness(patterns, groups, group_size, limit)
                    if found is not None:
                        best, target, family = limit, found, name
                        break
                if best == limit:
                    break
                lower = limit + 1
            lower = best if best < INF else size + 1
            complete = True
        except _Interrupted:
            complete = best == lower
        self.nodes += nodes
        result = DistanceResult(lower, best if best < INF else None, target, family, complete, nodes)
        # Interrupted results are never reused as exact negatives.
        if result.complete and self.cache_size:
            self.cache[key] = result
            self.cache.move_to_end(key)
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        return result

    def ukeire(self, hand, melds=(), unseen=None, *, budget=None):
        hand, melds = tuple(hand), tuple(melds)
        if sum(hand) != (3 - len(melds)) * 4 + 1:
            raise ValueError('Ukeire requires a post-discard hand')
        if unseen is None:
            raise ValueError('Ukeire requires explicit unseen inventory')
        budget = budget or AnalysisBudget()
        base = self.analyze(hand, melds, unseen, budget=budget)
        found, checked = [], []
        if base.complete and base.value is None:
            return Ukeire((), tuple(v for v, n in zip(TILE_VALUES, unseen) if n), True, base)
        if not base.complete:
            return Ukeire((), (), False, base)
        for i, n in enumerate(unseen):
            if not n:
                continue
            if budget.expired():
                break
            h, u = list(hand), list(unseen)
            h[i] += 1
            u[i] -= 1
            if base.value == 1:
                # With fixed capacity and one extra tile, D can only be 0 or 1.
                # Checking the exact shape is sufficient; no target search.
                result = DistanceResult(0, 0, tuple(h), '已成形', True) if possible_shape(tuple(h), tuple(melds)) else DistanceResult(1, 1, base.target, base.family, True)
            else:
                result = self.analyze(tuple(h), melds, tuple(u), budget=budget)
            if not result.complete:
                break
            checked.append(TILE_VALUES[i])
            if result.value is not None and result.value < base.value:
                found.append((TILE_VALUES[i], n, result.value))
        return Ukeire(tuple(found), tuple(checked), len(checked) == sum(bool(n) for n in unseen), base)
