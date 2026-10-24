from copy import deepcopy

import pytest

from simulator.game_state import GamePhase, GameState, GameStateError, PlayerState, TileWall


HAND_COMMANDS = (
    "reorder_hand_tile",
    "move_hand_tile_by_id",
    "create_hand_group",
    "disband_hand_group",
    "discard_tile_by_id",
    "discard_tile_at",
    "discard_tile",
)


@pytest.fixture
def game():
    players = [
        PlayerState(
            player_id=f"p{seat}",
            seat=seat,
            name=f"p{seat}",
            hand_tiles=[3, 4, 3, 5, 6, 7],
        )
        for seat in range(4)
    ]
    state = GameState(
        players=players,
        wall=TileWall([8, 9, 10]),
        phase=GamePhase.WAITING_DRAW,
    )
    state.draw_tile("p0", now=10)
    return state


def _hand_snapshot(player):
    return (
        list(player.hand_tiles),
        list(player.hand_tile_ids),
        list(player._identity_values),
        [
            (group.group_id, group.created_order, list(group.tile_ids))
            for group in player.hand_groups
        ],
    )


def _game_snapshot(game):
    return (
        [_hand_snapshot(player) for player in game.players],
        [list(player.discarded_tiles) for player in game.players],
        deepcopy(game.event_log),
        list(game.discard_history),
        game.last_discard,
        game.phase,
        game.turn_window_id,
        deepcopy(game.active_timer),
        list(game.wall.tiles),
    )


def _invoke_hand_command(game, command, player, group, **kwargs):
    tile_id = group.tile_ids[0]
    args = {
        "reorder_hand_tile": (0, len(player.hand_tiles) - 1),
        "move_hand_tile_by_id": (tile_id, 0),
        "create_hand_group": ([player.hand_tile_ids[-1]],),
        "disband_hand_group": (group.group_id,),
        "discard_tile_by_id": (tile_id,),
        "discard_tile_at": (0,),
        "discard_tile": (player.hand_tiles[0],),
    }
    return getattr(game, command)(player.player_id, *args[command], **kwargs)


@pytest.mark.parametrize("command", HAND_COMMANDS)
def test_stale_commands_do_not_mutate_a_new_t3(game, command):
    old_window_id = game.turn_window_id
    game.discard_tile_at("p0", 0, now=11)
    for player_id in list(game.response_window.players()):
        game.pass_response(player_id, now=11)
    game.draw_tile("p1", now=12)
    assert game.turn_window_id != old_window_id

    player = game.current_player()
    group = game.create_hand_group(player.player_id, player.hand_tile_ids[:2])
    before = _game_snapshot(game)

    with pytest.raises(GameStateError, match="t3 窗口已经过期"):
        _invoke_hand_command(
            game, command, player, group, source_turn_window_id=old_window_id
        )

    assert _game_snapshot(game) == before


@pytest.mark.parametrize("command", HAND_COMMANDS)
def test_stale_check_precedes_hand_identity_synchronization(game, command):
    player = game.current_player()
    group = player.create_hand_group(player.hand_tile_ids[:2])
    # Legacy direct assignments normally regenerate IDs and clear groups.
    player.hand_tiles = list(reversed(player.hand_tiles))
    before = _game_snapshot(game)

    with pytest.raises(GameStateError, match="t3 窗口已经过期"):
        _invoke_hand_command(
            game, command, player, group, source_turn_window_id="old-t3"
        )

    assert _game_snapshot(game) == before


@pytest.mark.parametrize("include_window_id", [False, True])
def test_organization_commands_allow_other_players_during_t3(game, include_window_id):
    player = game.get_player("p1")
    ids = list(player.hand_tile_ids)
    values_by_id = dict(zip(ids, player.hand_tiles))
    kwargs = {"source_turn_window_id": game.turn_window_id} if include_window_id else {}

    group = game.create_hand_group("p1", ids[:2], **kwargs)
    game.move_hand_tile_by_id("p1", ids[2], 1, group_id=group.group_id, **kwargs)
    assert group.tile_ids == [ids[0], ids[2], ids[1]]

    game.move_hand_tile_by_id("p1", ids[0], 1, **kwargs)
    assert group.tile_ids == [ids[2], ids[1]]
    assert player.loose_tile_ids() == [ids[3], ids[0], ids[4], ids[5]]

    assert game.disband_hand_group("p1", group.group_id, **kwargs) is group
    game.reorder_hand_tile("p1", 0, 5, **kwargs)

    assert player.hand_groups == []
    assert player.hand_tile_ids == [ids[1], ids[3], ids[0], ids[4], ids[5], ids[2]]
    assert player.hand_tiles == [values_by_id[tile_id] for tile_id in player.hand_tile_ids]
    assert game.current_player().player_id == "p0"
    assert game.phase == GamePhase.WAITING_TURN_ACTION


