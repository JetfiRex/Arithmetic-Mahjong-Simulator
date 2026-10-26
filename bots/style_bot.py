"""Small playable personalities built from shared, visible-information analysis.

No simulator/UI path is changed. All profiles accept legal HU and keep the
same budget contract. Shape/risk/goal terms are heuristic preferences, not EV.
"""

from dataclasses import dataclass, field, replace
import math
import random
import time

from bots.math_bot_helper.analysis import AnalysisBudget, ReadyAnalyzer, SUPPORTED_RULESET, unseen_counts
from bots.common_bot_helper.decision import ActionScore, DecisionExplanation, StyleActionFeatures, StyleDiagnostics
from bots.math_bot_helper.features import shape_potential, subtract_counts, values_from_counts, possible_shape
from bots.math_bot import MathBot
from bots.common_bot_helper.observation import TILE_VALUES
from bots.style_bot_helper.risk import RISK_VERSION, assess_risk
from bots.style_bot_helper.styles import PROFILE_VERSION, PROFILES
from bots.style_bot_helper.targets import TARGET_VERSION, choose_target, target_missing, fixed_counts
from simulator.actions import ActionType
from simulator.action_options import build_chi_detail_payload
from simulator.rule_adapter import ArithmeticMahjongRuleAdapter, HandSnapshot, MeldSnapshot, WinMethod
from fan_calculator.fan_base import MAX_FAN

STYLE_VERSION = 'beginner-style-bot-v1'
KONGS = (ActionType.MING_GANG, ActionType.AN_GANG, ActionType.JIA_GANG)


@dataclass
class _Position:
    counts: tuple
    melds: tuple
    stock: tuple
    potential: object
    analysis: object = None


@dataclass
class _Plan:
    position: _Position
    discard: int | str | None
    note: str


