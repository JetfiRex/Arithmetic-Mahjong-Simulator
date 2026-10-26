"""Style-conditioned small neural policy; only visible inputs, optional NumPy.

Training is separate. Missing/incompatible artifacts explicitly use StyleBot;
HU and candidate legality remain engine contracts, not learned predictions.
"""

from dataclasses import dataclass, field, replace
import math
import time
from zipfile import BadZipFile

from bots.math_bot_helper.analysis import AnalysisBudget, SUPPORTED_RULESET
from bots.common_bot_helper.decision import ActionScore, DecisionExplanation, NeuralDiagnostics, StyleActionFeatures, StyleDiagnostics
from bots.neural_bot_helper.encoding import FEATURE_VERSION, encode, head_weights, EncodingTimeout
from bots.neural_bot_helper.portable_policy import DEFAULT_MODEL, load_policy
from bots.style_bot_helper.risk import RISK_VERSION
from bots.style_bot_helper.styles import PROFILE_VERSION
from bots.style_bot_helper.targets import TARGET_VERSION
from simulator.actions import ActionType

NEURAL_VERSION = 'style-neural-bot-v1'


@dataclass
class NeuralBot:
    style: str = 'fast'
    seed: int = 0
    containment: float | None = None
    model_path: str = str(DEFAULT_MODEL)
    reserve_ms: float = 20.
    last_decision: object = field(default=None, init=False, repr=False)
    _policy: object = field(default=None, init=False, repr=False)
    _error: str = field(default='', init=False, repr=False)
    _fallback: object = field(default=None, init=False, repr=False)
    _target: object = field(default=None, init=False, repr=False)
    _signature: object = field(default=None, init=False, repr=False)
    _history_size: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        head_weights(self.style, self.containment)
        if not math.isfinite(self.reserve_ms) or self.reserve_ms < 0:
            raise ValueError('Invalid neural reserve')

    def warmup(self):
        """Runner calls before game clocks; direct act() can load on first use."""
        try:
            self._policy = load_policy(self.model_path)
            m = self._policy.metadata
            self._policy.logits([0.] * m['state_dim'], [[0.] * m['action_dim']], head_weights(self.style, self.containment))
            self._error = ''
        except (OSError, ValueError, ImportError, KeyError, BadZipFile, EOFError) as exc:
            self._policy, self._error = None, f'{type(exc).__name__}: {exc}'

    def _diagnostic(self, status, reason='', encoding_ms=0., inference_ms=0.):
        return NeuralDiagnostics(NEURAL_VERSION, FEATURE_VERSION, status,
            self._policy.digest if self._policy else None, self.style, head_weights(self.style, self.containment),
            encoding_ms, inference_ms, reason)

    def _simple(self, actions, selected, started, status, reason, *, limited=False):
        self.last_decision = DecisionExplanation(NEURAL_VERSION, selected.action_id,
            tuple(ActionScore(a.action_id, None, None, '', 'not_started', note=reason) for a in actions),
            limited, 0, 0, (time.monotonic() - started) * 1000, reason,
            neural=self._diagnostic(status, reason))
        return selected

    def _recover(self, observation, actions, started, deadline, reason):
        if self._fallback is None:
            from bots.style_bot import StyleBot
            self._fallback = StyleBot('defensive' if self.style == 'blocking' else self.style, self.seed,
                                      1. if self.style == 'blocking' else self.containment)
        remaining = max(0., (deadline - time.monotonic()) * 1000)
        # Never restart a fresh 500ms budget after spending time encoding/loading.
        context = replace(observation.decision_context, budget_ms=remaining, deadline_monotonic=deadline)
        selected = self._fallback.act(replace(observation, decision_context=context), actions)
        self.last_decision = replace(self._fallback.last_decision,
            elapsed_ms=(time.monotonic() - started) * 1000, neural=self._diagnostic('fallback', reason))
        return selected

    def act(self, observation, legal_actions):
        if not legal_actions:
            raise ValueError('NeuralBot requires legal candidates')
        started = time.monotonic()
        deadline = started + observation.decision_context.remaining_ms() / 1000
        hu = next((a for a in legal_actions if a.action_type == ActionType.HU), None)
        if hu is not None:
            return self._simple(legal_actions, hu, started, 'hard_rule', 'Accept legal HU')
        if len(legal_actions) == 1:
            return self._simple(legal_actions, legal_actions[0], started, 'single', 'Single legal candidate')
        if self._policy is None and not self._error:
            if deadline - time.monotonic() >= .08:
                self.warmup()
            else:
                return self._recover(observation, legal_actions, started, deadline, 'Insufficient budget for cold model loading')
        if self._policy is None:
            return self._recover(observation, legal_actions, started, deadline, self._error)
        m = self._policy.metadata
        if (observation.version != m['contract']['observation'] or observation.ruleset_id != SUPPORTED_RULESET
                or observation.scoring_id != 'NormalScoringAdapter' or observation.min_fan != m.get('min_fan')
                or list(observation.tile_supply) != m.get('tile_supply')):
            return self._recover(observation, legal_actions, started, deadline, 'Observation/rule/scoring/supply outside training contract')
        signature = tuple(e for e in observation.history if e.private and e.kind == 'initial_hand')
        if signature != self._signature or len(observation.history) < self._history_size:
            self._target = None
        self._signature, self._history_size = signature, len(observation.history)
        budget = AnalysisBudget(deadline - min(self.reserve_ms / 1000, max(0., deadline - started) * .15))
        try:
            before = time.monotonic()
            encoded = encode(observation, legal_actions, previous_target=self._target, budget=budget)
            encoding_ms = (time.monotonic() - before) * 1000
            if budget.expired():
                raise EncodingTimeout('No remaining inference budget')
            before = time.monotonic()
            logits = self._policy.logits(encoded.state, encoded.actions, head_weights(self.style, self.containment))
            inference_ms = (time.monotonic() - before) * 1000
            if budget.expired():
                raise EncodingTimeout('Inference exceeded remaining budget')
            maximum = float(logits.max())
            selected = min((a for a, value in zip(legal_actions, logits) if float(value) == maximum),
                           key=lambda a: a.action_id)
            self._target = encoded.target
            target = encoded.target if self.style == 'big' else None
            scores = []
            for a, logit, (progress, danger, feeding, outgoing, missing) in zip(legal_actions, logits, encoded.auxiliary):
                features = StyleActionFeatures(progress, danger, feeding, outgoing, False, 0, 0,
                    target.family if target else None, missing if target else None)
                scores.append(ActionScore(a.action_id, float(logit), progress, 'Learned style preference',
                    'neural', note='Logit, not EV; shape waits do not certify legal ready', style=features))
            c = head_weights(self.style, self.containment)[3] if self.style in ('defensive', 'blocking') else (0. if self.style == 'fast' else .1)
            style = StyleDiagnostics(NEURAL_VERSION, PROFILE_VERSION, RISK_VERSION, TARGET_VERSION,
                'defensive' if self.style == 'blocking' else self.style, c, target.family if target else None,
                target.whole if target else None, 'Visible heuristic target; payment not oracle-verified',
                False, False, encoded.pressure, 'Neural scores do not certify ready or measure offense sacrifice')
            self.last_decision = DecisionExplanation(NEURAL_VERSION, selected.action_id, tuple(scores), False, 0, 0,
                (time.monotonic() - started) * 1000,
                'Frozen neural policy: ' + str(m.get('objective', 'unspecified training objective')), style=style,
                neural=self._diagnostic('model', encoding_ms=encoding_ms, inference_ms=inference_ms))
            return selected
        except (ValueError, KeyError, EncodingTimeout) as exc:
            return self._recover(observation, legal_actions, started, deadline, f'{type(exc).__name__}: {exc}')
