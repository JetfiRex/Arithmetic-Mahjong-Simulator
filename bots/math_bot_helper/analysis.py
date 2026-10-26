"""Structured, budget-aware ready analysis using existing rule/fan contracts."""

from collections import OrderedDict
from dataclasses import dataclass
import time

from simulator.rule_adapter import ArithmeticMahjongRuleAdapter, HandSnapshot, MeldSnapshot, WinContext, WinMethod, WinResult
from bots.math_bot_helper.features import counts_from_values, possible_shape, values_from_counts
from bots.common_bot_helper.observation import TILE_VALUES, TILE_INDEX

SUPPORTED_RULESET = 'arithmetic-mahjong-simplified-v1'
ANALYSIS_VERSION = 'ready-analysis-v1'


@dataclass(frozen=True)
class AnalysisBudget:
    deadline: float | None = None

    def expired(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_ms(self):
        return float('inf') if self.deadline is None else max(0, (self.deadline - time.monotonic()) * 1000)

    @classmethod
    def from_context(cls, context, reserve_ms=30.0):
        remaining = context.remaining_ms()
        reserve = min(reserve_ms, max(2.0, remaining * .15))
        return cls(time.monotonic() + max(0.0, remaining - reserve) / 1000)


@dataclass(frozen=True)
class ReadyTile:
    tile: int | str
    unseen_count: int
    discard: WinResult | None
    self_draw: WinResult | None

    @property
    def legal(self):
        return any(result is not None and result.can_win for result in (self.discard, self.self_draw))


@dataclass(frozen=True)
class ReadyAnalysis:
    tiles: tuple[ReadyTile, ...]
    checked_tiles: tuple[int | str, ...]
    complete: bool
    errors: tuple[str, ...]
    elapsed_ms: float

    @property
    def is_ready(self):
        if any(tile.legal for tile in self.tiles):
            return True
        return False if self.complete else None

    @property
    def live_waits(self):
        return tuple(tile for tile in self.tiles if tile.legal and tile.unseen_count > 0)


def hand_from_observation(observation):
    return HandSnapshot(values_from_counts(observation.hand_counts),
                        tuple(MeldSnapshot(m.group_type, m.tiles) for m in observation.players[0].melds))


def unseen_counts(observation):
    """Current rivers + melds + own concealed hand, never discard history.

    A declared but uncommitted added-kong tile is publicly known once during t4.
    Its source still holds it; exclude that copy from the unknown inventory.
    """
    known = list(observation.hand_counts)
    for player in observation.players:
        for tile in player.discards:
            known[TILE_INDEX[tile]] += 1
        for meld in player.melds:
            for tile in meld.tiles:
                known[TILE_INDEX[tile]] += 1
    if observation.phase == 'WAITING_ROB_KONG' and observation.source not in (None, 0):
        known[TILE_INDEX[observation.target_tile]] += 1
    result = tuple(supply - count for supply, count in zip(observation.tile_supply, known))
    if len(result) != len(TILE_VALUES) or min(result) < 0:
        raise ValueError('Visible inventory exceeds the configured tile supply')
    return result


class ReadyAnalyzer:
    def __init__(self, adapter=None, *, cache_size=4096):
        self.adapter = adapter or ArithmeticMahjongRuleAdapter()
        if cache_size < 0:
            raise ValueError('cache_size must be nonnegative')
        self.cache_size = cache_size
        self.cache = OrderedDict()
        self.oracle_calls = self.cache_hits = self.shape_rejections = 0
        self.max_call_ms = 0.0

    @property
    def screened(self):
        # Never apply built-in shape assumptions to an injected/custom ruleset.
        return type(self.adapter) is ArithmeticMahjongRuleAdapter and self.adapter.ruleset_id == SUPPORTED_RULESET

    def evaluate(self, hand, tile, method):
        """hand is always the pre-win concealed hand, for either method."""
        counts = counts_from_values(hand.hand_tiles)
        key = (self.adapter.ruleset_id, getattr(self.adapter, 'min_fan', None), counts,
               hand.melded_groups, tile, method)
        if self.screened and key in self.cache:
            self.cache_hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        # Canonical concealed order gives stable tie-group presentation and a
        # valid count-based cache key. Legal/fan/payment semantics are unchanged.
        canonical = HandSnapshot(values_from_counts(counts), hand.melded_groups)
        snapshot = canonical
        if method == WinMethod.SELF_DRAW:
            snapshot = HandSnapshot(canonical.hand_tiles + (tile,), hand.melded_groups)
        self.oracle_calls += 1
        started = time.monotonic()
        result = self.adapter.evaluate_win(snapshot, WinContext('self', tile, method,
                                                              'opponent' if method != WinMethod.SELF_DRAW else None))
        self.max_call_ms = max(self.max_call_ms, (time.monotonic() - started) * 1000)
        if self.screened and not result.error and self.cache_size:
            self.cache[key] = result
            self.cache.move_to_end(key)
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        return result

    def analyze(self, hand, *, unseen=None, budget=None):
        """Analyze all 24 hypothetical incoming values; keep zero-stock waits.

        Complete=False explicitly distinguishes interrupted/error analysis from
        a proven negative. Built-in shape screening never decides min-fan or pay.
        """
        start = time.monotonic()
        budget = budget or AnalysisBudget()
        counts = counts_from_values(hand.hand_tiles)
        if len(hand.hand_tiles) != (3 - len(hand.melded_groups)) * 4 + 1:
            raise ValueError('Ready analysis requires a post-discard hand')
        if unseen is None:
            from calculator_base.constants import TILE_COUNTS
            known = list(counts)
            for meld in hand.melded_groups:
                for tile in meld.tiles:
                    known[TILE_INDEX[tile]] += 1
            unseen = tuple(TILE_COUNTS[v] - known[i] for i, v in enumerate(TILE_VALUES))
        unseen = tuple(unseen)
        if len(unseen) != len(TILE_VALUES) or any(type(n) is not int or n < 0 for n in unseen):
            raise ValueError('Invalid unseen counts')
        tiles, checked, errors = [], [], []
        for index, tile in enumerate(TILE_VALUES):
            if budget.expired():
                break
            added = list(counts)
            added[index] += 1
            if self.screened and not possible_shape(tuple(added), hand.melded_groups):
                self.shape_rejections += 1
                checked.append(tile)
                continue
            results = []
            for method in (WinMethod.DISCARD, WinMethod.SELF_DRAW):
                # RuleAdapter is synchronous. Avoid starting another indivisible
                # evaluation when recent call cost no longer fits the budget.
                if budget.expired() or budget.remaining_ms() < max(30, 2 * self.max_call_ms):
                    break
                result = self.evaluate(hand, tile, method)
                results.append(result)
                if result.error:
                    errors.append(f'{tile}/{method.value}: {result.error}')
            if results:
                results += [None] * (2 - len(results))
                if any(r is not None and (r.is_winning_shape or r.error) for r in results):
                    tiles.append(ReadyTile(tile, unseen[index], *results))
            if len(results) != 2 or results[1] is None:
                break
            checked.append(tile)
        return ReadyAnalysis(tuple(tiles), tuple(checked), len(checked) == len(TILE_VALUES) and not errors,
                             tuple(errors), (time.monotonic() - start) * 1000)
