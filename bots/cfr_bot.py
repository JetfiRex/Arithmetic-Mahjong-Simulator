"""Frozen MCCFR average-strategy lookup, with an explicit unseen-state fallback.

Construct directly or use training.cfr.play. Existing policy registries remain
unchanged while PPO is running. Call warmup before a live decision clock.
"""

from dataclasses import dataclass, field, replace
import math
import random
import time

from bots.cfr_bot_helper.cfr_policy import Recall, STYLES, load_policy, rule_contract, semantic
from bots.common_bot_helper.decision import ActionScore, DecisionExplanation
from simulator.actions import ActionType


@dataclass
class CfrBot:
    style: str = 'fast'
    seat: int = 0
    seed: int = 0
    model_path: str = '.data/models/cfr-mahjong/policy.json.gz'
    unseen: str = 'style'
    greedy: bool = False
    reserve_ms: float = 20.
    last_decision: object = field(default=None, init=False, repr=False)
    last_status: str = field(default='', init=False)
    _policy: object = field(default=None, init=False, repr=False)
    _reason: str = field(default='warmup() has not been called', init=False, repr=False)

    def __post_init__(self):
        if (self.style not in STYLES or type(self.seat) is not int or self.seat not in range(4)
                or self.unseen not in ('style', 'uniform') or type(self.greedy) is not bool
                or not math.isfinite(self.reserve_ms) or self.reserve_ms < 0):
            raise ValueError('Invalid CFR bot configuration')
        self._recall, self._rng, self._fallback = Recall(), random.Random(self.seed), None

    def __getstate__(self):
        state = dict(self.__dict__)
        state['_policy'] = None  # workers warm their own read-only model before clocks
        return state

    def reset(self):
        self._recall.reset()

    def warmup(self):
        try:
            self._policy = load_policy(self.model_path)
            self._reason = ''
        except (OSError, EOFError, ValueError, TypeError, KeyError) as exc:
            self._policy, self._reason = None, f'{type(exc).__name__}: {exc}'
        from bots.style_bot import StyleBot
        self._fallback = StyleBot('defensive' if self.style == 'blocking' else self.style, seed=self.seed,
            containment=0. if self.style == 'defensive' else 1. if self.style == 'blocking' else None)

    def _explain(self, selected, status, started, note, scores=()):
        self.last_status = status
        self.last_decision = DecisionExplanation('cfr-bot-v1', selected.action_id, tuple(scores),
            status == 'budget-fallback', 0, 0, (time.monotonic() - started) * 1000,
            note=f'CFR {status}: {note}')
        return selected

    def _safe(self, observation, legal_actions):
        return (next((a for a in legal_actions if a.action_type == ActionType.HU), None)
                or next((a for a in legal_actions if a.action_type == ActionType.PASS), None)
                or next((a for a in legal_actions if a.action_type == ActionType.DISCARD
                         and a.tile == observation.drawn_tile), None) or legal_actions[0])

    def act(self, observation, legal_actions):
        started = time.monotonic()
        if not legal_actions:
            raise ValueError('No legal CFR action')
        if observation.decision_context.remaining_ms() <= self.reserve_ms:
            self._recall.reset()
            return self._explain(self._safe(observation, legal_actions), 'budget-fallback', started,
                                 'No new lookup/search budget; discard unmatched private recall')
        encoded = self._recall.project(observation, legal_actions, style=self.style, seat=self.seat)
        if self._recall.last is not None and self._recall.last.base == encoded.base:
            selected = encoded.actions[encoded.tokens.index(self._recall.last_action)]
            return self._explain(selected, self.last_status, started, 'Repeated visible request; reuse choice')
        hu = next((a for a in encoded.actions if a.action_type == ActionType.HU), None)
        if hu or len(encoded.actions) == 1:
            selected = hu or encoded.actions[0]
            status, probabilities, reason = 'hard-rule' if hu else 'single', None, 'Fixed legal action'
        else:
            probabilities, reason = None, self._reason
            if self._policy is not None:
                if self._policy.metadata['rules'] != rule_contract(observation):
                    reason = 'Rule/supply/scoring contract differs from training'
                else:
                    node = self._policy.lookup(encoded, self.seat)
                    if node is not None:
                        probabilities = node.probabilities
                    else:
                        reason = 'Unseen exact recalled information set'
            if probabilities is not None:
                index = max(range(len(probabilities)), key=probabilities.__getitem__) if self.greedy else (
                    self._rng.choices(range(len(probabilities)), weights=probabilities, k=1)[0])
                selected, status = encoded.actions[index], 'average'
            elif self.unseen == 'uniform' and self._policy is not None and self._policy.metadata['rules'] == rule_contract(observation):
                probabilities = (1 / len(encoded.actions),) * len(encoded.actions)
                selected, status = self._rng.choice(encoded.actions), 'unseen-uniform'
            elif self._fallback is not None:
                elapsed = (time.monotonic() - started) * 1000
                context = observation.decision_context
                deadline = min(context.deadline_monotonic or math.inf, started + context.budget_ms / 1000)
                projected = replace(observation, decision_context=replace(context,
                    budget_ms=max(0., context.budget_ms - elapsed), deadline_monotonic=deadline))
                selected, status = self._fallback.act(projected, legal_actions), 'unseen-style'
            else:
                selected, status = self._safe(observation, legal_actions), 'unprepared-fallback'
        self._recall.commit(encoded, semantic(selected))
        scores = tuple(ActionScore(a.action_id, p, None, 'cfr', 'average-probability',
            note='Average-strategy probability, not payoff or win probability')
            for a, p in zip(encoded.actions, probabilities or ()))
        if len(scores) > 128:
            chosen_score = next(s for s in scores if s.action_id == selected.action_id)
            scores = tuple(s for s in scores if s.action_id != selected.action_id)[:127] + (chosen_score,)
        return self._explain(selected, status, started,
            'Frozen average strategy' if status == 'average' else reason, scores)
