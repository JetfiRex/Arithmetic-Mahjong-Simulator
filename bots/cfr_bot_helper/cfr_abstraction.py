"""Small, frozen visible-state codebooks and deterministic legal action plans.

No distance search, wall clock, absolute seat or private opponent state enters
the encoder. Structural scores are overlapping-template heuristics, not shanten,
fan guarantees or deal-in probabilities. Runtime uses only the standard library.
"""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math

from bots.math_bot_helper.analysis import unseen_counts
from bots.cfr_bot_helper.cfr_policy import CONTRACT as BASE_CONTRACT, STYLES, rule_contract, semantic
from bots.math_bot_helper.features import arithmetic_patterns
from bots.common_bot_helper.observation import TILE_INDEX, TILE_VALUES
from simulator.actions import ActionType

V1_VERSION = 'visible-codebooks-legal-plans-v1'
VERSION = 'visible-codebooks-legal-plans-v2'
CONTRACT = dict(BASE_CONTRACT, information=VERSION)
_V1_CONTRACT = dict(BASE_CONTRACT, information=V1_VERSION)
PHASES = ('WAITING_TURN_ACTION', 'WAITING_RESPONSE', 'WAITING_ACTION_DETAIL', 'WAITING_ROB_KONG')
PLANS = ('progress', 'value', 'safe', 'contain', 'balanced')
WEIGHTS = ((1., .08, .15, .05, .10), (.55, 1.1, .25, .10, .35),
           (.45, .05, 4., .4, .7), (.5, .05, 1.8, 4., .6), (.85, .25, 1.2, .8, .25))
DEFAULT_PLAN = dict(fast=0, big=1, defensive=2, blocking=3)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _counts(values):
    c = Counter(values)
    return tuple(c[v] for v in TILE_VALUES)


def _remove(hand, values):
    result = list(hand)
    for tile in values:
        result[TILE_INDEX[tile]] -= 1
    if min(result) < 0:
        raise ValueError('Abstract action consumes unavailable tiles')
    return tuple(result)


