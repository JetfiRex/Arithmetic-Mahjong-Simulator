"""Uniform hypothetical worlds drawn ONLY from a player's visible inventory."""

from dataclasses import dataclass

from bots.math_bot_helper.analysis import SUPPORTED_RULESET, unseen_counts
from bots.math_bot_helper.features import values_from_counts
from simulator.actions import TimeoutConfig
from simulator.config import GameConfig
from simulator.game_state import GamePhase, GameState, PlayerState, TileWall
from simulator.rule_adapter import ArithmeticMahjongRuleAdapter

BELIEF_VERSION = 'uniform-visible-inventory-v1'


@dataclass(frozen=True)
class SampledWorld:
    hands: tuple[tuple[int | str, ...], ...]
    wall: tuple[int | str, ...]


def sample_world(observation, rng):
    """Exchangeable unknowns, NOT a posterior inferred from discard choices.

    History is retained by rollout observations, but does not bias this prior.
    A publicly declared added-kong copy is reserved in its source's hand.
    """
    stock = list(values_from_counts(unseen_counts(observation)))
    reserved = observation.source if observation.phase == 'WAITING_ROB_KONG' else None
    reserved = reserved if reserved not in (None, 0) else None
    slots = sum(p.hand_count for p in observation.players[1:]) - int(reserved is not None)
    if slots < 0 or len(stock) != slots + observation.wall_remaining:
        raise ValueError('Visible hand sizes/wall do not conserve the tile inventory')
    rng.shuffle(stock)
    hands = [values_from_counts(observation.hand_counts)]
    cursor = 0
    for p in observation.players[1:]:
        count = p.hand_count - int(p.seat == reserved)
        if count < 0:
            raise ValueError('Invalid declared-kong source hand')
        hand = tuple(stock[cursor:cursor + count])
        cursor += count
        if p.seat == reserved:
            hand += (observation.target_tile,)
        hands.append(hand)
    return SampledWorld(tuple(hands), tuple(stock[cursor:]))


def turn_game(observation, world):
    """Reconstruct a fresh t3 experiment, not a server checkpoint.

    Relative seat zero is always the observer. Only discard roots are currently
    supported. Prompt acknowledgement is local to this synthetic t3 window.
    """
    if (observation.phase != 'WAITING_TURN_ACTION' or observation.current_player != 0
            or observation.ruleset_id != SUPPORTED_RULESET or observation.min_fan is None
            or observation.scoring_id != 'NormalScoringAdapter'):
        raise ValueError('Rollout reconstruction currently supports built-in t3 discard roots')
    players = []
    for p, hand in zip(observation.players, world.hands):
        players.append(PlayerState(f'p{p.seat}', p.seat, f'p{p.seat}',
            is_dealer=p.seat == observation.dealer, hand_tiles=list(hand), score=p.score,
            melded_groups=[{'group_type': m.group_type, 'tiles': list(m.tiles), 'display_tiles': []} for m in p.melds],
            discarded_tiles=list(p.discards)))
    game = GameState(players, TileWall(list(world.wall)), phase=GamePhase.WAITING_TURN_ACTION,
        dealer_seat=observation.dealer, current_turn_seat=0, timeout_config=TimeoutConfig(),
        rule_adapter=ArithmeticMahjongRuleAdapter(min_fan=observation.min_fan),
        config=GameConfig(allow_multiple_hu=observation.allow_multiple_hu),
        turn_window_id='hypothetical-turn', turn_gang_prompt_passed=True, turn_hu_prompt_passed=True)
    # No root HU/kong evaluation is needed: the real candidate contract already
    # supplied legal discards. Subsequent windows are built normally by GameState.
    return game
