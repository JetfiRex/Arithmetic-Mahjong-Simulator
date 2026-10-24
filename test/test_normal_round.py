import json
import random
from dataclasses import replace

import pytest

from simulator.actions import ActionType, TimeoutConfig
from simulator.config import GameConfig
from simulator.game_state import GamePhase, GameState, GameStateError, PlayerState, TileWall
from simulator.rule_adapter import WinMethod
from simulator.settlement import NormalScoringAdapter, RoundEndReason, ScoreDelta


READY_HAND = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5]


def _discard_game(*, multiple=True, winners=("p1",), scorer=None):
    game = GameState.new_game(
        rng=random.Random(31),
        config=GameConfig(allow_multiple_hu=multiple),
        scoring_adapter=scorer,
    )
    game.phase = GamePhase.WAITING_TURN_ACTION
    game.get_player("p0").hand_tiles = [6, 20]
    for player in game.players[1:]:
        player.hand_tiles = list(READY_HAND) if player.player_id in winners else []
    game.discard_tile("p0", 6)
    return game


def _complete_responses(game, winners=("p1",)):
    for player_id in ("p3", "p2", "p1"):
        game.submit_response(player_id, ActionType.HU if player_id in winners else ActionType.PASS)


def _scores(game):
    return {player.player_id: player.score for player in game.players}


def _self_draw_game(*, scorer=None):
    players = [
        PlayerState(player_id=f"p{seat}", seat=seat, name=f"p{seat}")
        for seat in range(4)
    ]
    # Arrange a real rule tile pool so dealing plus the first draw gives p0 a win.
    pool = TileWall.from_rule_tiles(shuffle=False).tiles
    for value in READY_HAND + [6]:
        pool.remove(value)
    hands = [list(READY_HAND)] + [[pool.pop() for _ in range(13)] for _ in range(3)]
    deal_order = [hands[seat][index] for index in range(13) for seat in range(4)]
    game = GameState(
        players=players,
        wall=TileWall(pool + [6] + list(reversed(deal_order))),
        scoring_adapter=scorer or NormalScoringAdapter(),
        timeout_config=TimeoutConfig(turn_action_seconds=10),
    )
    game.deal_initial_hands()
    game.draw_tile("p0", now=1)
    return game


def test_discard_hu_creates_public_settlement_and_updates_scores():
    game = _discard_game()
    _complete_responses(game)

    assert game.phase == GamePhase.ROUND_ENDED
    assert _scores(game) == {"p0": -16, "p1": 16, "p2": 0, "p3": 0}
    result = game.round_result
    assert result.winner_ids == ("p1",)
    assert result.end_reason == RoundEndReason.HU
    assert result.next_dealer_seat == 1
    assert game.active_timer is None
    assert game.response_window is None
    assert game.turn_window_id is None
    public = game.to_public_dict()
    win = public["round_result"]["settlement_events"][0]["win_result"]
    assert win["win_method"] == "点胡"
    assert win["winning_tile"] == 6
    assert win["groups"] and win["fan_items"]
    assert win["total_fan"] == win["capped_fan"] == 4
    assert win["base_payment"] == 16
    assert "hand_tiles" not in public["players"][2]
    json.dumps(public, ensure_ascii=False)


@pytest.mark.parametrize("multiple", [True, False])
def test_multiple_hu_config_uses_seat_distance_and_settles_each_winner(multiple):
    game = _discard_game(multiple=multiple, winners=("p1", "p2"))
    _complete_responses(game, winners=("p1", "p2"))

    assert game.round_result.winner_ids == (("p1", "p2") if multiple else ("p1",))
    assert _scores(game) == {
        "p0": -32 if multiple else -16,
        "p1": 16,
        "p2": 16 if multiple else 0,
        "p3": 0,
    }
    assert len({event.settlement_id for event in game.settlement_events}) == (2 if multiple else 1)