@dataclass
class StyleBot:
    style: str = 'fast'
    seed: int = 0
    containment: float | None = None
    reserve_ms: float = 40.0
    max_claim_plans: int = 8
    last_decision: DecisionExplanation | None = field(default=None, init=False, repr=False)
    _rng: object = field(default=None, init=False, repr=False)
    _analyzer: object = field(default=None, init=False, repr=False)
    _target: object = field(default=None, init=False, repr=False)
    _signature: object = field(default=None, init=False, repr=False)
    _history_size: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        if self.style not in PROFILES:
            raise ValueError('Unknown beginner style')
        if (self.containment is not None and (not math.isfinite(self.containment) or not 0 <= self.containment <= 1)
                or not math.isfinite(self.reserve_ms) or self.reserve_ms < 0 or self.max_claim_plans < 1):
            raise ValueError('Invalid style limits')
        self._rng = random.Random(self.seed)

    def _profile(self):
        p = PROFILES[self.style]
        return replace(p, containment=self.containment) if self.containment is not None else p

    def _reset_round(self, observation):
        signature = tuple(e for e in observation.history if e.kind == 'initial_hand' and e.private)
        if signature != self._signature or len(observation.history) < self._history_size:
            self._target = None
            self._signature = signature
        self._history_size = len(observation.history)

    @staticmethod
    def _waits(position):
        if position.analysis is None:
            return ()
        return tuple((t.tile, t.unseen_count,
            t.discard.base_payment if t.discard and t.discard.can_win else 0,
            3 * t.self_draw.base_payment if t.self_draw and t.self_draw.can_win else 0)
            for t in position.analysis.live_waits)

    def _grade(self, plan, profile, risk, original_melds, target):
        s = plan.position
        waits = self._waits(s)
        copies = sum(n for _, n, _, _ in waits)
        payments = sum(n * (ron + tsumo / 3) / 2 for _, n, ron, tsumo in waits) / max(1, copies)
        missing = target_missing(target, s.counts, s.melds, s.stock) if target else None
        maximum_payment = max((max(ron, tsumo // 3) for _, _, ron, tsumo in waits), default=0)
        score = profile.progress * s.potential.best
        if copies:
            ready_bonus = profile.ready
            goal_payment = target.payment or 2 ** min(MAX_FAN, target.fan_floor) if target else 0
            if target and missing is not None and maximum_payment < goal_payment:
                ready_bonus *= .3  # a feasible big goal can outweigh cheap readiness
            score += ready_bonus + profile.width * math.log1p(copies) + profile.payment * math.log2(1 + payments)
        if target:
            score += profile.target * (8 - 2 * missing if missing is not None else -3)
        score -= profile.claim_cost * max(0, len(s.melds) - original_melds)
        if plan.discard is None:
            danger = feeding = 0.0
        else:
            tile_risk = risk.for_tile(plan.discard)
            danger, feeding = tile_risk.danger, tile_risk.feeding
        penalty = profile.danger * danger + profile.feeding * profile.containment * feeding
        features = StyleActionFeatures(s.potential.best, danger, feeding, plan.discard, bool(copies), copies,
            maximum_payment,
            target.family if target else None, missing,
            target.payment if target and target.verified_melds == s.melds else None,
            bool(target and target.verified_melds == s.melds))
        return score - penalty, score, features

    def act(self, observation, legal_actions):
        if not legal_actions:
            raise ValueError('StyleBot requires legal candidates')
        started = time.monotonic()
        budget = AnalysisBudget.from_context(observation.decision_context, self.reserve_ms)
        profile = self._profile()
        self._reset_round(observation)
        fallback = next((a for a in legal_actions if a.action_type == ActionType.PASS), None)
        if fallback is None:
            fallback = next((a for a in legal_actions if a.action_type == ActionType.DISCARD and a.tile == observation.drawn_tile), legal_actions[0])
        hu = next((a for a in legal_actions if a.action_type == ActionType.HU), None)
        if hu is not None:
            return self._simple(legal_actions, hu, started, profile, 'Legal HU accepted by every style', ready=True)
        if (observation.ruleset_id != SUPPORTED_RULESET or observation.scoring_id != 'NormalScoringAdapter'
                or observation.min_fan is None):
            return self._simple(legal_actions, fallback, started, profile, 'Unsupported rule/scoring contract', unsupported=True)
        melds = tuple(MeldSnapshot(m.group_type, m.tiles) for m in observation.players[0].melds)
        expected = (3 - len(melds)) * 4 + (2 if observation.phase == 'WAITING_TURN_ACTION' else 1)
        if sum(observation.hand_counts) != expected:
            return self._simple(legal_actions, fallback, started, profile, 'Unsupported ordinary-round hand size', unsupported=True)
        if budget.expired():
            return self._simple(legal_actions, fallback, started, profile, 'No budget; legal fallback', limited=True)
        try:
            stock = unseen_counts(observation)
        except ValueError as exc:
            return self._simple(legal_actions, fallback, started, profile, str(exc), unsupported=True)
        risk = assess_risk(observation, stock)
        if self._analyzer is None or self._analyzer.adapter.min_fan != observation.min_fan:
            self._analyzer = ReadyAnalyzer(ArithmeticMahjongRuleAdapter(min_fan=observation.min_fan))
        analyzer = self._analyzer
        before_calls, before_hits = analyzer.oracle_calls, analyzer.cache_hits
        target_note = 'This profile does not pursue a special target'
        if profile.target:
            self._target, target_note = choose_target(observation.hand_counts, melds, stock,
                observation.wall_remaining, observation.min_fan, self._target)
            if (self._target and self._target.verified_melds != melds
                    and budget.remaining_ms() >= max(90, 2 * analyzer.max_call_ms)):
                fixed = fixed_counts(melds)
                goal = tuple(w - m for w, m in zip(self._target.whole, fixed))
                tile = next(TILE_VALUES[i] for i, n in enumerate(goal) if n)
                before = subtract_counts(goal, (tile,))
                win = analyzer.evaluate(HandSnapshot(values_from_counts(before), melds), tile, WinMethod.DISCARD)
                if win.can_win:
                    self._target = replace(self._target, payment=win.base_payment, verified_melds=melds)
                elif not win.error:
                    self._target = None
                    target_note = 'Catalogue target rejected by rule oracle'
        target = self._target
        positions, roots, notes = {}, {a.action_id: [] for a in legal_actions}, {}
        capped = False

        def plan(counts, new_melds, discard, note, available=stock):
            key = counts, new_melds, available
            if key not in positions:
                positions[key] = _Position(counts, new_melds, available, shape_potential(counts, new_melds, available))
            return _Plan(positions[key], discard, note)

        def after_claim(counts, new_melds, note):
            plans = []
            for i, n in enumerate(counts):
                if budget.expired():
                    break
                if n:
                    plans.append(plan(subtract_counts(counts, (TILE_VALUES[i],)), new_melds, TILE_VALUES[i], note))
            return plans

        for action in legal_actions:
            if budget.expired():
                break
            if action.action_type == ActionType.DISCARD:
                roots[action.action_id].append(plan(subtract_counts(observation.hand_counts, (action.tile,)), melds, action.tile, 'Direct discard'))
            elif action.action_type == ActionType.PASS:
                roots[action.action_id].append(plan(observation.hand_counts, melds, None, 'Keep hand; no immediate discard'))
        for action in legal_actions:
            if budget.expired():
                break
            plans = []
            if action.action_type == ActionType.CHI:
                selections = (action.required_values,) if action.required_values else MathBot._chi_values(observation.hand_counts, observation.target_tile)
                for required in selections:
                    if budget.expired():
                        break
                    payload = build_chi_detail_payload(list(required), observation.target_tile)
                    new_melds = melds + (MeldSnapshot('吃', tuple(payload['display_tiles'])),)
                    plans.extend(after_claim(subtract_counts(observation.hand_counts, required), new_melds, f'CHI {required}; hypothetical next discard'))
            elif action.action_type == ActionType.PENG:
                tile = observation.target_tile
                new_melds = melds + (MeldSnapshot('碰', (tile,) * 4),)
                plans = after_claim(subtract_counts(observation.hand_counts, (tile,) * 3), new_melds, 'PENG; hypothetical next discard')
            if plans:
                plans.sort(key=lambda p: self._grade(p, profile, risk, len(melds), target)[0], reverse=True)
                capped |= len(plans) > self.max_claim_plans
                roots[action.action_id].extend(plans[:self.max_claim_plans])
                notes[action.action_id] = f'Best {min(len(plans), self.max_claim_plans)}/{len(plans)} claim/discard plans'
        relevant = {id(p.position): p for group in roots.values() for p in group}
        ordered = sorted(relevant.values(), key=lambda p: self._grade(p, profile, risk, len(melds), target)[0], reverse=True)
        refinement = AnalysisBudget(min(budget.deadline, time.monotonic() + budget.remaining_ms() * .78 / 1000))
        for p in ordered:
            if refinement.expired():
                break
            s = p.position
            s.analysis = analyzer.analyze(HandSnapshot(values_from_counts(s.counts), s.melds), unseen=s.stock, budget=refinement)
            if not s.analysis.complete:
                break
        scores, offense = [], {}
        for action in legal_actions:
            if roots[action.action_id]:
                best = max(roots[action.action_id], key=lambda p: self._grade(p, profile, risk, len(melds), target)[0])
                value, attack, features = self._grade(best, profile, risk, len(melds), target)
                s = best.position
                state = 'not_started' if s.analysis is None else ('complete' if s.analysis.complete else 'partial')
                scores.append(ActionScore(action.action_id, value, s.potential.best, s.potential.family, state,
                    self._waits(s), best.note + '; ' + notes.get(action.action_id, ''), style=features))
                offense[action.action_id] = attack
            elif action.action_type in KONGS and not budget.expired():
                result = self._kong(action, observation, melds, stock, analyzer, budget, profile, risk, target, plan)
                if result is not None:
                    value, features = result
                    scores.append(ActionScore(action.action_id, value, features.progress, 'Replacement mixture', 'heuristic_only',
                        note='All unseen replacement values averaged; root risk held fixed; future waits omitted', style=features))
                    offense[action.action_id] = value + profile.danger * features.danger_index + profile.feeding * profile.containment * features.feeding_index
                    continue
                scores.append(ActionScore(action.action_id, None, None, '', 'not_started', note='Incomplete replacement mixture; excluded'))
            else:
                scores.append(ActionScore(action.action_id, None, None, '', 'not_started', note='Budget exhausted'))
        usable = [s for s in scores if s.score is not None]
        if usable:
            maximum = max(s.score for s in usable)
            tied = [s.action_id for s in usable if abs(s.score - maximum) < 1e-9]
            selected_id = fallback.action_id if fallback.action_id in tied else self._rng.choice(tied)
            selected = next(a for a in legal_actions if a.action_id == selected_id)
        else:
            selected = fallback
        selected_score = next(s for s in scores if s.action_id == selected.action_id)
        attack_id = max(offense, key=offense.get) if offense else selected.action_id
        attack_score = next(s for s in scores if s.action_id == attack_id)
        folded = bool(selected.action_type == ActionType.DISCARD and selected_score.style and attack_score.style
            and attack_id != selected.action_id and offense[attack_id] - offense[selected.action_id] > .25
            and selected_score.style.danger_index < attack_score.style.danger_index)
        limited = (budget.expired() or any(p.position.analysis is None or not p.position.analysis.complete for p in relevant.values())
                   or any(s.score is None for s in scores))
        diag = StyleDiagnostics(STYLE_VERSION, PROFILE_VERSION, RISK_VERSION, TARGET_VERSION,
            profile.name, profile.containment, target.family if target else None, target.whole if target else None,
            target_note, bool(selected_score.style and selected_score.style.known_ready), folded, risk.pressure,
            'Visible heuristics; danger is not a deal-in probability; ' + ('claim plans capped' if capped else 'all generated claim plans retained'))
        self.last_decision = DecisionExplanation(STYLE_VERSION, selected.action_id, tuple(scores), limited,
            analyzer.oracle_calls - before_calls, analyzer.cache_hits - before_hits, (time.monotonic() - started) * 1000,
            'Beginner style preferences; no calibrated EV or guaranteed safety', style=diag)
        return selected

    def _simple(self, actions, selected, started, profile, note, *, ready=False, limited=False, unsupported=False):
        scores = tuple(ActionScore(a.action_id, None, None, '', 'unsupported' if unsupported else 'not_started', note=note) for a in actions)
        diag = StyleDiagnostics(STYLE_VERSION, PROFILE_VERSION, RISK_VERSION, TARGET_VERSION, profile.name,
            profile.containment, None, None, note, ready, False, 0)
        self.last_decision = DecisionExplanation(STYLE_VERSION, selected.action_id, scores, limited, 0, 0,
            (time.monotonic() - started) * 1000, note, style=diag)
        return selected

    def _kong(self, action, observation, melds, stock, analyzer, budget, profile, risk, target, make_plan):
        tile = observation.target_tile if action.action_type == ActionType.MING_GANG else action.tile
        n = {ActionType.MING_GANG: 4, ActionType.AN_GANG: 5, ActionType.JIA_GANG: 1}[action.action_type]
        counts = subtract_counts(observation.hand_counts, (tile,) * n)
        if action.action_type == ActionType.JIA_GANG:
            new_melds = list(melds)
            new_melds[action.meld_index] = MeldSnapshot('加杠', (tile,) * 5)
            new_melds = tuple(new_melds)
        else:
            new_melds = melds + (MeldSnapshot('明杠' if action.action_type == ActionType.MING_GANG else '暗杠', (tile,) * 5),)
        total = sum(stock)
        if not total:
            return None
        value = progress = danger = feeding = 0.0
        for i, copies in enumerate(stock):
            if not copies:
                continue
            if budget.expired():
                return None
            drawn, remaining = list(counts), list(stock)
            drawn[i] += 1
            remaining[i] -= 1
            plans = []
            for j, count in enumerate(drawn):
                if budget.expired():
                    return None
                if count:
                    plans.append(make_plan(subtract_counts(tuple(drawn), (TILE_VALUES[j],)), new_melds,
                                           TILE_VALUES[j], 'Replacement branch', tuple(remaining)))
            best = max((self._grade(p, profile, risk, len(melds), target) for p in plans), key=lambda row: row[0])
            score, _, features = best
            if possible_shape(tuple(drawn), new_melds):
                if budget.remaining_ms() < max(40, 2 * analyzer.max_call_ms):
                    return None
                win = analyzer.evaluate(HandSnapshot(values_from_counts(counts), new_melds), TILE_VALUES[i], WinMethod.SELF_DRAW)
                if win.error:
                    return None
                if win.can_win:
                    score = max(score, 40 + profile.payment * math.log2(1 + 3 * win.base_payment))
            weight = copies / total
            value += weight * score
            progress += weight * features.progress
            danger += weight * features.danger_index
            feeding += weight * features.feeding_index
        if action.action_type == ActionType.JIA_GANG:
            danger += risk.for_tile(tile).danger
            value -= profile.danger * risk.for_tile(tile).danger
        features = StyleActionFeatures(progress, danger, feeding, None, False, 0, 0,
            target.family if target else None, None)
        return value, features
