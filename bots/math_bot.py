"""First mathematical policy: shape potentials + exact, budgeted ready checks.

This is a transparent heuristic policy, not a calibrated four-player EV solver.
It consumes only Observation/candidates and leaves human execution untouched.
"""

from dataclasses import dataclass, field
from itertools import combinations
import math
import random
import time

from simulator.actions import ActionType
from simulator.action_options import build_chi_detail_payload, is_valid_chi_selection
from simulator.rule_adapter import ArithmeticMahjongRuleAdapter, HandSnapshot, MeldSnapshot, WinMethod
from bots.math_bot_helper.analysis import AnalysisBudget, ReadyAnalyzer, SUPPORTED_RULESET, unseen_counts
from bots.common_bot_helper.decision import ActionScore, DecisionExplanation
from bots.math_bot_helper.features import counts_from_values, possible_shape, shape_potential, subtract_counts, values_from_counts
from bots.common_bot_helper.observation import TILE_VALUES, TILE_INDEX

MATH_VERSION = 'math-bot-v1'


@dataclass
class _State:
    counts: tuple[int, ...]
    melds: tuple[MeldSnapshot, ...]
    unseen: tuple[int, ...]
    potential: object
    note: str
    analysis: object = None

    @property
    def live_waits(self):
        if self.analysis is None:
            return ()
        return tuple((t.tile, t.unseen_count,
                      t.discard.base_payment if t.discard and t.discard.can_win else 0,
                      3 * t.self_draw.base_payment if t.self_draw and t.self_draw.can_win else 0)
                     for t in self.analysis.live_waits)

    @property
    def score(self):
        waits = self.live_waits
        if not waits:
            return self.potential.best
        # Availability/payment proxy, deliberately not described as full EV.
        weighted = sum(n * (ron + tsumo) / 2 for _, n, ron, tsumo in waits) / max(1, sum(self.unseen))
        return self.potential.best + 20 + math.log2(1 + weighted) + .01 * sum(w[1] for w in waits)

    @property
    def status(self):
        return 'not_started' if self.analysis is None else ('complete' if self.analysis.complete else 'partial')