def test_self_draw_keeps_actual_drawn_tile_after_grouping_and_reordering():
    game = _self_draw_game()
    player = game.get_player("p0")
    drawn_id = game.drawn_tile_id
    game.create_hand_group("p0", [drawn_id])
    assert player.hand_tiles[-1] != 6

    payload = game.submit_turn_action("p0", ActionType.HU, now=2, source_turn_window_id=game.turn_window_id)

    assert game.phase == GamePhase.ROUND_ENDED
    result = game.settlement_events[0].win_result
    assert result.winning_tile == 6
    assert result.win_method == WinMethod.SELF_DRAW
    payment = result.base_payment
    assert _scores(game) == {"p0": 3 * payment, "p1": -payment, "p2": -payment, "p3": -payment}
    assert payload == game.round_result.to_dict()


def test_pass_self_draw_does_not_refresh_timer_and_prevents_same_window_hu():
    game = _self_draw_game()
    timer = game.active_timer
    game.submit_turn_action("p0", ActionType.PASS, now=2)

    assert game.active_timer is timer
    with pytest.raises(GameStateError, match="不能自摸"):
        game.submit_turn_action("p0", ActionType.HU, now=3)
    game.discard_tile_by_id("p0", game.drawn_tile_id, now=3)
    assert game.phase == GamePhase.WAITING_RESPONSE


def test_hu_is_rejected_for_wrong_player_and_after_deadline():
    game = _self_draw_game()
    with pytest.raises(GameStateError):
        game.submit_turn_action("p1", ActionType.HU, now=2)
    with pytest.raises(GameStateError, match="超时"):
        game.submit_turn_action("p0", ActionType.HU, now=11)
    assert game.phase == GamePhase.WAITING_RESPONSE
    assert game.settlement_events == []


def test_no_self_draw_option_after_turn_without_draw():
    game = _self_draw_game()
    game._begin_turn_without_draw(game.get_player("p0"), now=2)
    assert all(option.action_type != ActionType.HU for option in game.turn_options_by_player["p0"])
    with pytest.raises(GameStateError):
        game.submit_turn_action("p0", ActionType.HU, now=3)


def test_replacement_draw_after_an_gang_can_self_draw_win():
    game = GameState.new_game()
    game.get_player("p0").hand_tiles = [3] * 5 + [1, "+", 2, 3, 2, "+", 2, 4]
    game.wall = TileWall([5, 5])
    game.draw_tile("p0", now=1)

    game.submit_turn_action("p0", ActionType.AN_GANG, now=2)
    game.submit_turn_action("p0", ActionType.HU, now=3)

    assert game.round_result.end_reason == RoundEndReason.HU
    assert game.settlement_events[0].win_result.winning_tile == 5
    assert game.get_player("p0").melded_groups[0]["group_type"] == "暗杠"
    assert sum(player.score for player in game.players) == 0


def test_failed_multi_winner_settlement_does_not_partially_apply_scores():
    class FailingScorer(NormalScoringAdapter):
        def settle_win(self, result, context):
            if context.winner_id == "p2":
                raise ValueError("second settlement failed")
            return super().settle_win(result, context)

    game = _discard_game(winners=("p1", "p2"), scorer=FailingScorer())
    with pytest.raises(GameStateError, match="second settlement failed"):
        _complete_responses(game, winners=("p1", "p2"))

    assert _scores(game) == {f"p{seat}": 0 for seat in range(4)}
    assert game.settlement_events == []
    assert not any(player.is_winner for player in game.players)
    assert game.phase == GamePhase.WAITING_RESPONSE
    assert game.response_window is not None
    game.scoring_adapter = NormalScoringAdapter()
    game.submit_response("p1", ActionType.HU)
    assert game.phase == GamePhase.ROUND_ENDED
    assert game.get_player("p0").score == -32


def test_invalid_scorer_output_is_rejected_before_any_score_changes():
    class InvalidScorer(NormalScoringAdapter):
        def settle_win(self, result, context):
            event = super().settle_win(result, context)
            return replace(event, score_deltas=(ScoreDelta(context.winner_id, 999),))

    game = _discard_game(scorer=InvalidScorer())
    with pytest.raises(GameStateError, match="无效的结算"):
        _complete_responses(game)
    assert sum(player.score for player in game.players) == 0
    assert game.round_result is None


