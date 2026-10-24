from simulator.action_options import (
    build_an_gang_option,
    build_chi_option,
    build_hu_response_option,
    build_jia_gang_option,
    build_ming_gang_option,
    build_peng_option,
    build_self_hu_option,
)
from simulator.actions import ActionType
from simulator.game_state import PlayerState


def _player(seat, hand_tiles, melded_groups=None):
    return PlayerState(
        player_id=f"p{seat}",
        seat=seat,
        name=f"p{seat}",
        hand_tiles=list(hand_tiles),
        melded_groups=list(melded_groups or []),
    )


def test_chi_option_only_for_next_seat_and_uses_hand_indices():
    source = _player(0, [])
    player = _player(1, [1, "+", 2, 9])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=3,
    )

    assert option.action_type == ActionType.CHI
    assert option.requires_detail is True
    assert option.payload_options[0]["hand_indices"] == [0, 1, 2]
    assert option.payload_options[0]["claimed_tile"] == 3
    assert option.payload_options[0]["preview"] == "1 + 2 3"
    assert option.payload_options[0]["display_tiles"] == [1, "+", 2, 3]
    assert option.payload_options[0]["claimed_display_index"] == 3


def test_chi_option_rejects_non_next_seat():
    source = _player(0, [])
    player = _player(2, [1, "+", 2])

    assert build_chi_option(player=player, source_player=source, discarded_tile=3) is None


def test_chi_options_deduplicate_physical_copies_of_same_formula():
    source = _player(0, [])
    player = _player(1, [1, 1, "+", 2, 2])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=3,
    )

    assert option.payload_options[0]["selection_mode"] == "listed"
    assert len(option.payload_options) == 1
    assert option.payload_options[0]["required_values"] == [1, "+", 2]


def test_more_than_five_unique_chi_formulas_use_manual_selection():
    source = _player(0, [])
    player = _player(1, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, "+", "×"])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=6,
    )

    assert option.payload_options[0]["selection_mode"] == "manual"
    assert option.payload_options[0]["option_count"] > 5


def test_at_most_five_chi_formulas_are_all_listed():
    source = _player(0, [])
    player = _player(1, [1, 2, 4, 5, "+"])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=3,
    )

    assert {payload["preview"] for payload in option.payload_options} == {
        "1 + 2 3",
        "1 + 3 4",
        "2 + 3 5",
    }
    assert all(
        payload["selection_mode"] == "listed"
        for payload in option.payload_options
    )


def test_chi_preview_marks_claimed_operand_position():
    source = _player(0, [])
    player = _player(1, ["+", 6, 9])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=3,
    )

    assert option.payload_options[0]["preview"] == "3 + 6 9"
    assert option.payload_options[0]["display_tiles"] == [3, "+", 6, 9]
    assert option.payload_options[0]["claimed_display_index"] == 0


def test_chi_preview_marks_claimed_duplicate_value_at_leftmost_position():
    source = _player(0, [])
    player = _player(1, [3, "+", 6])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=3,
    )

    assert option.payload_options[0]["preview"] == "3 + 3 6"
    assert option.payload_options[0]["display_tiles"] == [3, "+", 3, 6]
    assert option.payload_options[0]["claimed_display_index"] == 0


def test_chi_preview_marks_claimed_duplicate_zero_at_leftmost_position():
    source = _player(0, [])
    player = _player(1, [0, "+", 0])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=0,
    )

    assert option.payload_options[0]["preview"] == "0 + 0 0"
    assert option.payload_options[0]["display_tiles"] == [0, "+", 0, 0]
    assert option.payload_options[0]["claimed_display_index"] == 0


def test_chi_preview_marks_claimed_non_left_duplicate_by_canonical_order():
    source = _player(0, [])
    player = _player(1, [0, "+", 1])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=1,
    )

    assert option.payload_options[0]["preview"] == "0 + 1 1"
    assert option.payload_options[0]["display_tiles"] == [0, "+", 1, 1]
    assert option.payload_options[0]["claimed_display_index"] == 2


def test_chi_preview_marks_claimed_zero_multiplier_as_left_factor():
    source = _player(0, [])
    player = _player(1, [0, "×", 1])

    option = build_chi_option(
        player=player,
        source_player=source,
        discarded_tile=0,
    )

    assert option.payload_options[0]["preview"] == "0 × 1 0"
    assert option.payload_options[0]["display_tiles"] == [0, "×", 1, 0]
    assert option.payload_options[0]["claimed_display_index"] == 0


def test_peng_option_uses_leftmost_three_matching_tiles():
    player = _player(1, [3, 9, 3, 3, 3])

    option = build_peng_option(player=player, discarded_tile=3)

    assert option.action_type == ActionType.PENG
    assert option.payload_options[0]["hand_indices"] == [0, 2, 3]


def test_ming_gang_requires_wall_and_uses_leftmost_four_matching_tiles():
    player = _player(1, [3, 9, 3, 3, 3, 3])

    option = build_ming_gang_option(
        player=player,
        discarded_tile=3,
        wall_remaining=1,
    )

    assert option.action_type == ActionType.MING_GANG
    assert option.payload_options[0]["hand_indices"] == [0, 2, 3, 4]
    assert build_ming_gang_option(
        player=player,
        discarded_tile=3,
        wall_remaining=0,
    ) is None


def test_an_gang_lists_one_choice_per_matching_value():
    player = _player(0, [3, 3, 9, 3, 3, 3, 3])

    option = build_an_gang_option(player=player)

    assert option.action_type == ActionType.AN_GANG
    assert option.payload_options[0]["option_id"] == "an-gang-0"
    assert option.payload_options[0]["tile"] == 3
    assert option.payload_options[0]["required_values"] == [3] * 5


def test_jia_gang_lists_the_upgradable_peng_group():
    player = _player(
        0,
        [2, 3, 3],
        melded_groups=[{"group_type": "碰", "tiles": [3, 3, 3, 3]}],
    )

    option = build_jia_gang_option(player=player)

    assert option.action_type == ActionType.JIA_GANG
    assert option.payload_options[0]["option_id"] == "jia-gang-0"
    assert option.payload_options[0]["meld_index"] == 0
    assert option.payload_options[0]["required_values"] == [3]


def test_hu_response_option_uses_calculator_base_checker():
    player = _player(
        1,
        [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5],
    )

    option = build_hu_response_option(player=player, discarded_tile=6)

    assert option.action_type == ActionType.HU
    assert option.payload_options[0]["win_type"] is not None
    assert option.payload_options[0]["winning_method"] == "点胡"


def test_self_hu_option_uses_calculator_base_checker():
    player = _player(
        0,
        [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 6, 5, 5],
    )

    option = build_self_hu_option(player=player)

    assert option.action_type == ActionType.HU
    assert option.payload_options[0]["winning_method"] == "自摸"