@dataclass
class MathBot:
    seed: int = 0
    reserve_ms: float = 30.0
    max_claim_plans: int = 8
    last_decision: DecisionExplanation | None = field(default=None, init=False, repr=False)
    _analyzer: object = field(default=None, init=False, repr=False)
    _rng: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.reserve_ms < 0 or self.max_claim_plans < 1:
            raise ValueError('Invalid mathematical policy limits')
        self._rng = random.Random(self.seed)

    def act(self, observation, legal_actions):
        if not legal_actions:
            raise ValueError('MathBot requires legal candidates')
        started = time.monotonic()
        budget = AnalysisBudget.from_context(observation.decision_context, self.reserve_ms)
        fallback = next((a for a in legal_actions if a.action_type == ActionType.PASS), None)
        if fallback is None:
            fallback = next((a for a in legal_actions if a.action_type == ActionType.DISCARD
                             and a.tile == observation.drawn_tile), legal_actions[0])
        hu = next((a for a in legal_actions if a.action_type == ActionType.HU), None)
        if hu:
            self.last_decision = DecisionExplanation(MATH_VERSION, hu.action_id,
                tuple(ActionScore(a.action_id, None, None, '', 'not_started', note='Legal HU accepted' if a == hu else 'HU available')
                      for a in legal_actions), False, 0, 0, (time.monotonic() - started) * 1000, 'Accept legal HU')
            return hu
        if (observation.ruleset_id != SUPPORTED_RULESET or observation.min_fan is None
                or observation.scoring_id != 'NormalScoringAdapter'):
            return self._unsupported(legal_actions, fallback, started, 'Unsupported rule/scoring contract')
        expected = (3 - len(observation.players[0].melds)) * 4 + (2 if observation.phase == 'WAITING_TURN_ACTION' else 1)
        if sum(observation.hand_counts) != expected:
            return self._unsupported(legal_actions, fallback, started, 'Hand size is outside the ordinary-round analysis contract')
        try:
            unseen = unseen_counts(observation)
        except ValueError as exc:
            return self._unsupported(legal_actions, fallback, started, str(exc))
        if self._analyzer is None or self._analyzer.adapter.min_fan != observation.min_fan:
            self._analyzer = ReadyAnalyzer(ArithmeticMahjongRuleAdapter(min_fan=observation.min_fan))
        analyzer = self._analyzer
        before_calls, before_hits = analyzer.oracle_calls, analyzer.cache_hits
        melds = tuple(MeldSnapshot(m.group_type, m.tiles) for m in observation.players[0].melds)
        states = {}
        roots = {a.action_id: [] for a in legal_actions}
        notes = {a.action_id: '' for a in legal_actions}
        gang_scores = {}
        truncated = False

        def state(counts, new_melds, stock, note):
            key = (counts, new_melds, stock)
            if key not in states:
                states[key] = _State(counts, new_melds, stock, shape_potential(counts, new_melds, stock), note)
            return states[key]

        def after_claim(counts, new_melds, note):
            result = []
            for i, n in enumerate(counts):
                if not n or budget.expired():
                    continue
                rest = list(counts)
                rest[i] -= 1
                result.append(state(tuple(rest), new_melds, unseen, f'{note}; discard {TILE_VALUES[i]}'))
            return result

        # Cheap direct/pass states first: there is always a usable incumbent
        # before considering optional claims and kong expectations.
        for a in legal_actions:
            if budget.expired():
                break
            if a.action_type == ActionType.DISCARD:
                counts = subtract_counts(observation.hand_counts, (a.tile,))
                roots[a.action_id].append(state(counts, melds, unseen, f'discard {a.tile}'))
            elif a.action_type == ActionType.PASS:
                roots[a.action_id].append(state(observation.hand_counts, melds, unseen, 'Keep concealed hand'))

        for a in legal_actions:
            if budget.expired():
                break
            if a.action_type == ActionType.CHI:
                selections = (a.required_values,) if a.required_values else self._chi_values(observation.hand_counts, observation.target_tile)
                plans = []
                for required in selections:
                    if budget.expired():
                        break
                    payload = build_chi_detail_payload(list(required), observation.target_tile)
                    new_melds = melds + (MeldSnapshot('吃', tuple(payload['display_tiles'])),)
                    plans.extend(after_claim(subtract_counts(observation.hand_counts, required), new_melds, f'chi {required}'))
                plans.sort(key=lambda s: s.potential.best, reverse=True)
                truncated |= len(plans) > self.max_claim_plans
                roots[a.action_id].extend(plans[:self.max_claim_plans])
                if len(plans) > self.max_claim_plans:
                    notes[a.action_id] = f'Top {self.max_claim_plans}/{len(plans)} claim/discard plans refined'
            elif a.action_type == ActionType.PENG:
                target = observation.target_tile
                new_melds = melds + (MeldSnapshot('碰', (target,) * 4),)
                roots[a.action_id].extend(after_claim(subtract_counts(observation.hand_counts, (target,) * 3), new_melds, 'peng'))

        # Refine the strongest structural candidates first. Interruption leaves
        # untouched states explicitly not_started, never "not ready".
        relevant = {id(s): s for group in roots.values() for s in group}
        ordered = sorted(relevant.values(), key=lambda s: s.potential.best, reverse=True)
        for s in ordered:
            if budget.expired():
                break
            s.analysis = analyzer.analyze(HandSnapshot(values_from_counts(s.counts), s.melds), unseen=s.unseen, budget=budget)
            if not s.analysis.complete:
                break

        # A kong includes an unknown replacement draw. Average every possible
        # value under the explicit exchangeable-unknowns model; never cherry-pick
        # one lucky replacement. An interrupted average is not used as a gain.
        for a in legal_actions:
            if a.action_type not in (ActionType.MING_GANG, ActionType.AN_GANG, ActionType.JIA_GANG):
                continue
            if budget.expired():
                notes[a.action_id] = 'Replacement expectation not started: budget'
                continue
            result = self._kong(a, observation, melds, unseen, analyzer, budget)
            if result is not None:
                gang_scores[a.action_id] = result
            else:
                notes[a.action_id] = 'Incomplete replacement expectation; not used as a gain'

        scored = []
        for a in legal_actions:
            if roots[a.action_id]:
                best = max(roots[a.action_id], key=lambda s: s.score)
                # Small fixed opening cost: an unverified tie keeps the hand.
                cost = .15 if a.action_type in (ActionType.CHI, ActionType.PENG) else 0
                scored.append(ActionScore(a.action_id, best.score - cost, best.potential.best, best.potential.family,
                                          best.status, best.live_waits, best.note + ('; ' + notes[a.action_id] if notes[a.action_id] else '')))
            elif a.action_id in gang_scores:
                value, note = gang_scores[a.action_id]
                scored.append(ActionScore(a.action_id, value, value, '算术麻将', 'heuristic_only', note=note))
            else:
                scored.append(ActionScore(a.action_id, None, None, '', 'not_started', note=notes[a.action_id] or 'Budget exhausted'))
        usable = [s for s in scored if s.score is not None]
        if usable:
            best_score = max(s.score for s in usable)
            tied = [s.action_id for s in usable if abs(s.score - best_score) < 1e-9]
            selected_id = fallback.action_id if fallback.action_id in tied else self._rng.choice(tied)
            selected = next(a for a in legal_actions if a.action_id == selected_id)
        else:
            selected = fallback
        limited = (budget.expired() or any(s.analysis is None or not s.analysis.complete for s in relevant.values())
                   or any(s.analysis_state == 'not_started' for s in scored))
        self.last_decision = DecisionExplanation(
            MATH_VERSION, selected.action_id, tuple(scored), limited,
            analyzer.oracle_calls - before_calls, analyzer.cache_hits - before_hits,
            (time.monotonic() - started) * 1000,
            'Heuristic ranking; not full EV' + ('; claim plans capped' if truncated else ''),
        )
        return selected

    def _unsupported(self, legal_actions, fallback, started, note):
        self.last_decision = DecisionExplanation(MATH_VERSION, fallback.action_id,
            tuple(ActionScore(a.action_id, None, None, '', 'unsupported', note=note) for a in legal_actions),
            False, 0, 0, (time.monotonic() - started) * 1000, note)
        return fallback

    @staticmethod
    def _chi_values(counts, target):
        seen = set()
        for values in combinations(values_from_counts(counts), 3):
            if values not in seen:
                seen.add(values)
                if is_valid_chi_selection(list(values), target):
                    yield values

    def _kong(self, action, observation, melds, unseen, analyzer, budget):
        target = observation.target_tile if action.action_type == ActionType.MING_GANG else action.tile
        consume = {ActionType.MING_GANG: 4, ActionType.AN_GANG: 5, ActionType.JIA_GANG: 1}[action.action_type]
        counts = subtract_counts(observation.hand_counts, (target,) * consume)
        if action.action_type == ActionType.JIA_GANG:
            new_melds = list(melds)
            new_melds[action.meld_index] = MeldSnapshot('加杠', (target,) * 5)
            new_melds = tuple(new_melds)
        else:
            kind = '明杠' if action.action_type == ActionType.MING_GANG else '暗杠'
            new_melds = melds + (MeldSnapshot(kind, (target,) * 5),)
        total = sum(unseen)
        if not total:
            return None
        expectation = 0.0
        for i, n in enumerate(unseen):
            if not n:
                continue
            if budget.expired():
                return None
            drawn = list(counts)
            drawn[i] += 1
            stock = list(unseen)
            stock[i] -= 1
            best = 0.0
            for j, copies in enumerate(drawn):
                if not copies:
                    continue
                if budget.expired():
                    return None
                after = drawn.copy()
                after[j] -= 1
                best = max(best, shape_potential(tuple(after), new_melds, tuple(stock)).best)
            # Exact immediate replacement wins are a bonus, evaluated only when
            # enough time remains for the synchronous rule oracle.
            if possible_shape(tuple(drawn), new_melds):
                if budget.remaining_ms() < max(30, 2 * analyzer.max_call_ms):
                    return None
                win = analyzer.evaluate(HandSnapshot(values_from_counts(counts), new_melds), TILE_VALUES[i], WinMethod.SELF_DRAW)
                if win.error:
                    return None
                if win.can_win:
                    best = max(best, 40 + math.log2(1 + 3 * win.base_payment))
            expectation += n / total * best
        penalty = .35 if action.action_type == ActionType.JIA_GANG else .15
        return expectation - penalty, 'All unseen replacement values averaged; future ready bonus omitted; fixed kong/rob-risk penalty'
