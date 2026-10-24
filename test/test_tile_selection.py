import pytest

from simulator.game_state import PlayerState
from simulator.tile_selection import (
    TileSelectionError,
    select_tiles,
    select_tiles_by_region_order,
)


def _player(values):
    return PlayerState(player_id="p0", seat=0, name="玩家", hand_tiles=list(values))


def test_explicit_chi_selection_has_highest_priority():
    player = _player([3, "+", 6, 3, "+", 6])
    first_group_ids = player.hand_tile_ids[:3]
    player.create_hand_group(first_group_ids)
    explicit_ids = player.loose_tile_ids()

    result = select_tiles(
        player,
        [3, "+", 6],
        selected_tile_ids=explicit_ids,
        allow_explicit_selection=True,
    )

    assert result.tile_ids == explicit_ids
    assert result.selection_source == "explicit"


def test_exact_group_beats_containing_group_and_loose_tiles():
    player = _player([3, "+", 6, 8, 3, "+", 6, 3, "+", 6])
    containing_ids = player.hand_tile_ids[:4]
    exact_ids = player.hand_tile_ids[4:7]
    player.create_hand_group(containing_ids)
    exact_group = player.create_hand_group(exact_ids)

    result = select_tiles(player, [3, "+", 6])

    assert result.tile_ids == exact_group.tile_ids
    assert result.selection_source == "exact_group"


def test_leftmost_containing_group_uses_leftmost_matching_tiles():
    player = _player([3, 3, "+", 6, 8, 3, "+", 6, 9])
    first_ids = player.hand_tile_ids[:5]
    second_ids = player.hand_tile_ids[5:]
    first_group = player.create_hand_group(first_ids)
    player.create_hand_group(second_ids)

    result = select_tiles(player, [3, "+", 6])

    assert result.tile_ids == [first_group.tile_ids[0], first_group.tile_ids[2], first_group.tile_ids[3]]
    assert result.selection_source == "containing_group"


def test_mixed_selection_uses_loose_then_groups_left_to_right():
    player = _player([3, 1, 6, 8, 2, "+", 3])
    first_group = player.create_hand_group(player.hand_tile_ids[:2])
    second_group = player.create_hand_group(player.hand_tile_ids[2:4])
    loose_ids = player.loose_tile_ids()

    result = select_tiles(player, [3, "+", 6])

    assert result.tile_ids == [loose_ids[1], loose_ids[2], second_group.tile_ids[0]]
    assert first_group.group_id not in result.source_group_ids
    assert result.selection_source == "mixed"


def test_explicit_selection_rejects_wrong_count_or_values():
    player = _player([1, "+", 2, 4])

    with pytest.raises(TileSelectionError):
        select_tiles(
            player,
            [1, "+", 2],
            selected_tile_ids=player.hand_tile_ids[:2],
            allow_explicit_selection=True,
        )
    with pytest.raises(TileSelectionError):
        select_tiles(
            player,
            [1, "+", 2],
            selected_tile_ids=[player.hand_tile_ids[0], player.hand_tile_ids[1], player.hand_tile_ids[3]],
            allow_explicit_selection=True,
        )


def test_region_order_skips_exact_group_and_prefers_loose_tile():
    player = _player([3, 3, 9])
    exact_group = player.create_hand_group([player.hand_tile_ids[0]])
    loose_three_id = next(
        tile_id
        for tile_id in player.loose_tile_ids()
        if player.tile_value(tile_id) == 3
    )

    result = select_tiles_by_region_order(player, [3])

    assert result.tile_ids == [loose_three_id]
    assert result.selection_source == "region_order"
    assert exact_group.group_id not in result.source_group_ids


def test_region_order_uses_leftmost_matching_tile_in_leftmost_group():
    player = _player([9, 3, 3, 3])
    first_group = player.create_hand_group(player.hand_tile_ids[:2])
    player.create_hand_group(player.loose_tile_ids()[:2])

    result = select_tiles_by_region_order(player, [3])

    assert result.tile_ids == [first_group.tile_ids[1]]
    assert result.source_group_ids == [first_group.group_id]
