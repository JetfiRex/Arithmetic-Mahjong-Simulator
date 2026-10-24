import random

import pytest

from simulator.actions import ActionType, TimeoutConfig, TimerKind
from simulator.game_state import GamePhase, GameState, GameStateError


def _open_response(
    discarded_tile,
    responder_hands,
    *,
    responder_groups=None,
    timeout_config=None,
    now=0.0,
):
    game = GameState.new_game(
        rng=random.Random(21),
        dealer_seat=0,
        timeout_config=timeout_config,
    )
    game.current_turn_seat = 0
    game.phase = GamePhase.WAITING_TURN_ACTION
    game.get_player("p0").hand_tiles = [discarded_tile, 20]
    for player_id in ("p1", "p2", "p3"):
        game.get_player(player_id).hand_tiles = list(responder_hands.get(player_id, []))
    for player_id, groups in (responder_groups or {}).items():
        player = game.get_player(player_id)
        for group_values in groups:
            game.create_hand_group(
                player_id,
                _tile_ids_for_values(player, group_values),
            )
    game.discard_tile("p0", discarded_tile, now=now)
    return game


def _tile_ids_for_values(player, values):
    player.hand_tile_records()
    remaining = list(values)
    selected = []
    for tile_id, value in zip(player.hand_tile_ids, player.hand_tiles):
        if value in remaining:
            selected.append(tile_id)
            remaining.remove(value)
    assert not remaining
    return selected


def test_t1_ming_gang_beats_peng_and_chi():
    game = _open_response(
        3,
        {
            "p1": [1, "+", 2],
            "p2": [3, 3, 3],
            "p3": [3, 3, 3, 3],
        },
    )

    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PENG)
    game.submit_response("p3", ActionType.MING_GANG, now=2)

    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.current_turn_seat == 3
    assert game.get_player("p3").melded_groups[-1]["group_type"] == "明杠"
    assert game.resolved_actions[-1].action_type == ActionType.MING_GANG
    assert game.get_player("p0").discarded_tiles == []


def test_t1_same_priority_uses_nearest_seat_not_submission_time():
    game = _open_response(
        3,
        {
            "p1": [3, 3, 3],
            "p2": [3, 3, 3],
            "p3": [],
        },
    )

    game.submit_response("p2", ActionType.PENG, now=1)
    game.submit_response("p1", ActionType.PENG, now=2)
    game.submit_response("p3", ActionType.PASS, now=3)

    assert game.current_turn_seat == 1
    assert game.get_player("p1").melded_groups[-1]["group_type"] == "碰"
    assert game.get_player("p2").melded_groups == []


def test_t1_rejects_unoffered_or_duplicate_response():
    game = _open_response(3, {"p1": [], "p2": [], "p3": [3, 3, 3]})

    with pytest.raises(GameStateError):
        game.submit_response("p1", ActionType.PENG)

    game.submit_response("p3", ActionType.PENG)
    with pytest.raises(GameStateError):
        game.submit_response("p3", ActionType.PASS)


def test_t1_hu_beats_peng_and_ends_round():
    ready_hand = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5]
    game = _open_response(
        6,
        {
            "p1": ready_hand,
            "p2": [6, 6, 6],
            "p3": [],
        },
    )

    game.submit_response("p2", ActionType.PENG)
    game.submit_response("p1", ActionType.HU)
    game.submit_response("p3", ActionType.PASS)

    assert game.phase == GamePhase.ROUND_ENDED
    assert game.get_player("p1").is_winner is True
    assert game.get_player("p2").melded_groups == []
    assert game.resolved_actions[-1].action_type == ActionType.HU


def test_t1_allows_multiple_hu_responses():
    ready_hand = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5]
    game = _open_response(
        6,
        {"p1": ready_hand, "p2": ready_hand, "p3": []},
    )

    game.submit_response("p2", ActionType.HU)
    game.submit_response("p1", ActionType.HU)
    game.submit_response("p3", ActionType.PASS)

    assert game.phase == GamePhase.ROUND_ENDED
    assert game.get_player("p1").is_winner is True
    assert game.get_player("p2").is_winner is True
    assert [action.player_id for action in game.resolved_actions[-2:]] == ["p1", "p2"]


