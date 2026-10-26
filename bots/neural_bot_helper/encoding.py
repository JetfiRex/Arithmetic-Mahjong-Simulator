"""Visible-only state/candidate features for the small conditional policy.

No rule oracle, teacher score, wall seed, UUID, timer, or opponent private tile
enters the encoder. Shape waits ignore fan and are explicitly only features.
"""

from dataclasses import dataclass

from bots.math_bot_helper.analysis import AnalysisBudget, unseen_counts
from bots.common_bot_helper.decision import ACTION_VERSION
from bots.math_bot_helper.features import counts_from_values, subtract_counts, shape_potential, possible_shape
from bots.common_bot_helper.observation import TILE_VALUES, TILE_INDEX, OBSERVATION_VERSION
from bots.style_bot_helper.risk import assess_risk, RISK_VERSION
from bots.style_bot_helper.styles import PROFILE_VERSION
from bots.style_bot_helper.targets import choose_target, target_missing, TARGET_VERSION
from simulator.actions import ActionType
from simulator.action_options import build_chi_detail_payload
from simulator.rule_adapter import MeldSnapshot

FEATURE_VERSION = 'conditional-policy-features-v1'
PHASES = ('WAITING_TURN_ACTION', 'WAITING_RESPONSE', 'WAITING_ACTION_DETAIL', 'WAITING_ROB_KONG')
KINDS = tuple(ActionType)
MELDS = ('吃', '碰', '明杠', '暗杠', '加杠')
HEADS = ('fast', 'big', 'defensive-0', 'defensive-1')
CONTRACT = {'features': FEATURE_VERSION, 'observation': OBSERVATION_VERSION, 'actions': ACTION_VERSION,
            'profiles': PROFILE_VERSION, 'risk': RISK_VERSION, 'targets': TARGET_VERSION}


def onehot(value, values):
    return [float(value == v) for v in values]


def head_weights(style, containment=None):
    if style in ('fast', 'big') and containment is not None:
        raise ValueError('Neural containment is adjustable only for defensive style')
    if style == 'blocking' and containment not in (None, 1):
        raise ValueError('Blocking is the containment=1 preset')
    if style == 'fast':
        return (1., 0., 0., 0.)
    if style == 'big':
        return (0., 1., 0., 0.)
    if style == 'blocking':
        style, containment = 'defensive', 1.
    if style != 'defensive':
        raise ValueError('Unknown neural style')
    c = .35 if containment is None else float(containment)
    if not 0 <= c <= 1:
        raise ValueError('Containment must be between zero and one')
    return (0., 0., 1 - c, c)


@dataclass(frozen=True)
class EncodedDecision:
    state: tuple[float, ...]
    actions: tuple[tuple[float, ...], ...]
    auxiliary: tuple[tuple[float, float, float, int | str | None, int | None], ...]
    target: object
    pressure: float


class EncodingTimeout(TimeoutError):
    pass


