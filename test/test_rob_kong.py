import json

import pytest

from simulator.actions import ActionType, TimeoutConfig, TimerKind
from simulator.config import GameConfig
from simulator.game_state import GamePhase, GameState, GameStateError, PlayerState, TileWall
from simulator.melds import make_peng_meld
from simulator.rule_adapter import ArithmeticMahjongRuleAdapter, WinMethod
from simulator.settlement import NormalScoringAdapter


READY_HAND = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5]


def _game(*, eligible=("p1",), t3=20, t4=5, multiple=True):
    players = [
        PlayerState(
            player_id=f"p{seat}", seat=seat, name=f"p{seat}",
            hand_tiles=list(READY_HAND) if f"p{seat}" in eligible else [],
        )
        for seat in range(4)
    ]
    players[0].hand_tiles = [6, 9]
    players[0].melded_groups = [make_peng_meld(6)]
    game = GameState(
        players=players,
        wall=TileWall([7, 8]),
        phase=GamePhase.WAITING_DRAW,
        config=GameConfig(allow_multiple_hu=multiple),
        timeout_config=TimeoutConfig(turn_action_seconds=t3, rob_kong_seconds=t4),
    )
    game.draw_tile("p0", now=10)
    return game


def _declare(game):
    return game.submit_turn_action(
        "p0", ActionType.JIA_GANG, now=12, source_turn_window_id=game.turn_window_id,
    )


def test_proposed_jia_gang_pauses_t3_without_removing_tiles_or_drawing():
    game = _game()
    player = game.get_player("p0")
    ids = list(player.hand_tile_ids)
    old_window = game.turn_window_id

    assert _declare(game) is None

    assert game.phase == GamePhase.WAITING_ROB_KONG
    assert set(game.rob_kong_window.options_by_player) == {"p1"}
    assert player.hand_tile_ids == ids
    assert player.melded_groups[0]["group_type"] == "碰"
    assert game.wall.remaining_count() == 1
    assert game.turn_window_id == old_window
    assert game.active_timer.kind == TimerKind.ROB_KONG_RESPONSE
    assert game.active_timer.deadline_at == 17
    assert game.rob_kong_window.paused_turn_timer.remaining_seconds(12) == 18


def test_only_eligible_players_can_decide_and_all_normal_operations_are_frozen():
    game = _game()
    _declare(game)
    player = game.get_player("p0")
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p0", ActionType.PASS, now=13)
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p2", ActionType.HU, now=13)
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p1", ActionType.PENG, now=13)
    with pytest.raises(GameStateError):
        game.discard_tile("p0", 9, now=13)
    with pytest.raises(GameStateError):
        game.submit_turn_action("p0", ActionType.PASS, now=13)
    with pytest.raises(GameStateError):
        game.draw_tile("p0", now=13)
    with pytest.raises(GameStateError):
        game.create_hand_group("p0", player.hand_tile_ids[:1])
    with pytest.raises(GameStateError):
        game.move_hand_tile_by_id("p0", player.hand_tile_ids[0], 1)
    assert game.phase == GamePhase.WAITING_ROB_KONG


def test_everyone_passing_completes_gang_and_resumes_original_remaining_t3():
    game = _game(t4=None)
    old_turn_window = game.turn_window_id
    _declare(game)
    game.expire_timers(500)
    assert game.phase == GamePhase.WAITING_ROB_KONG
    game.submit_rob_kong_response("p1", ActionType.PASS, now=500)

    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.rob_kong_window is None
    assert game.turn_window_id != old_turn_window
    assert game.get_player("p0").melded_groups[0]["group_type"] == "加杠"
    assert game.get_player("p0").hand_tiles[-1] == 7
    assert game.active_timer.kind == TimerKind.TURN_DECISION
    assert game.active_timer.remaining_seconds(500) == 18
    assert game.active_timer.deadline_at == 518
    assert game.round_result is None
    game.expire_timers(517.9)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    game.expire_timers(518)
    assert game.phase == GamePhase.WAITING_RESPONSE


def test_t4_timeout_defaults_unsubmitted_players_to_pass():
    game = _game()
    _declare(game)
    game.expire_timers(16.9)
    assert game.phase == GamePhase.WAITING_ROB_KONG
    game.expire_timers(17)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.active_timer.remaining_seconds(17) == 18
    assert game.get_player("p0").melded_groups[0]["group_type"] == "加杠"