def test_end_round_cannot_apply_settlement_twice():
    game = _self_draw_game()
    game.submit_turn_action("p0", ActionType.HU, now=2)
    scores = _scores(game)
    with pytest.raises(GameStateError):
        game.submit_turn_action("p0", ActionType.HU, now=3)
    with pytest.raises(GameStateError):
        game.end_round("hu")
    assert _scores(game) == scores
    assert len(game.settlement_events) == 1


def test_last_draw_can_still_win_before_wall_empty_settlement():
    game = _self_draw_game()
    game.wall.tiles.clear()
    game.submit_turn_action("p0", ActionType.HU, now=2)
    assert game.round_result.end_reason == RoundEndReason.HU


def test_last_discard_can_be_claimed_for_hu_with_empty_wall():
    game = _discard_game()
    game.wall.tiles.clear()
    _complete_responses(game)
    assert game.round_result.end_reason == RoundEndReason.HU
    assert game.get_player("p1").score == 16


def test_last_discard_waits_for_responses_before_zero_score_draw_result():
    game = _discard_game(winners=())
    game.wall.tiles.clear()
    game.get_player("p0").score = 50
    assert game.round_result is None
    _complete_responses(game, winners=())

    assert game.phase == GamePhase.ROUND_ENDED
    assert game.round_result.end_reason == RoundEndReason.WALL_EMPTY
    assert game.round_result.winner_ids == ()
    assert game.round_result.settlement_events == ()
    assert all(delta.delta == 0 for delta in game.round_result.score_deltas)
    assert game.get_player("p0").score == 50


def test_wall_empty_draw_still_generates_result():
    game = GameState.new_game()
    game.wall.tiles.clear()
    with pytest.raises(GameStateError, match="流局"):
        game.draw_tile(game.current_player().player_id)
    assert game.round_result.end_reason == RoundEndReason.WALL_EMPTY
    assert all(delta.delta == 0 for delta in game.round_result.score_deltas)


@pytest.mark.parametrize("dealer", [0, 3])
@pytest.mark.parametrize("win", [False, True])
def test_manual_next_round_preserves_scores_rotates_dealer_and_resets_state(dealer, win):
    game = _self_draw_game()
    game.dealer_seat = dealer
    game.create_hand_group("p0", game.get_player("p0").hand_tile_ids[:3])
    if win:
        game.submit_turn_action("p0", ActionType.HU, now=2)
    else:
        game.end_round("wall_empty")
    old_result = game.round_result
    old_round_id = game.round_id
    old_game_id = game.game_id
    scores = _scores(game)
    assert game.phase == GamePhase.ROUND_ENDED

    game.start_next_round(rng=random.Random(41), source_round_id=old_round_id)

    assert game.game_id == old_game_id
    assert game.round_id != old_round_id
    assert game.dealer_seat == game.current_turn_seat == (dealer + 1) % 4
    assert game.phase == GamePhase.WAITING_DRAW
    assert _scores(game) == scores
    assert [player.is_dealer for player in game.players].count(True) == 1
    assert all(len(player.hand_tiles) == 13 for player in game.players)
    assert all(not player.hand_groups and not player.melded_groups and not player.discarded_tiles for player in game.players)
    assert not any(player.is_winner for player in game.players)
    assert game.round_result is None
    assert game.settlement_events == game.resolved_actions == game.discard_history == []
    assert game.last_discard is game.active_timer is game.turn_window_id is None
    assert old_result.round_id == old_round_id
    with pytest.raises(GameStateError):
        game.start_next_round()


def test_stale_next_round_request_is_rejected():
    game = GameState.new_game()
    game.end_round("wall_empty")
    old_round_id = game.round_id
    with pytest.raises(GameStateError, match="已过期"):
        game.start_next_round(source_round_id="other-round")
    assert game.round_id == old_round_id
    assert game.phase == GamePhase.ROUND_ENDED
