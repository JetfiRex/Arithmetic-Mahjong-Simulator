import random

import pytest

from simulator.actions import TimeoutConfig
from simulator.game_state import GamePhase, GameState, GameStateError, PlayerState


def _player(values):
    return PlayerState(player_id="p0", seat=0, name="玩家", hand_tiles=list(values))


def test_duplicate_tiles_keep_distinct_ids_when_reordered():
    player = _player([3, 4, 3])
    first_three_id = player.tile_id_at(0)
    second_three_id = player.tile_id_at(2)

    player.move_tile(2, 0)

    assert first_three_id != second_three_id
    assert player.hand_tiles == [3, 3, 4]
    assert player.hand_tile_ids == [second_three_id, first_three_id, player.tile_id_at(2)]


def test_groups_are_ordered_before_loose_tiles_without_changing_values():
    player = _player([1, 2, 3, 4, 5])
    ids_by_value = dict(zip(player.hand_tiles, player.hand_tile_ids))

    first_group = player.create_hand_group([ids_by_value[3], ids_by_value[1]])
    second_group = player.create_hand_group([ids_by_value[5], ids_by_value[4]])

    assert first_group.tile_ids == [ids_by_value[1], ids_by_value[3]]
    assert second_group.tile_ids == [ids_by_value[4], ids_by_value[5]]
    assert player.hand_tile_ids == [
        ids_by_value[1],
        ids_by_value[3],
        ids_by_value[4],
        ids_by_value[5],
        ids_by_value[2],
    ]
    assert player.hand_tiles == [1, 3, 4, 5, 2]
    assert sorted(player.hand_tiles) == [1, 2, 3, 4, 5]
    assert player.loose_tile_ids() == [ids_by_value[2]]


def test_regrouping_moves_selected_tiles_and_removes_empty_old_group():
    player = _player([1, 2, 3, 4])
    tile_ids = list(player.hand_tile_ids)
    old_group = player.create_hand_group(tile_ids[:2])

    new_group = player.create_hand_group([tile_ids[0], tile_ids[1], tile_ids[2]])

    assert old_group not in player.hand_groups
    assert player.hand_groups == [new_group]
    assert new_group.tile_ids == tile_ids[:3]
    assert player.loose_tile_ids() == [tile_ids[3]]


def test_disbanded_group_returns_to_left_edge_of_loose_region():
    player = _player([1, 2, 3, 4, 5])
    tile_ids = list(player.hand_tile_ids)
    first_group = player.create_hand_group([tile_ids[0], tile_ids[1]])
    second_group = player.create_hand_group([tile_ids[2], tile_ids[3]])

    player.disband_hand_group(first_group.group_id)

    assert player.hand_groups == [second_group]
    assert player.hand_tile_ids == [tile_ids[2], tile_ids[3], tile_ids[0], tile_ids[1], tile_ids[4]]
    assert player.loose_tile_ids() == [tile_ids[0], tile_ids[1], tile_ids[4]]


def test_tile_can_move_within_group_and_back_to_loose_region():
    player = _player([1, 2, 3, 4])
    tile_ids = list(player.hand_tile_ids)
    group = player.create_hand_group(tile_ids[:3])

    player.move_tile_by_id(tile_ids[2], 0, group_id=group.group_id)
    player.move_tile_by_id(tile_ids[0], 0)

    assert group.tile_ids == [tile_ids[2], tile_ids[1]]
    assert player.loose_tile_ids() == [tile_ids[0], tile_ids[3]]
    assert player.hand_tile_ids == [tile_ids[2], tile_ids[1], tile_ids[0], tile_ids[3]]


def test_discard_by_id_removes_exact_duplicate_and_cleans_empty_group():
    game = GameState.new_game(rng=random.Random(11), dealer_seat=0)
    player = game.current_player()
    player.hand_tiles = [3, 4, 3]
    game.phase = GamePhase.WAITING_TURN_ACTION
    first_three_id = player.tile_id_at(0)
    second_three_id = player.tile_id_at(2)
    group = game.create_hand_group(player.player_id, [second_three_id])

    record = game.discard_tile_by_id(player.player_id, second_three_id)

    assert record.tile == 3
    assert record.tile_id == second_three_id
    assert player.hand_tiles == [3, 4]
    assert player.hand_tile_ids == [first_three_id, player.tile_id_at(1)]
    assert group not in player.hand_groups


def test_player_view_contains_own_groups_but_not_other_players_groups():
    game = GameState.new_game(rng=random.Random(12))
    player = game.get_player("p0")
    game.phase = GamePhase.WAITING_TURN_ACTION
    group = game.create_hand_group("p0", player.hand_tile_ids[:3])

    snapshot = game.to_public_dict(viewer_player_id="p0")

    assert snapshot["players"][0]["hand_groups"][0]["group_id"] == group.group_id
    assert "hand_tile_records" in snapshot["players"][0]
    assert "hand_groups" not in snapshot["players"][1]
    assert "hand_tile_records" not in snapshot["players"][1]
    assert any(event["type"] == "hand_group_created" for event in snapshot["event_log"])

    anonymous_snapshot = game.to_public_dict()
    assert not any(
        event["type"] == "hand_group_created"
        for event in anonymous_snapshot["event_log"]
    )


def test_meld_removal_updates_group_membership_and_deletes_empty_group():
    game = GameState.new_game(rng=random.Random(14))
    player = game.get_player("p0")
    player.hand_tiles = [3, 3, 3, 9]
    grouped_ids = [player.tile_id_at(index) for index in range(3)]
    game.phase = GamePhase.WAITING_TURN_ACTION
    group = game.create_hand_group(player.player_id, grouped_ids)

    game.apply_peng(player.player_id, 3)

    assert player.hand_tiles == [9]
    assert group not in player.hand_groups


def test_turn_timeout_discards_visual_rightmost_loose_tile_after_grouping():
    game = GameState.new_game(
        rng=random.Random(13),
        dealer_seat=0,
        timeout_config=TimeoutConfig(turn_action_seconds=3),
    )
    player = game.current_player()
    game.draw_tile(player.player_id, now=20)
    player.hand_tiles = [1, 2, 3, 4]
    tile_ids = [player.tile_id_at(index) for index in range(4)]
    game.create_hand_group(player.player_id, [tile_ids[3]])

    game.expire_timers(23)

    assert player.hand_tiles == [4, 1, 2]
    assert player.discarded_tiles[-1] == 3
    assert game.last_discard.tile_id == tile_ids[2]


def test_group_rejects_unknown_or_duplicate_tile_ids():
    player = _player([1, 2, 3])

    with pytest.raises(GameStateError):
        player.create_hand_group(["not-in-hand"])
    with pytest.raises(GameStateError):
        player.create_hand_group([player.tile_id_at(0), player.tile_id_at(0)])