def encode(observation, actions, *, previous_target=None, budget=None):
    budget = budget or AnalysisBudget()
    if budget.expired():
        raise EncodingTimeout('Feature budget expired')
    stock = unseen_counts(observation)
    risk = assess_risk(observation, stock)
    melds = tuple(MeldSnapshot(m.group_type, m.tiles) for m in observation.players[0].melds)
    target, _ = choose_target(observation.hand_counts, melds, stock, observation.wall_remaining,
                              observation.min_fan or 0, previous_target)
    supply = observation.tile_supply
    normalise = lambda counts: [n / max(1, s) for n, s in zip(counts, supply)]
    state = normalise(observation.hand_counts)
    state += [s / 12 for s in supply]
    state += onehot(observation.phase, PHASES)
    state += onehot(observation.dealer, range(4)) + onehot(observation.current_player, range(4))
    state += onehot(observation.source, (None, 0, 1, 2, 3))
    state += onehot(observation.drawn_tile, TILE_VALUES) + onehot(observation.target_tile, TILE_VALUES)
    state += [observation.wall_remaining / max(1, sum(supply)), (observation.min_fan or 0) / 6,
              float(observation.allow_multiple_hu), risk.pressure]
    for player in observation.players:
        state += [player.hand_count / 14, max(-5., min(5., player.score / 100)), len(player.melds) / 3]
        state += [sum(m.group_type == kind for m in player.melds) / 3 for kind in MELDS]
        state += normalise(counts_from_values(v for m in player.melds for v in m.tiles))
        state += normalise(counts_from_values(player.discards))
    # Aggregate perfect-recall input, with no event sequence IDs/time/hidden draws.
    for seat in range(4):
        state += normalise(counts_from_values(e.tile for e in observation.history
            if e.kind == 'discard' and e.actor == seat and e.tile is not None))
    state += normalise(counts_from_values(e.tile for e in observation.history
        if e.private and e.kind == 'own_draw' and e.tile is not None))
    state += normalise(target.whole) if target else [0.] * len(TILE_VALUES)
    state += onehot(target.family if target else None, ('七小对', '连七对', '清龙'))
    state += [float(target is not None), target.fan_floor / 6 if target else 0.]
    rows, aux = [], []
    for action in actions:
        if budget.expired():
            raise EncodingTimeout('Candidate feature budget expired')
        counts, new_melds, outgoing = observation.hand_counts, melds, None
        post_discard = False
        if action.action_type == ActionType.DISCARD:
            counts = subtract_counts(counts, (action.tile,))
            outgoing, post_discard = action.tile, True
        elif action.action_type == ActionType.PASS:
            post_discard = sum(counts) == (3 - len(melds)) * 4 + 1
        elif action.action_type == ActionType.PENG:
            counts = subtract_counts(counts, (observation.target_tile,) * 3)
            new_melds += (MeldSnapshot('碰', (observation.target_tile,) * 4),)
        elif action.action_type == ActionType.CHI and action.required_values:
            counts = subtract_counts(counts, action.required_values)
            payload = build_chi_detail_payload(list(action.required_values), observation.target_tile)
            new_melds += (MeldSnapshot('吃', tuple(payload['display_tiles'])),)
        tile_risk = risk.for_tile(outgoing) if outgoing is not None else None
        if action.action_type == ActionType.JIA_GANG:
            tile_risk = risk.for_tile(action.tile)
        danger, feeding = (tile_risk.danger, tile_risk.feeding) if tile_risk else (0., 0.)
        if post_discard:
            potential = shape_potential(counts, new_melds, stock)
            potentials = (potential.arithmetic, potential.traditional, potential.seven_pairs,
                          potential.progression_pairs, potential.dragon)
        else:
            potentials = (0.,) * 5
        waits = []
        for i, n in enumerate(stock):
            if budget.expired():
                raise EncodingTimeout('Shape feature budget expired')
            if post_discard and n:
                added = list(counts); added[i] += 1
                waits.append(n if possible_shape(tuple(added), new_melds) else 0)
            else:
                waits.append(0)
        missing = target_missing(target, counts, new_melds, stock) if target else None
        row = onehot(action.action_type, KINDS) + onehot(action.tile, TILE_VALUES)
        row += normalise(counts_from_values(action.required_values))
        row += onehot(action.meld_index, (None, 0, 1, 2))
        row += normalise(counts)
        row += [v / 14 for v in potentials]
        row += [danger / 5, feeding / 2, float(post_discard), float(missing is not None),
                (missing if missing is not None else 8) / 8, float(sum(waits) > 0), sum(waits) / max(1, sum(stock))]
        row += [n / 12 for n in waits]
        rows.append(tuple(row))
        aux.append((max(potentials), danger, feeding, outgoing, missing))
    return EncodedDecision(tuple(state), tuple(rows), tuple(aux), target, risk.pressure)