@lru_cache(maxsize=32768)
def structural(hand, fixed, *, distinct_pairs=True):
    """One fixed scan of templates, with explicitly optimistic overlap."""
    matches = [0] * 5
    for pattern in arithmetic_patterns():
        n = sum(min(hand[i], need) for i, need in pattern)
        matches[n] += 1
    # v1 is retained only to encode observations for existing frozen codebooks.
    # Action plans and newly fitted codebooks use the distinct-pair rule.
    pairs = sum(n >= 2 for n in hand) if distinct_pairs else sum(n // 2 for n in hand)
    slots = max(0, 3 - fixed)
    progress = (4 * fixed + 2 * min(1, pairs) + 3.5 * min(slots, matches[4])
                + 1.1 * min(slots, matches[3]) + .2 * math.log1p(matches[2]))
    numbers = [(v, hand[i]) for i, v in enumerate(TILE_VALUES) if isinstance(v, int)]
    category = max((sum(n for v, n in numbers if predicate(v)) for predicate in (
        lambda v: v % 2 == 0, lambda v: v % 2 == 1,
        lambda v: v in (2, 3, 5, 7, 11, 13, 17, 19), lambda v: v in (1, 2, 4, 8, 16))), default=0)
    value = max(2 * pairs, category * .7) + .3 * sum(n >= 3 for n in hand)
    return progress, value, min(3, matches[4]), min(3, matches[3]), min(7, pairs)


def visible_vectors(observation, *, version=VERSION):
    """Counts retain multiplicity. Meld order and old discard order are ignored.

    Only opponents' most recent actual discards are read from history, including
    claimed discards. They are not counted again in the public inventory.
    """
    if version not in (V1_VERSION, VERSION):
        raise ValueError('Unsupported abstract feature version')
    hand = observation.hand_counts
    fixed = _counts(v for m in observation.players[0].melds for v in m.tiles)
    own_melds = len(observation.players[0].melds)
    shape = structural(hand, own_melds, distinct_pairs=version != V1_VERSION)
    own = tuple(n / max(1, cap) for n, cap in zip(hand, observation.tile_supply))
    own += tuple(n / max(1, cap) for n, cap in zip(fixed, observation.tile_supply))
    own += (own_melds / 3, sum(hand) / 14, shape[0] / 16, shape[1] / 14,
            shape[2] / 3, shape[3] / 3, shape[4] / 7)
    public = []
    for p in observation.players:
        river = _counts(p.discards)
        melds = _counts(v for m in p.melds for v in m.tiles)
        public.extend(n / max(1, cap) for n, cap in zip(river, observation.tile_supply))
        public.extend(n / max(1, cap) for n, cap in zip(melds, observation.tile_supply))
        public.extend((len(p.melds) / 3, p.hand_count / 14))
    recent = {}
    for event in reversed(observation.history):
        if event.kind == 'discard' and event.actor in (1, 2, 3) and event.actor not in recent:
            recent[event.actor] = event.tile
            if len(recent) == 3:
                break
    for actor in (1, 2, 3):
        index = TILE_INDEX[recent[actor]] if actor in recent else len(TILE_VALUES)
        public.extend(float(i == index) for i in range(len(TILE_VALUES) + 1))
    return own, tuple(public)


def _centroid(book, vector):
    if len(vector) != len(book['mean']):
        raise ValueError('Abstract feature width changed')
    scaled = tuple(max(-6., min(6., (v - m) / s)) for v, m, s in zip(vector, book['mean'], book['scale']))
    return min(range(len(book['centers'])),
               key=lambda i: sum((v - c) ** 2 for v, c in zip(scaled, book['centers'][i])))


class Abstraction:
    def __init__(self, value):
        version = value.get('version')
        expected_contract = _V1_CONTRACT if version == V1_VERSION else CONTRACT
        if (version not in (V1_VERSION, VERSION) or value.get('contract') != expected_contract
                or value.get('plans') != list(PLANS)):
            raise ValueError('Abstract codebook contract mismatch')
        for name, maximum in (('hand', 32), ('public', 64)):
            book = value.get(name, {})
            mean, scale, centers = (book.get(k) for k in ('mean', 'scale', 'centers'))
            if (not isinstance(mean, list) or len(mean) != (55 if name == 'hand' else 275)
                    or not isinstance(scale, list) or len(scale) != len(mean)
                    or not isinstance(centers, list) or not 1 <= len(centers) <= maximum
                    or any(not isinstance(row, list) or len(row) != len(mean) for row in centers)
                    or any(type(v) not in (int, float) or not math.isfinite(v)
                           for row in [mean, scale, *centers] for v in row)
                    or any(s <= 0 for s in scale)):
                raise ValueError('Invalid abstract codebook values')
        self.value, self.digest = value, digest(value)
        self.hand_count, self.public_count = len(value['hand']['centers']), len(value['public']['centers'])
        self.bucket_limit = 4 * 4 * 3 * self.hand_count * self.public_count

    def vectors(self, observation):
        return visible_vectors(observation, version=self.value['version'])

    def key(self, observation, style, vectors=None):
        if style not in STYLES or observation.phase not in PHASES:
            raise ValueError('Unsupported abstract style/phase')
        if rule_contract(observation) != self.value['rules']:
            raise ValueError('Abstract rule/supply/scoring mismatch')
        own, public = vectors or self.vectors(observation)
        initial = max(1, sum(observation.tile_supply) - 52)
        stage = 0 if observation.wall_remaining > initial * 2 / 3 else 1 if observation.wall_remaining > initial / 3 else 2
        h, p = _centroid(self.value['hand'], own), _centroid(self.value['public'], public)
        return (((STYLES.index(style) * 4 + PHASES.index(observation.phase)) * 3 + stage)
                 * self.hand_count + h) * self.public_count + p


@dataclass(frozen=True)
class Planned:
    choices: tuple
    status: str
    scores: tuple = ()


def plan_actions(observation, legal_actions):
    """Five stable action meanings, always mapped to actual legal candidates.

    Duplicate mappings are allowed. If every plan maps to the same concrete
    action, it is forced and no regret node is created. No action-menu hash or
    availability mask is appended to the state key.
    """
    if not legal_actions:
        raise ValueError('No legal abstract actions')
    ordered = tuple(sorted(legal_actions, key=semantic))
    hu = next((a for a in ordered if a.action_type == ActionType.HU), None)
    if hu or len(ordered) == 1:
        return Planned((hu or ordered[0],), 'hard-rule' if hu else 'single')
    stock = unseen_counts(observation)
    total = max(1, sum(stock))
    initial = max(1, sum(observation.tile_supply) - 52)
    late = max(0., min(1., 1 - observation.wall_remaining / initial))
    danger, feed = [], []
    for i in range(len(TILE_VALUES)):
        d = f = 0.
        for p in observation.players[1:]:
            # Visible-inventory indices, never interpreted as safety/probability.
            exposure = .25 + .3 * len(p.melds) + .3 * late
            support = min(2., stock[i] * p.hand_count / total)
            previous = min(2, p.discards.count(TILE_VALUES[i]))
            d += exposure * (.15 + support) * (1 - .08 * previous)
            if p.seat == 1:
                f = exposure * (.2 + support + .2 * min(3, stock[i]))
        danger.append(d)
        feed.append(f)
    hand, fixed = observation.hand_counts, len(observation.players[0].melds)

    def grade(counts, melds, discarded, claim_cost, kong=False):
        progress, value, *_ = structural(counts, melds)
        if kong:
            progress += .4  # replacement opportunity proxy; no unseen draw inspected
        d = danger[TILE_INDEX[discarded]] if discarded is not None else 0.
        f = feed[TILE_INDEX[discarded]] if discarded is not None else 0.
        return tuple(wp * progress + wv * value - wd * d - wf * f - wc * claim_cost
                     for wp, wv, wd, wf, wc in WEIGHTS)

    evaluations = []
    for action in ordered:
        kind = action.action_type
        if kind == ActionType.DISCARD:
            scores = grade(_remove(hand, (action.tile,)), fixed, action.tile, 0)
        elif kind == ActionType.PASS:
            scores = grade(hand, fixed, None, 0)
        elif kind in (ActionType.CHI, ActionType.PENG):
            values = action.required_values if kind == ActionType.CHI else (observation.target_tile,) * 3
            remaining = _remove(hand, values)
            followups = [grade(_remove(remaining, (tile,)), fixed + 1, tile, 1)
                         for tile, n in zip(TILE_VALUES, remaining) if n]
            if not followups:
                raise ValueError('Claim has no legal-size followup discard')
            scores = tuple(max(row[i] for row in followups) for i in range(len(PLANS)))
        elif kind in (ActionType.MING_GANG, ActionType.AN_GANG, ActionType.JIA_GANG):
            tile = observation.target_tile if kind == ActionType.MING_GANG else action.tile
            copies = {ActionType.MING_GANG: 4, ActionType.AN_GANG: 5, ActionType.JIA_GANG: 1}[kind]
            remaining = _remove(hand, (tile,) * copies)
            scores = grade(remaining, fixed + (kind != ActionType.JIA_GANG),
                           tile if kind == ActionType.JIA_GANG else None, 1, kong=True)
        else:
            raise ValueError('Unknown abstract action type')
        evaluations.append(scores)
    choices = tuple(ordered[max(range(len(ordered)), key=lambda j: evaluations[j][i])]
                    for i in range(len(PLANS)))
    if len({semantic(a) for a in choices}) == 1:
        return Planned((choices[0],), 'single')
    return Planned(choices, 'choice', tuple(evaluations))
