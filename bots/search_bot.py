"""Budgeted distance/ukeire + paired belief rollouts, preserving the P2 policy.

This research policy searches a bounded shortlist of t3 discards. Claims and
other phases retain P2 behavior. Leaf equity is explicitly heuristic, not EV.
"""

from dataclasses import dataclass, field, replace
import math
import random
import statistics
import time

from bots.math_bot_helper.analysis import AnalysisBudget, unseen_counts
from bots.search_bot_helper.belief import BELIEF_VERSION, sample_world
from bots.common_bot_helper.decision import DecisionExplanation, SearchDiagnostics
from bots.search_bot_helper.distance import DistanceAnalyzer
from bots.math_bot_helper.features import subtract_counts
from bots.math_bot import MathBot
from bots.search_bot_helper.rollout import ROLLOUT_VERSION, completion_probability, rollout
from simulator.actions import ActionType
from simulator.rule_adapter import MeldSnapshot

SEARCH_VERSION = 'distance-belief-search-v1'


@dataclass
class SearchBot:
    seed: int = 0
    reserve_ms: float = 45.0
    max_roots: int = 3
    max_worlds: int = 4
    horizon: int = 16
    last_decision: DecisionExplanation | None = field(default=None, init=False, repr=False)
    _base: object = field(default=None, init=False, repr=False)
    _distance: object = field(default=None, init=False, repr=False)
    _rng: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.reserve_ms < 0 or self.max_roots < 1 or self.max_worlds < 0 or self.horizon < 1:
            raise ValueError('Invalid search limits')
        self._base = MathBot(self.seed)
        self._distance = DistanceAnalyzer()
        self._rng = random.Random(self.seed)

    def act(self, observation, legal_actions):
        started = time.monotonic()
        budget = AnalysisBudget.from_context(observation.decision_context, self.reserve_ms)
        remaining = budget.remaining_ms()
        # Non-discard phases get the full P2 budget. Preserve a usable P2 result
        # before research work starts; shortened context cannot refresh deadlines.
        searchable = observation.phase == 'WAITING_TURN_ACTION'
        fraction = .38 if searchable else 1
        base_deadline = min(budget.deadline, started + remaining * fraction / 1000)
        context = replace(observation.decision_context, budget_ms=max(0, (base_deadline - time.monotonic()) * 1000),
                          deadline_monotonic=base_deadline)
        selected = self._base.act(replace(observation, decision_context=context), legal_actions)
        base = self._base.last_decision
        self.last_decision = replace(base, version=SEARCH_VERSION, elapsed_ms=(time.monotonic() - started) * 1000,
                                     note='P2 delegate; ' + base.note)
        if (not searchable or selected.action_type != ActionType.DISCARD or budget.expired()
                or any(s.analysis_state == 'unsupported' for s in base.scores)):
            return selected
        stock = unseen_counts(observation)
        melds = tuple(MeldSnapshot(m.group_type, m.tiles) for m in observation.players[0].melds)
        scores = {s.action_id: s for s in base.scores}
        candidates = [a for a in legal_actions if a.action_type == ActionType.DISCARD and scores[a.action_id].score is not None]
        candidates.sort(key=lambda a: (a == selected, scores[a.action_id].score), reverse=True)
        roots = candidates[:self.max_roots]
        if len(roots) < 2:
            return selected
        before_nodes, before_hits = self._distance.nodes, self._distance.cache_hits
        distances, improvements = {}, {}
        counts = {a.action_id: subtract_counts(observation.hand_counts, (a.tile,)) for a in roots}
        # Distance gets half the remaining search time, fairly split per root.
        distance_end = time.monotonic() + budget.remaining_ms() * .50 / 1000
        for index, a in enumerate(roots):
            now = time.monotonic()
            end = min(budget.deadline, now + max(0, distance_end - now) / (len(roots) - index))
            distances[a.action_id] = self._distance.analyze(counts[a.action_id], melds, stock, budget=AnalysisBudget(end))
        # Partial bounds remain diagnostics. Apply a common distance/ukeire
        # correction only when every compared root has a certified result.
        exact = all(d.complete and d.value is not None for d in distances.values())
        if exact:
            ukeire_end = time.monotonic() + budget.remaining_ms() * .25 / 1000
            for index, a in enumerate(roots):
                now = time.monotonic()
                end = min(budget.deadline, now + max(0, ukeire_end - now) / (len(roots) - index))
                improvements[a.action_id] = self._distance.ukeire(counts[a.action_id], melds, stock, budget=AnalysisBudget(end))
        adjusted = {a.action_id: scores[a.action_id].score for a in roots}
        if exact:
            nearest = min(d.value for d in distances.values())
            for a in roots:
                adjusted[a.action_id] -= 4 * (distances[a.action_id].value - nearest)
            if all(u.complete for u in improvements.values()):
                for a in roots:
                    adjusted[a.action_id] += .2 * math.log1p(improvements[a.action_id].copies)
        # A fixed-wait, finite-draw calculation is a model feature, not a claim
        # about who wins first. It uses SELF-DRAW-legal waits and public counts.
        draws = min(3, observation.wall_remaining // 4, sum(stock))
        for a in roots:
            s = scores[a.action_id]
            if s.analysis_state == 'complete' and s.live_waits:
                p = completion_probability(stock, (v for v, _, _, tsumo in s.live_waits if tsumo), draws)
                adjusted[a.action_id] += p
        samples = {a.action_id: [] for a in roots}
        batches = discarded = terminals = 0
        belief_note = ''
        for _ in range(self.max_worlds):
            # Engine calls are synchronous; leave margin for an indivisible rule
            # evaluation. The outer Arena watchdog still owns the hard deadline.
            if budget.remaining_ms() < 65:
                break
            try:
                world = sample_world(observation, self._rng)
            except ValueError as exc:
                belief_note = str(exc)
                break
            batch = []
            for a in roots:
                if budget.remaining_ms() < 45:
                    break
                value = rollout(observation, world, a, horizon=self.horizon, budget=budget)
                if value is None:
                    break
                batch.append(value)
            if len(batch) != len(roots):
                discarded += 1
                break
            # Never keep extra samples for the root processed first.
            batches += 1
            terminals += sum(r.terminal for r in batch)
            for a, value in zip(roots, batch):
                samples[a.action_id].append(value.value)
        if batches:
            for a in roots:
                adjusted[a.action_id] += 2 * statistics.mean(samples[a.action_id])
        best = max(adjusted.values())
        tied = [a for a in roots if abs(adjusted[a.action_id] - best) < 1e-9]
        chosen = selected if selected in tied else self._rng.choice(tied)
        report = []
        for s in base.scores:
            if s.action_id not in distances:
                report.append(replace(s, score=None, baseline_score=s.score,
                                      note=s.note + '; outside searched P2 shortlist'))
                continue
            d = distances[s.action_id]
            u = improvements.get(s.action_id)
            values = samples[s.action_id]
            report.append(replace(s, score=adjusted[s.action_id], baseline_score=s.score, distance_lower=d.lower, distance_upper=d.upper,
                distance_complete=d.complete, improving_tiles=u.tiles if u else (), ukeire_complete=u.complete if u else False,
                samples=len(values), sample_mean=statistics.mean(values) if values else None,
                sample_variance=statistics.variance(values) if len(values) > 1 else None,
                note=s.note + '; shape distance ignores min-fan; paired truncated potential, not EV'))
        limited = (base.budget_limited or any(not d.complete for d in distances.values())
                   or any(not u.complete for u in improvements.values()) or batches < self.max_worlds)
        diag = SearchDiagnostics(SEARCH_VERSION, BELIEF_VERSION, ROLLOUT_VERSION,
            tuple(a.action_id for a in roots), batches, discarded, terminals, self.horizon,
            self._distance.nodes - before_nodes, self._distance.cache_hits - before_hits, selected.action_id,
            'Uniform unknown allocation; P2 shortlist; HU/claims delegated; truncated potential, not chip EV. ' + belief_note)
        self.last_decision = DecisionExplanation(SEARCH_VERSION, chosen.action_id, tuple(report), limited,
            base.oracle_calls, base.cache_hits, (time.monotonic() - started) * 1000,
            'Distance/ukeire certificates and paired belief rollouts; empirical weights', diag)
        return chosen
