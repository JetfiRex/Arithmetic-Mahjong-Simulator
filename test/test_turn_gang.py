import pytest

from simulator.action_options import build_turn_action_options
from simulator.actions import ActionType, TimeoutConfig, TimerKind
from simulator.game_state import GamePhase, GameState, GameStateError, PlayerState, TileWall


def _player(hand_tiles, *, melded_groups=None):
    return PlayerState(
        player_id="p0",
        seat=0,
        name="玩家",
        hand_tiles=list(hand_tiles),
        melded_groups=list(melded_groups or []),
    )


def _start_t3(player, wall_tiles, *, now=10.0, timeout=5.0):
    players = [player] + [
        PlayerState(player_id=f"p{seat}", seat=seat, name=f"p{seat}")
        for seat in range(1, 4)
    ]
    game = GameState(
        players=players,
        wall=TileWall(list(wall_tiles)),
        timeout_config=TimeoutConfig(turn_action_seconds=timeout),
        current_turn_seat=0,
        phase=GamePhase.WAITING_DRAW,
    )
    game.draw_tile(player.player_id, now=now)
    return game


def _gang_option(game, action_type):
    return next(
        option
        for option in game.turn_options_by_player["p0"]
        if option.action_type == action_type
    )


def _peng(tile):
    return {
        "group_type": "碰",
        "tiles": [tile] * 4,
        "display_tiles": [{"value": tile, "orientation": "upright"}] * 3
        + [{"value": tile, "orientation": "sideways"}],
    }


