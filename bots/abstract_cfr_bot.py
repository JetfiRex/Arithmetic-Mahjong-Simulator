"""Read-only small-bucket average policy; no training/NumPy/Torch imports."""

from dataclasses import dataclass, field
import math
import random
import time

from bots.cfr_bot_helper.abstract_policy import load_policy

from bots.cfr_bot_helper.cfr_abstraction import DEFAULT_PLAN, PLANS, plan_actions, visible_vectors
from bots.cfr_bot_helper.cfr_policy import STYLES, semantic
from bots.common_bot_helper.decision import ActionScore, DecisionExplanation
from simulator.actions import ActionType

@dataclass
class AbstractCfrBot:
    style: str = 'fast'
    seed: int = 0
    model_path: str = '.data/models/cfr-abstract-v1/policy.json.gz'
    unseen: str = 'rule'
    greedy: bool = False
    reserve_ms: float = 15.
    last_decision: object = field(default=None, init=False, repr=False)
    last_status: str = field(default='', init=False)
    last_key: int | None = field(default=None, init=False)
    _policy: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if (self.style not in STYLES or self.unseen not in ('rule', 'uniform') or type(self.greedy) is not bool
                or not math.isfinite(self.reserve_ms) or self.reserve_ms < 0):
            raise ValueError('Invalid abstract CFR configuration')
        self._rng, self._reason = random.Random(self.seed), 'warmup() has not been called'
        self.reset()

    def __getstate__(self):
        state = dict(self.__dict__)
        state['_policy'] = None
        return state

    def reset(self):
        self._last_request, self._last_choice = None, None
        self.last_key = None

    def warmup(self):
        try:
            self._policy, self._reason = load_policy(self.model_path), ''
        except (OSError, EOFError, ValueError, TypeError, KeyError) as exc:
            self._policy, self._reason = None, f'{type(exc).__name__}: {exc}'

    def _explain(self, action, status, started, note, scores=()):
        self.last_status = status
        self.last_decision = DecisionExplanation('abstract-cfr-bot-v1', action.action_id, tuple(scores),
            status == 'budget-fallback', 0, 0, (time.monotonic() - started) * 1000,
            note=f'ACFR {status}: {note}')
        return action

    def act(self, observation, legal_actions):
        started = time.monotonic()
        if not legal_actions:
            raise ValueError('No legal abstract CFR action')
        hu = next((a for a in legal_actions if a.action_type == ActionType.HU), None)
        if hu or len(legal_actions) == 1:
            self.last_key = None
            return self._explain(hu or legal_actions[0], 'hard-rule' if hu else 'single', started, 'Fixed legal action')
        safe = (next((a for a in legal_actions if a.action_type == ActionType.PASS), None)
                or next((a for a in legal_actions if a.action_type == ActionType.DISCARD
                         and a.tile == observation.drawn_tile), None) or legal_actions[0])
        if observation.decision_context.remaining_ms() <= self.reserve_ms:
            self.reset()
            return self._explain(safe, 'budget-fallback', started, 'No remaining feature/lookup budget')
        planned = plan_actions(observation, legal_actions)
        if observation.decision_context.remaining_ms() <= self.reserve_ms:
            self.reset()
            return self._explain(safe, 'budget-fallback', started, 'Planning consumed remaining budget')
        if len(planned.choices) == 1:
            self.last_key = None
            return self._explain(planned.choices[0], planned.status, started, 'All legal plans select the same action')
        vectors = visible_vectors(observation)
        request = (vectors, observation.phase, observation.source, observation.target_tile,
                   observation.drawn_tile, tuple(sorted(semantic(a) for a in legal_actions)), len(observation.history))
        if request == self._last_request:
            action = planned.choices[self._last_choice]
            return self._explain(action, self.last_status, started, 'Retry: reuse previous plan')
        probabilities, reason, self.last_key = None, self._reason, None
        if self._policy is not None:
            try:
                self.last_key = self._policy.abstraction.key(observation, self.style, vectors)
                node = self._policy.nodes.get(self.last_key)
                if node is not None:
                    probabilities = node[0]
            except ValueError as exc:
                reason = str(exc)
        if observation.decision_context.remaining_ms() <= self.reserve_ms:
            self.reset()
            return self._explain(safe, 'budget-fallback', started, 'Encoding consumed remaining budget')
        if probabilities is not None:
            selected = max(range(len(PLANS)), key=probabilities.__getitem__) if self.greedy else self._rng.choices(
                range(len(PLANS)), weights=probabilities, k=1)[0]
            status = 'average'
        elif self.last_key is not None:
            selected = self._rng.randrange(len(PLANS)) if self.unseen == 'uniform' else DEFAULT_PLAN[self.style]
            status = 'unseen-uniform' if self.unseen == 'uniform' else 'unseen-rule'
        else:
            selected, status = DEFAULT_PLAN[self.style], 'model-fallback'
        self._last_request, self._last_choice = request, selected
        action = planned.choices[selected]
        aggregate = {}
        for candidate, p in zip(planned.choices, probabilities or ()):
            aggregate[candidate.action_id] = aggregate.get(candidate.action_id, 0.) + p
        scores = tuple(ActionScore(aid, p, None, 'abstract-cfr', 'average-plan-probability')
                       for aid, p in aggregate.items())
        note = f'bucket={self.last_key} plan={PLANS[selected]}' if self.last_key is not None else reason
        return self._explain(action, status, started, note, scores)
