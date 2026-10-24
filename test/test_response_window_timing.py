"""A timed discard response is a fixed window for every discarding seat."""

import pytest

from simulator import ActionType, GamePhase, GameState, PlayerState, TileWall, TimeoutConfig
from UI.game_session import DeveloperSession


def response_game(source, seconds=5, hand=None):
    target = (source + 1) % 4
    players = [PlayerState(f"p{i}", i, f"p{i}", hand_tiles=[]) for i in range(4)]
    players[source].hand_tiles = [3, 20]
    players[target].hand_tiles = list(hand if hand is not None else [3, 3, 3])
    game = GameState(players, TileWall([7, 8]), phase=GamePhase.WAITING_TURN_ACTION, current_turn_seat=source,
                     timeout_config=TimeoutConfig(discard_response_seconds=seconds, action_detail_seconds=5, turn_action_seconds=30))
    game.discard_tile(f"p{source}", 3, now=10)
    return game, f"p{target}"


@pytest.mark.parametrize("source", range(4))
@pytest.mark.parametrize("choice", [ActionType.PASS, ActionType.PENG])
def test_complete_responses_cannot_end_timed_window_early(source, choice):
    game, responder = response_game(source)
    window = game.response_window
    game.submit_response(responder, choice, now=11)
    assert window.is_complete()
    assert game.phase == GamePhase.WAITING_RESPONSE
    assert game.response_window is window
    assert not game.get_player(responder).melded_groups
    game.expire_timers(14.999)
    assert game.response_window is window
    game.expire_timers(15)
    assert game.response_window is None
    assert game.current_player().player_id == responder
    if choice == ActionType.PENG:
        assert game.phase == GamePhase.WAITING_TURN_ACTION
        assert game.get_player(responder).melded_groups[0]["group_type"] == "碰"
        assert game.active_timer.deadline_at == 45
    else:
        assert game.phase == GamePhase.WAITING_DRAW


@pytest.mark.parametrize("source", range(4))
def test_silent_passes_also_wait_full_duration(source):
    game, _ = response_game(source, hand=[])
    assert game.response_window.is_complete()
    game.expire_timers(14.999)
    assert game.phase == GamePhase.WAITING_RESPONSE
    game.expire_timers(15)
    assert game.phase == GamePhase.WAITING_DRAW
    assert game.current_turn_seat == (source + 1) % 4


def test_untimed_window_still_resolves_after_every_response():
    game, responder = response_game(0, seconds=None)
    for player_id in ("p1", "p2", "p3"):
        game.submit_response(player_id, ActionType.PENG if player_id == responder else ActionType.PASS, now=11)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.current_player().player_id == responder


@pytest.mark.parametrize("source", [1, 2, 3])
def test_pass_only_bots_cannot_skip_other_bots_t1(source):
    session = DeveloperSession()
    session.started = True
    game, _ = response_game(source)
    # Give a bot a real PENG choice, while p0 can only silently pass.
    target = next(seat for seat in (1, 2, 3) if seat != source)
    players = game.players
    for player in players:
        if player.seat != source:
            player.hand_tiles = [3, 3, 3] if player.seat == target else []
            player._reset_hand_identity()
    # Recreate options after changing the test hands.
    game.phase = GamePhase.WAITING_TURN_ACTION
    game.players[source].add_hand_tile(3)
    game.discard_tile(f"p{source}", 3, now=10)
    session.game = game
    state = session.snapshot(now=11)
    assert game.response_window.is_complete()
    assert state["phase"] == "WAITING_RESPONSE"
    assert state["active_timer"] is None
    session.tick(now=14.999)
    assert game.phase == GamePhase.WAITING_RESPONSE
    session.tick(now=15)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.current_turn_seat == (source + 1) % 4