def test_listed_chi_uses_exact_group_and_enters_responder_turn():
    game = _open_response(
        3,
        {"p1": [1, "+", 2, 9], "p2": [], "p3": []},
        responder_groups={"p1": [[1, "+", 2]]},
    )
    player = game.get_player("p1")
    group = player.hand_groups[0]

    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PASS)
    game.submit_response("p3", ActionType.PASS, now=4)

    assert game.phase == GamePhase.WAITING_ACTION_DETAIL
    assert game.pending_action_detail.selection_mode == "listed"
    option = next(
        payload
        for payload in game.pending_action_detail.payload_options
        if payload["preview"] == "1 + 2 3"
    )

    assert game.submit_chi_detail("p1", option_id=option["option_id"], now=5) is True
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.current_turn_seat == 1
    assert player.hand_tiles == [9]
    assert group not in player.hand_groups
    assert player.melded_groups[-1]["group_type"] == "吃"
    assert game.resolved_actions[-1].payload["tile_selection"]["selection_source"] == "exact_group"


def test_manual_chi_requires_exactly_three_tiles_and_valid_formula():
    hand = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, "+", "×"]
    game = _open_response(6, {"p1": hand, "p2": [], "p3": []})
    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PASS)
    game.submit_response("p3", ActionType.PASS)

    detail = game.pending_action_detail
    assert detail.selection_mode == "manual"
    player = game.get_player("p1")
    selected_ids = _tile_ids_for_values(player, [1, "+", 5])

    assert game.submit_chi_detail(
        "p1",
        selected_tile_ids=selected_ids,
        detail_id=detail.detail_id,
    ) is True
    assert player.melded_groups[-1]["tiles"] == [1, "+", 5, 6]


@pytest.mark.parametrize(
    "selected_values",
    [
        [1, "+"],
        [1, 2, 3],
        None,
    ],
)
def test_invalid_or_missing_manual_chi_defaults_to_pass(selected_values):
    hand = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, "+", "×"]
    game = _open_response(6, {"p1": hand, "p2": [], "p3": []})
    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PASS)
    game.submit_response("p3", ActionType.PASS)
    selected_ids = None
    if selected_values is not None:
        selected_ids = _tile_ids_for_values(game.get_player("p1"), selected_values)

    assert game.submit_chi_detail("p1", selected_tile_ids=selected_ids) is False
    assert game.phase == GamePhase.WAITING_DRAW
    assert game.current_turn_seat == 1
    assert game.get_player("p1").melded_groups == []


def test_chi_detail_timeout_defaults_to_pass():
    hand = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, "+", "×"]
    game = _open_response(
        6,
        {"p1": hand, "p2": [], "p3": []},
        timeout_config=TimeoutConfig(action_detail_seconds=5),
        now=10,
    )
    game.submit_response("p1", ActionType.CHI, now=11)
    game.submit_response("p2", ActionType.PASS, now=11)
    game.submit_response("p3", ActionType.PASS, now=12)

    assert game.active_timer.kind == TimerKind.DETAIL_SELECTION
    assert game.active_timer.deadline_at == 17
    game.expire_timers(17)

    assert game.phase == GamePhase.WAITING_DRAW
    assert game.pending_action_detail is None


def test_t1_timeout_resolves_submitted_action_and_passes_unsubmitted_players():
    game = _open_response(
        3,
        {"p1": [3, 3, 3], "p2": [3, 3, 3], "p3": []},
        timeout_config=TimeoutConfig(discard_response_seconds=5),
        now=10,
    )
    game.submit_response("p1", ActionType.PENG, now=11)

    game.expire_timers(15)

    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.current_turn_seat == 1
    assert game.resolved_actions[-1].action_type == ActionType.PENG


def test_missing_listed_chi_option_defaults_to_pass():
    game = _open_response(3, {"p1": [1, "+", 2], "p2": [], "p3": []})
    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PASS)
    game.submit_response("p3", ActionType.PASS)

    assert game.pending_action_detail.selection_mode == "listed"
    assert game.submit_chi_detail("p1") is False
    assert game.phase == GamePhase.WAITING_DRAW


def test_t1_and_t2_freeze_hand_grouping():
    game = _open_response(3, {"p1": [1, "+", 2], "p2": [], "p3": []})
    player = game.get_player("p1")

    with pytest.raises(GameStateError):
        game.create_hand_group("p1", player.hand_tile_ids[:1])

    game.submit_response("p1", ActionType.CHI)
    game.submit_response("p2", ActionType.PASS)
    game.submit_response("p3", ActionType.PASS)
    with pytest.raises(GameStateError):
        game.move_hand_tile_by_id("p1", player.hand_tile_ids[0], 0)
