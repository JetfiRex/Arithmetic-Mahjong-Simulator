from simulator.action_options import build_chi_option
from simulator.game_state import GameState, PlayerState, TileWall
from simulator.melds import (
    ORIENTATION_BACK,
    ORIENTATION_SIDEWAYS,
    ORIENTATION_UPRIGHT,
)


def _player(seat, hand_tiles, melded_groups=None):
    return PlayerState(
        player_id=f"p{seat}",
        seat=seat,
        name=f"p{seat}",
        hand_tiles=list(hand_tiles),
        melded_groups=list(melded_groups or []),
    )


def _game_with_players(players):
    return GameState(players=players, wall=TileWall([]))


def _orientations(meld):
    return [tile["orientation"] for tile in meld["display_tiles"]]


def test_apply_chi_removes_leftmost_values_and_marks_claimed_tile_sideways():
    source = _player(0, [])
    player = _player(1, [3, 9, "+", 6, 3])
    game = _game_with_players([source, player, _player(2, []), _player(3, [])])

    option = build_chi_option(player=player, source_player=source, discarded_tile=3)
    payload = option.payload_options[0]
    meld = game.apply_chi(player.player_id, payload)

    assert player.hand_tiles == [9, 3]
    assert meld["tiles"] == [3, "+", 3, 6]
    assert _orientations(meld) == [
        ORIENTATION_SIDEWAYS,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
    ]


def test_apply_peng_removes_leftmost_three_and_marks_rightmost_sideways():
    player = _player(0, [3, 9, 3, 3, 3])
    game = _game_with_players([player, _player(1, []), _player(2, []), _player(3, [])])

    meld = game.apply_peng(player.player_id, 3)

    assert player.hand_tiles == [9, 3]
    assert meld["tiles"] == [3, 3, 3, 3]
    assert _orientations(meld) == [
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_SIDEWAYS,
    ]


def test_apply_ming_gang_removes_leftmost_four_and_marks_rightmost_sideways():
    player = _player(0, [3, 9, 3, 3, 3, 3])
    game = _game_with_players([player, _player(1, []), _player(2, []), _player(3, [])])

    meld = game.apply_ming_gang(player.player_id, 3)

    assert player.hand_tiles == [9, 3]
    assert meld["tiles"] == [3, 3, 3, 3, 3]
    assert _orientations(meld) == [
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_SIDEWAYS,
    ]


def test_apply_an_gang_removes_leftmost_five_and_uses_back_edges():
    player = _player(0, [3, 3, 9, 3, 3, 3, 3])
    game = _game_with_players([player, _player(1, []), _player(2, []), _player(3, [])])

    meld = game.apply_an_gang(player.player_id, 3)

    assert player.hand_tiles == [9, 3]
    assert meld["tiles"] == [3, 3, 3, 3, 3]
    assert _orientations(meld) == [
        ORIENTATION_BACK,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_BACK,
    ]


def test_apply_jia_gang_removes_leftmost_one_and_adds_upright_rightmost_tile():
    player = _player(
        0,
        [3, 9, 3],
        melded_groups=[
            {
                "group_type": "碰",
                "tiles": [3, 3, 3, 3],
                "display_tiles": [
                    {"value": 3, "orientation": ORIENTATION_UPRIGHT},
                    {"value": 3, "orientation": ORIENTATION_UPRIGHT},
                    {"value": 3, "orientation": ORIENTATION_UPRIGHT},
                    {"value": 3, "orientation": ORIENTATION_SIDEWAYS},
                ],
            }
        ],
    )
    game = _game_with_players([player, _player(1, []), _player(2, []), _player(3, [])])

    meld = game.apply_jia_gang(player.player_id, meld_index=0, tile=3)

    assert player.hand_tiles == [9, 3]
    assert meld["group_type"] == "加杠"
    assert meld["tiles"] == [3, 3, 3, 3, 3]
    assert _orientations(meld) == [
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_UPRIGHT,
        ORIENTATION_SIDEWAYS,
        ORIENTATION_UPRIGHT,
    ]
