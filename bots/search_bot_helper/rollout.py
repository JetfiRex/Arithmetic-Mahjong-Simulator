"""Paired, truncated engine rollouts with observation-only continuation policies."""

from dataclasses import dataclass
import math

from bots.baseline_bot import TsumogiriBot
from bots.search_bot_helper.belief import turn_game
from bots.common_bot_helper.decision import eligible_players
from bots.common_bot_helper.decision_driver import Control, DecisionDriver
from bots.math_bot_helper.features import shape_potential
from bots.common_bot_helper.observation import VisibleHistory, observe
from bots.math_bot_helper.analysis import unseen_counts
from simulator.actions import ActionType
from simulator.game_state import GamePhase, GameStateError
from simulator.rule_adapter import MeldSnapshot

ROLLOUT_VERSION = 'observed-policy-truncated-v1'


@dataclass(frozen=True)
class RolloutValue:
    value: float
    terminal: bool
    transitions: int


def no_hit_probability(total, hits, draws):
    if not 0 <= hits <= total or not 0 <= draws <= total:
        raise ValueError('Invalid without-replacement population')
    if draws > total - hits:
        return 0.0
    return math.comb(total - hits, draws) / math.comb(total, draws)


def completion_probability(unseen, wait_values, draws):
    """Exact fixed-wait hit probability in exchangeable unknowns; no competition."""
    from bots.common_bot_helper.observation import TILE_VALUES
    total = sum(unseen)
    if any(type(n) is not int or n < 0 for n in unseen) or len(unseen) != len(TILE_VALUES):
        raise ValueError('Invalid unseen inventory')
    waits = set(wait_values)
    hits = sum(n for v, n in zip(TILE_VALUES, unseen) if v in waits)
    return 1 - no_hit_probability(total, hits, draws)


def terminal_value(delta):
    # A real win/loss must outrank any truncated leaf on its corresponding side.
    return math.copysign(1 + .5 * math.tanh(abs(delta) / 16), delta) if delta else 0.0


def rollout(observation, world, root, *, horizon, budget, policy=None):
    """None means interrupted: caller must discard the whole paired batch.

    Leaf values are a dimensionless offensive potential; terminal chip returns
    are compressed onto the same bounded scale. This is not a full-game EV.
    Future root-player choices cannot read the sampled wall/opponent hands.
    """
    if root.action_type != ActionType.DISCARD or horizon < 1:
        raise ValueError('Rollout requires a discard root and positive horizon')
    if budget.expired():
        return None
    game = turn_game(observation, world)
    history = VisibleHistory(game)
    # Opponents get a synthetic current-hand bootstrap and public root history;
    # the observer retains its actual visible history. No fabricated past draws.
    history.events = [(('p0' if event.private else None), event) for event in observation.history] + [
        event for event in history.events if event[0] not in (None, 'p0')]
    driver = DecisionDriver(game, {f'p{i}': Control.BOT for i in range(4)}, history)
    policy = policy or TsumogiriBot()
    game.discard_tile('p0', root.tile)
    history.sync(game)
    transitions = 1
    while game.round_result is None and transitions < horizon:
        if budget.expired():
            return None
        if game.phase == GamePhase.WAITING_DRAW:
            try:
                game.draw_tile(game.current_player().player_id)
            except GameStateError:
                if game.round_result is None:
                    raise
            history.sync(game)
            transitions += 1
            continue
        players = eligible_players(game)
        if not players:
            raise RuntimeError('Hypothetical game failed to progress')
        for pid in players:
            if budget.expired():
                return None
            request = driver.prepare(pid)
            # Only the projected observation/candidates reach the continuation.
            selected = next((a for a in request.legal_actions if a.action_type == ActionType.HU), None)
            selected = selected or policy.act(request.observation, request.legal_actions)
            driver.submit(request.decision_id, selected.action_id)
            transitions += 1
        history.sync(game)
    if budget.expired():
        return None
    if game.round_result is not None:
        delta = next(d.delta for d in game.round_result.score_deltas if d.player_id == 'p0')
        return RolloutValue(terminal_value(delta), True, transitions)
    view = observe(game, 'p0', history)
    melds = tuple(MeldSnapshot(m.group_type, m.tiles) for m in view.players[0].melds)
    # The leaf also uses the root player's view, never sampled hidden counts.
    potential = shape_potential(view.hand_counts, melds, unseen_counts(view)).best
    return RolloutValue(max(-.75, min(.75, (potential - 7) / 14)), False, transitions)