def test_rob_kong_hu_preserves_peng_skips_draw_and_charges_declarer():
    game = _game()
    source = game.get_player("p0")
    robbed_id = source.hand_tile_ids[0]
    _declare(game)
    game.submit_rob_kong_response("p1", ActionType.HU, now=13)

    assert game.phase == GamePhase.ROUND_ENDED
    assert robbed_id not in source.hand_tile_ids
    assert source.melded_groups[0]["group_type"] == "碰"
    assert len(source.melded_groups[0]["tiles"]) == 4
    assert game.wall.remaining_count() == 1
    assert game.rob_kong_window is game.active_timer is None
    assert game.round_result.winner_ids == ("p1",)
    event = game.settlement_events[0]
    assert event.win_result.win_method == WinMethod.ROB_KONG
    assert event.source_player_id == "p0"
    assert source.score == -event.win_result.base_payment
    assert game.get_player("p1").score == event.win_result.base_payment
    assert not any(action.action_type == ActionType.JIA_GANG for action in game.resolved_actions)
    assert game.to_public_dict()["round_result"]["settlement_events"][0]["win_result"]["win_method"] == "抢杠"


@pytest.mark.parametrize("multiple", [True, False])
def test_multiple_robbers_obey_multiple_hu_config_and_seat_distance(multiple):
    game = _game(eligible=("p1", "p2"), multiple=multiple)
    _declare(game)
    game.submit_rob_kong_response("p2", ActionType.HU, now=13)
    assert game.phase == GamePhase.WAITING_ROB_KONG
    game.submit_rob_kong_response("p1", ActionType.HU, now=14)
    assert game.round_result.winner_ids == (("p1", "p2") if multiple else ("p1",))
    assert game.get_player("p0").score == (-32 if multiple else -16)


def test_t4_timeout_keeps_already_submitted_hu():
    game = _game(eligible=("p1", "p2"))
    _declare(game)
    game.submit_rob_kong_response("p2", ActionType.HU, now=13)
    game.expire_timers(17)
    assert game.phase == GamePhase.ROUND_ENDED
    assert game.round_result.winner_ids == ("p2",)


def test_hu_received_at_deadline_is_pass_and_stale_or_duplicate_responses_fail():
    game = _game(eligible=("p1", "p2"))
    _declare(game)
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p1", ActionType.HU, now=13, source_window_id="stale")
    game.submit_rob_kong_response("p1", ActionType.PASS, now=13)
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p1", ActionType.HU, now=14)
    game.submit_rob_kong_response("p2", ActionType.HU, now=17)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.round_result is None


def test_no_eligible_robber_skips_t4_and_keeps_normal_gang_refresh_rule():
    game = _game(eligible=())
    meld = _declare(game)
    assert meld["group_type"] == "加杠"
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.rob_kong_window is None
    assert game.active_timer.remaining_seconds(12) == 20


def test_t3_can_remain_disabled_across_t4():
    game = _game(t3=None)
    _declare(game)
    assert game.active_timer.kind == TimerKind.ROB_KONG_RESPONSE
    game.submit_rob_kong_response("p1", ActionType.PASS, now=13)
    assert game.active_timer is None
    assert game.phase == GamePhase.WAITING_TURN_ACTION


def test_an_gang_cannot_be_robbed_even_when_other_player_could_win_on_value():
    game = _game()
    game.get_player("p0").hand_tiles = [6] * 5 + [9]
    game.get_player("p0").melded_groups.clear()
    game.phase = GamePhase.WAITING_DRAW
    game.wall = TileWall([7, 8])
    game.draw_tile("p0", now=10)
    game.submit_turn_action("p0", ActionType.AN_GANG, now=12)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.rob_kong_window is None
    assert game.get_player("p0").melded_groups[0]["group_type"] == "暗杠"


def test_ming_gang_does_not_open_a_second_rob_kong_window():
    game = _game(eligible=("p2",))
    game.get_player("p1").hand_tiles = [6] * 4 + [9]
    game.submit_turn_action("p0", ActionType.PASS, now=11)
    game.discard_tile("p0", 6, now=12)
    game.submit_response("p1", ActionType.MING_GANG, now=13)
    game.submit_response("p2", ActionType.PASS, now=13)
    game.submit_response("p3", ActionType.PASS, now=13)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.rob_kong_window is None
    assert game.get_player("p1").melded_groups[0]["group_type"] == "明杠"