@pytest.mark.parametrize("include_window_id", [False, True])
@pytest.mark.parametrize("command", HAND_COMMANDS[-3:])
def test_discard_commands_preserve_identity_and_clean_single_tile_group(
    game, command, include_window_id
):
    player = game.current_player()
    ids = list(player.hand_tile_ids)
    group = game.create_hand_group("p0", [ids[2]])
    kwargs = {"source_turn_window_id": game.turn_window_id} if include_window_id else {}

    record = _invoke_hand_command(game, command, player, group, **kwargs)

    assert record.tile == 3
    assert record.tile_id == ids[2]
    assert player.hand_tile_ids == ids[:2] + ids[3:]
    assert player.hand_tiles == [3, 4, 5, 6, 7, 10]
    assert player.hand_groups == []
    assert game.phase == GamePhase.WAITING_RESPONSE


@pytest.mark.parametrize("through_game", [False, True])
@pytest.mark.parametrize(
    "source_index,target,index,error",
    [
        (0, "second", -1, GameStateError),
        (0, "second", 3, GameStateError),
        (1, "second", 2, GameStateError),
        (0, "loose", -1, GameStateError),
        (0, "loose", 5, GameStateError),
        (3, "loose", 4, GameStateError),
        (0, "second", 0.5, TypeError),
        (0, "loose", 0.5, TypeError),
        (0, "missing", 0, GameStateError),
        (None, "second", 0, GameStateError),
    ],
)
def test_invalid_move_preserves_values_ids_and_group_membership(
    game, through_game, source_index, target, index, error
):
    player = game.current_player()
    ids = list(player.hand_tile_ids)
    first = game.create_hand_group("p0", [ids[0]])
    second = game.create_hand_group("p0", ids[1:3])
    group_id = {"second": second.group_id, "loose": None, "missing": "missing"}[target]
    tile_id = ids[source_index] if source_index is not None else "missing-tile"
    before = _game_snapshot(game)

    with pytest.raises(error):
        if through_game:
            game.move_hand_tile_by_id(
                "p0", tile_id, index, group_id=group_id,
                source_turn_window_id=game.turn_window_id,
            )
        else:
            player.move_tile_by_id(tile_id, index, group_id=group_id)

    assert _game_snapshot(game) == before
    assert player.hand_groups == [first, second]
    assert first.tile_ids == [ids[0]]
    assert second.tile_ids == ids[1:3]


@pytest.mark.parametrize("destination", ["group", "loose", "same"])
def test_single_tile_group_survives_self_move_and_is_cleaned_after_leaving(
    game, destination
):
    player = game.current_player()
    ids = list(player.hand_tile_ids)
    values_by_id = dict(zip(ids, player.hand_tiles))
    first = game.create_hand_group("p0", [ids[0]])
    second = game.create_hand_group("p0", ids[1:3])
    group_id = {"group": second.group_id, "loose": None, "same": first.group_id}[destination]

    game.move_hand_tile_by_id(
        "p0", ids[0], 0, group_id=group_id,
        source_turn_window_id=game.turn_window_id,
    )

    if destination == "same":
        assert player.hand_groups == [first, second]
        assert first.tile_ids == [ids[0]]
        assert player.hand_tile_ids == ids
    else:
        assert player.hand_groups == [second]
        if destination == "group":
            assert second.tile_ids == ids[:3]
            assert player.loose_tile_ids() == ids[3:]
        else:
            assert second.tile_ids == ids[1:3]
            assert player.loose_tile_ids() == [ids[0]] + ids[3:]
    assert set(player.hand_tile_ids) == set(ids)
    assert player.hand_tiles == [values_by_id[tile_id] for tile_id in player.hand_tile_ids]


def test_within_region_moves_use_post_removal_indices(game):
    player = game.current_player()
    ids = list(player.hand_tile_ids)
    group = game.create_hand_group("p0", ids[:3])

    player.move_tile_by_id(ids[0], 2, group_id=group.group_id)
    player.move_tile_by_id(ids[3], 3)

    assert group.tile_ids == [ids[1], ids[2], ids[0]]
    assert player.loose_tile_ids() == [ids[4], ids[5], ids[6], ids[3]]
    assert player.hand_tile_ids == [ids[1], ids[2], ids[0], ids[4], ids[5], ids[6], ids[3]]
    assert player.hand_tiles == [4, 3, 3, 6, 7, 10, 5]


def test_noninteger_reorder_destination_is_atomic(game):
    before = _game_snapshot(game)

    with pytest.raises(TypeError):
        game.reorder_hand_tile(
            "p0", 0, 0.5, source_turn_window_id=game.turn_window_id
        )

    assert _game_snapshot(game) == before


@pytest.mark.parametrize(
    "command,args",
    [
        ("reorder_hand_tile", (-1, 0)),
        ("reorder_hand_tile", (0, 99)),
        ("create_hand_group", ([],)),
        ("create_hand_group", (["missing"],)),
        ("disband_hand_group", ("missing",)),
        ("discard_tile_by_id", ("missing",)),
        ("discard_tile_at", (-1,)),
        ("discard_tile_at", (99,)),
        ("discard_tile", (99,)),
    ],
)
def test_invalid_hand_command_does_not_mutate_or_log(game, command, args):
    before = _game_snapshot(game)

    with pytest.raises(GameStateError):
        getattr(game, command)("p0", *args, source_turn_window_id=game.turn_window_id)

    assert _game_snapshot(game) == before