def test_an_gang_uses_exact_group_draws_replacement_and_refreshes_t3():
    player = _player([3, 3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [7, 8])
    exact_ids = list(player.hand_tile_ids[:5])
    exact_group = game.create_hand_group(player.player_id, exact_ids)

    meld = game.submit_turn_action("p0", ActionType.AN_GANG, now=12)

    assert all(tile_id not in player.hand_tile_ids for tile_id in exact_ids)
    assert exact_group not in player.hand_groups
    assert player.hand_tiles.count(3) == 1
    assert player.hand_tiles[-1] == 7
    assert meld["group_type"] == "暗杠"
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.active_timer.kind == TimerKind.TURN_DECISION
    assert game.active_timer.started_at == 12
    assert game.active_timer.deadline_at == 17
    assert game.wall.remaining_count() == 0
    assert all(
        option.action_type not in (ActionType.AN_GANG, ActionType.JIA_GANG)
        for option in game.turn_options_by_player["p0"]
    )
    assert game.resolved_actions[-1].payload["tile_selection"]["selection_source"] == "exact_group"


def test_an_gang_prefers_leftmost_containing_group_over_loose_tiles():
    player = _player([3, 3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [7, 8])
    containing_ids = list(player.hand_tile_ids[:5]) + [player.hand_tile_ids[6]]
    containing_group = game.create_hand_group(player.player_id, containing_ids)

    game.submit_turn_action("p0", ActionType.AN_GANG, now=11)

    assert containing_group in player.hand_groups
    assert [player.tile_value(tile_id) for tile_id in containing_group.tile_ids] == [9]
    assert player.hand_tiles.count(3) == 1
    assert game.resolved_actions[-1].payload["tile_selection"]["selection_source"] == "containing_group"


def test_an_gang_falls_back_to_loose_then_groups_left_to_right():
    player = _player([3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [7, 8])
    original_ids = list(player.hand_tile_ids)
    first_group = game.create_hand_group("p0", original_ids[:2])
    second_group = game.create_hand_group("p0", original_ids[2:4])
    loose_three_id = original_ids[4]

    game.submit_turn_action("p0", ActionType.AN_GANG, now=11)

    selection = game.resolved_actions[-1].payload["tile_selection"]
    assert selection["tile_ids"][0] == loose_three_id
    assert selection["source_group_ids"] == [first_group.group_id, second_group.group_id]
    assert selection["selection_source"] == "mixed"


def test_jia_gang_skips_exact_group_and_removes_leftmost_loose_tile():
    player = _player([3, 3, 9], melded_groups=[_peng(3)])
    game = _start_t3(player, [7, 8])
    grouped_three_id = player.hand_tile_ids[0]
    exact_group = game.create_hand_group("p0", [grouped_three_id])
    loose_three_id = next(
        tile_id
        for tile_id in player.loose_tile_ids()
        if player.tile_value(tile_id) == 3
    )

    meld = game.submit_turn_action("p0", ActionType.JIA_GANG, now=13)

    assert loose_three_id not in player.hand_tile_ids
    assert grouped_three_id in exact_group.tile_ids
    assert meld["group_type"] == "加杠"
    assert game.resolved_actions[-1].payload["tile_selection"]["selection_source"] == "region_order"
    assert game.active_timer.deadline_at == 18


def test_jia_gang_without_loose_match_uses_leftmost_group_and_tile():
    player = _player([9, 3, 3, 3], melded_groups=[_peng(3)])
    game = _start_t3(player, [7, 8])
    ids = list(player.hand_tile_ids)
    first_group = game.create_hand_group("p0", ids[:2])
    second_group = game.create_hand_group("p0", ids[2:4])
    selected_id = first_group.tile_ids[1]

    game.submit_turn_action("p0", ActionType.JIA_GANG, now=11)

    assert selected_id not in player.hand_tile_ids
    assert first_group in player.hand_groups
    assert [player.tile_value(tile_id) for tile_id in first_group.tile_ids] == [9]
    assert second_group.tile_ids == ids[2:4]


def test_multiple_an_gang_choices_require_option_id():
    player = _player([3, 3, 3, 3, 3, 4, 4, 4, 4, 4])
    game = _start_t3(player, [7, 8])
    option = _gang_option(game, ActionType.AN_GANG)

    with pytest.raises(GameStateError, match="必须指定 option_id"):
        game.submit_turn_action("p0", ActionType.AN_GANG, now=11)

    four_choice = next(payload for payload in option.payload_options if payload["tile"] == 4)
    game.submit_turn_action(
        "p0",
        ActionType.AN_GANG,
        option_id=four_choice["option_id"],
        now=11,
    )

    assert player.hand_tiles.count(3) == 5
    assert player.hand_tiles.count(4) == 0


def test_empty_wall_does_not_offer_or_allow_gang():
    player = _player([3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [8])
    assert all(
        option.action_type != ActionType.AN_GANG
        for option in game.turn_options_by_player["p0"]
    )
    original_ids = list(player.hand_tile_ids)

    with pytest.raises(GameStateError, match="牌山已空"):
        game.submit_turn_action("p0", ActionType.AN_GANG, now=11)

    assert player.hand_tile_ids == original_ids
    assert player.melded_groups == []


def test_t3_gang_prompt_requires_pass_before_discard_without_refreshing_timer():
    player = _player([3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [7, 8], now=10, timeout=5)
    nine_id = next(
        tile_id
        for tile_id in player.hand_tile_ids
        if player.tile_value(tile_id) == 9
    )
    turn_window_id = game.turn_window_id

    with pytest.raises(GameStateError, match="请先选择杠或 PASS"):
        game.discard_tile_by_id("p0", nine_id, now=11)

    assert game.submit_turn_action(
        "p0",
        ActionType.PASS,
        now=11,
        source_turn_window_id=turn_window_id,
    ) is None
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    assert game.turn_gang_prompt_passed is True
    assert game.active_timer.started_at == 10
    assert game.active_timer.deadline_at == 15

    with pytest.raises(GameStateError, match="已跳过杠牌提示"):
        game.submit_turn_action("p0", ActionType.AN_GANG, now=12)

    game.discard_tile_by_id("p0", nine_id, now=12)

    assert game.phase == GamePhase.WAITING_RESPONSE
    assert game.turn_window_id is None
    assert game.turn_gang_prompt_passed is False


def test_gang_replacement_opens_new_t3_and_rejects_old_window_request():
    player = _player([3, 3, 3, 3, 3, 9])
    game = _start_t3(player, [7, 6, 8])
    old_window_id = game.turn_window_id

    game.submit_turn_action(
        "p0",
        ActionType.AN_GANG,
        now=11,
        source_turn_window_id=old_window_id,
    )

    assert game.turn_window_id != old_window_id
    assert game.turn_gang_prompt_passed is False
    with pytest.raises(GameStateError, match="t3 窗口已经过期"):
        game.submit_turn_action(
            "p0",
            ActionType.PASS,
            now=12,
            source_turn_window_id=old_window_id,
        )