def test_rob_kong_eligibility_obeys_the_rule_adapters_minimum_fan():
    game = _game()
    game.rule_adapter = ArithmeticMahjongRuleAdapter(min_fan=5)
    assert _declare(game)["group_type"] == "加杠"
    assert game.rob_kong_window is None


def test_failed_rob_kong_settlement_keeps_tiles_and_scores_and_allows_retry():
    class FailingScorer(NormalScoringAdapter):
        def settle_win(self, result, context):
            raise ValueError("unavailable")

    game = _game()
    _declare(game)
    source_ids = list(game.get_player("p0").hand_tile_ids)
    game.scoring_adapter = FailingScorer()
    with pytest.raises(GameStateError):
        game.submit_rob_kong_response("p1", ActionType.HU, now=13)
    assert game.phase == GamePhase.WAITING_ROB_KONG
    assert game.get_player("p0").hand_tile_ids == source_ids
    assert sum(player.score for player in game.players) == 0
    assert game.settlement_events == []
    game.scoring_adapter = NormalScoringAdapter()
    game.submit_rob_kong_response("p1", ActionType.HU, now=14)
    assert game.phase == GamePhase.ROUND_ENDED


def test_t4_options_and_countdowns_are_visible_only_to_eligible_player():
    game = _game(eligible=("p1", "p2"))
    _declare(game)
    for player_id in ("p0", "p3", None):
        snapshot = game.to_public_dict(viewer_player_id=player_id)
        assert snapshot["rob_kong_window"] is None
        assert snapshot["active_timer"] is None
        assert "deadline_at" not in json.dumps(snapshot)
        if player_id == "p0":
            assert snapshot["paused_turn_remaining_seconds"] == 18
        else:
            assert snapshot["paused_turn_remaining_seconds"] is None
    for player_id in ("p1", "p2"):
        snapshot = game.to_public_dict(viewer_player_id=player_id)
        assert snapshot["active_timer"]["kind"] == "ROB_KONG_RESPONSE"
        assert snapshot["rob_kong_window"]["deadline_at"] == 17
        assert {option["action_type"] for option in snapshot["rob_kong_window"]["options"]} == {"HU", "PASS"}
        assert snapshot["paused_turn_remaining_seconds"] is None


def test_t3_countdown_is_private_even_when_hands_are_revealed_for_debugging():
    game = _game()
    assert game.to_public_dict(viewer_player_id="p0")["active_timer"]["deadline_at"] == 30
    for player_id in ("p1", "p2", "p3", None):
        snapshot = game.to_public_dict(viewer_player_id=player_id, reveal_hands=True)
        assert snapshot["active_timer"] is None
        assert "deadline_at" not in json.dumps(snapshot)


def test_t1_and_t2_deadlines_are_not_exposed_to_other_players():
    game = _game(eligible=())
    game.timeout_config = TimeoutConfig(discard_response_seconds=5, action_detail_seconds=7)
    game.get_player("p1").hand_tiles = [1, "+", 5]
    game.submit_turn_action("p0", ActionType.PASS, now=11)
    game.discard_tile("p0", 6, now=12)

    assert game.to_public_dict(viewer_player_id="p1")["active_timer"]["deadline_at"] == 17
    for player_id in ("p0", "p2", "p3", None):
        public = game.to_public_dict(viewer_player_id=player_id, reveal_hands=True)
        assert public["active_timer"] is None
        assert "deadline_at" not in json.dumps(public)

    game.submit_response("p1", ActionType.CHI, now=13)
    assert game.phase == GamePhase.WAITING_RESPONSE
    game.expire_timers(17)
    assert game.phase == GamePhase.WAITING_ACTION_DETAIL
    assert game.to_public_dict(viewer_player_id="p1")["active_timer"]["deadline_at"] == 24
    for player_id in ("p0", "p2", "p3", None):
        public = game.to_public_dict(viewer_player_id=player_id, reveal_hands=True)
        assert public["active_timer"] is None
        assert "deadline_at" not in json.dumps(public)
