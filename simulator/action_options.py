"""Legal action option generation for the simulator.

This module answers "what can this player do right now?" It does not mutate
GameState. GameState can safely call these helpers when opening a response
window or after a player draws a tile.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from calculator_base.arithmetic_mahjong import ArithmeticMahjongChecker
from calculator_base.hand_structure import MeldedGroup
from simulator.actions import ActionOption, ActionType
from simulator.rule_adapter import (
    ArithmeticMahjongRuleAdapter,
    HandSnapshot,
    RuleAdapter,
    WinContext,
    WinMethod,
    WinResult,
)


ACTION_PRIORITY = {
    ActionType.HU: 40,
    ActionType.MING_GANG: 30,
    ActionType.PENG: 20,
    ActionType.CHI: 10,
    ActionType.PASS: 0,
}

CHI_OPTION_DISPLAY_LIMIT = 5

_ARITHMETIC_CHECKER = ArithmeticMahjongChecker()


def build_discard_response_options(
    *,
    player: Any,
    source_player: Any,
    discarded_tile: Any,
    wall_remaining: int,
    min_fan: int = 1,
    rule_adapter: RuleAdapter | None = None,
) -> list[ActionOption]:
    """Return response options after another player discards a tile.

    The result always includes PASS. Other options are added only when legal for
    the player's current hand: HU, MING_GANG, PENG, and CHI.
    """
    options: list[ActionOption] = []

    hu_option = build_hu_response_option(
        player=player,
        discarded_tile=discarded_tile,
        min_fan=min_fan,
        source_player_id=source_player.player_id,
        rule_adapter=rule_adapter,
    )
    if hu_option:
        options.append(hu_option)

    ming_gang_option = build_ming_gang_option(
        player=player,
        discarded_tile=discarded_tile,
        wall_remaining=wall_remaining,
    )
    if ming_gang_option:
        options.append(ming_gang_option)

    peng_option = build_peng_option(player=player, discarded_tile=discarded_tile)
    if peng_option:
        options.append(peng_option)

    chi_option = build_chi_option(
        player=player,
        source_player=source_player,
        discarded_tile=discarded_tile,
    )
    if chi_option:
        options.append(chi_option)

    options.append(ActionOption(ActionType.PASS, priority=ACTION_PRIORITY[ActionType.PASS]))
    return options


def build_turn_action_options(
    *,
    player: Any,
    wall_remaining: int,
    min_fan: int = 1,
    rule_adapter: RuleAdapter | None = None,
    allow_self_hu: bool = True,
    winning_tile: Any = None,
) -> list[ActionOption]:
    """Return options for the current player after drawing a tile.

    DISCARD is always available when the player has at least one hand tile.
    AN_GANG and JIA_GANG require a remaining wall tile because they must draw a
    replacement tile after the gang.
    """
    options: list[ActionOption] = []

    if allow_self_hu:
        hu_option = build_self_hu_option(
            player=player,
            min_fan=min_fan,
            rule_adapter=rule_adapter,
            winning_tile=winning_tile,
        )
        if hu_option:
            options.append(hu_option)

    if wall_remaining > 0:
        an_gang_option = build_an_gang_option(player=player)
        if an_gang_option:
            options.append(an_gang_option)

        jia_gang_option = build_jia_gang_option(player=player)
        if jia_gang_option:
            options.append(jia_gang_option)

    if player.hand_tiles:
        options.append(
            ActionOption(
                ActionType.DISCARD,
                priority=ACTION_PRIORITY[ActionType.PASS],
                payload_options=[
                    {
                        "hand_index": index,
                        "tile": tile,
                    }
                    for index, tile in enumerate(player.hand_tiles)
                ],
                display_hint="打牌",
            )
        )

    options.append(ActionOption(ActionType.PASS, priority=ACTION_PRIORITY[ActionType.PASS]))
    return options


def build_chi_option(
    *,
    player: Any,
    source_player: Any,
    discarded_tile: Any,
) -> ActionOption | None:
    """Return CHI when the player is the source player's next seat.

    Listed options are deduplicated by arithmetic formula. Physical duplicate
    tiles are selected later by the grouping-aware tile selector.
    """
    if player.seat != _next_seat(source_player.seat):
        return None

    payloads_by_formula: dict[tuple[Any, ...], dict[str, Any]] = {}
    for indices in combinations(range(len(player.hand_tiles)), 3):
        hand_tiles = [player.hand_tiles[index] for index in indices]
        if not is_valid_chi_selection(hand_tiles, discarded_tile):
            continue
        formula_display = _formula_display(hand_tiles, discarded_tile)
        formula_key = (
            tuple(formula_display["display_tiles"]),
            formula_display["claimed_display_index"],
        )
        payloads_by_formula.setdefault(
            formula_key,
            {
                "hand_indices": list(indices),
                "hand_tiles": hand_tiles,
                "required_values": hand_tiles,
                "claimed_tile": discarded_tile,
                "selection_mode": "listed",
                "preview": formula_display["preview"],
                "display_tiles": formula_display["display_tiles"],
                "claimed_display_index": formula_display["claimed_display_index"],
            },
        )

    payloads = list(payloads_by_formula.values())
    for option_index, payload in enumerate(payloads):
        payload["option_id"] = f"chi-{option_index}"

    if not payloads:
        return None

    requires_manual_selection = len(payloads) > CHI_OPTION_DISPLAY_LIMIT
    if requires_manual_selection:
        payload_options = [
            {
                "claimed_tile": discarded_tile,
                "selection_mode": "manual",
                "option_count": len(payloads),
                "display_limit": CHI_OPTION_DISPLAY_LIMIT,
            }
        ]
    else:
        payload_options = payloads

    return ActionOption(
        ActionType.CHI,
        priority=ACTION_PRIORITY[ActionType.CHI],
        payload_options=payload_options,
        requires_detail=True,
        display_hint="吃",
    )


def is_valid_chi_selection(hand_values: list[Any], claimed_tile: Any) -> bool:
    """Return whether exactly three hand values plus the discard form a formula."""
    return len(hand_values) == 3 and _ARITHMETIC_CHECKER.is_valid_formula(
        list(hand_values) + [claimed_tile]
    )


def build_chi_detail_payload(
    hand_values: list[Any],
    claimed_tile: Any,
) -> dict[str, Any]:
    """Build canonical display data after a listed or manual chi selection."""
    if not is_valid_chi_selection(hand_values, claimed_tile):
        raise ValueError("所选三张牌无法与弃牌组成合法算式")
    formula_display = _formula_display(hand_values, claimed_tile)
    return {
        "hand_tiles": list(hand_values),
        "required_values": list(hand_values),
        "claimed_tile": claimed_tile,
        "preview": formula_display["preview"],
        "display_tiles": formula_display["display_tiles"],
        "claimed_display_index": formula_display["claimed_display_index"],
    }


def build_peng_option(*, player: Any, discarded_tile: Any) -> ActionOption | None:
    """Return PENG when the player has at least three matching hand tiles."""
    indices = _matching_indices(player.hand_tiles, discarded_tile)
    if len(indices) < 3:
        return None

    selected = indices[:3]
    return ActionOption(
        ActionType.PENG,
        priority=ACTION_PRIORITY[ActionType.PENG],
        payload_options=[
            {
                "hand_indices": selected,
                "hand_tiles": [player.hand_tiles[index] for index in selected],
                "claimed_tile": discarded_tile,
            }
        ],
        display_hint="碰",
    )


def build_ming_gang_option(
    *,
    player: Any,
    discarded_tile: Any,
    wall_remaining: int,
) -> ActionOption | None:
    """Return MING_GANG when the player has four matches and can draw after it."""
    if wall_remaining <= 0:
        return None

    indices = _matching_indices(player.hand_tiles, discarded_tile)
    if len(indices) < 4:
        return None

    selected = indices[:4]
    return ActionOption(
        ActionType.MING_GANG,
        priority=ACTION_PRIORITY[ActionType.MING_GANG],
        payload_options=[
            {
                "hand_indices": selected,
                "hand_tiles": [player.hand_tiles[index] for index in selected],
                "claimed_tile": discarded_tile,
            }
        ],
        display_hint="明杠",
    )


def build_an_gang_option(*, player: Any) -> ActionOption | None:
    """Return one AN_GANG choice per value with at least five hand tiles."""
    payloads = []
    for tile, count in Counter(player.hand_tiles).items():
        if count < 5:
            continue
        payloads.append(
            {
                "option_id": f"an-gang-{len(payloads)}",
                "tile": tile,
                "required_values": [tile] * 5,
            }
        )

    if not payloads:
        return None

    return ActionOption(
        ActionType.AN_GANG,
        priority=ACTION_PRIORITY[ActionType.MING_GANG],
        payload_options=payloads,
        display_hint="暗杠",
    )


def build_jia_gang_option(*, player: Any) -> ActionOption | None:
    """Return one JIA_GANG choice per upgradable peng group."""
    payloads = []
    for meld_index, meld in enumerate(player.melded_groups):
        if _meld_group_type(meld) != "碰":
            continue

        tile = _meld_tile_value(meld)
        if tile is None:
            continue

        indices = _matching_indices(player.hand_tiles, tile)
        if not indices:
            continue

        payloads.append(
            {
                "option_id": f"jia-gang-{meld_index}",
                "tile": tile,
                "meld_index": meld_index,
                "required_values": [tile],
            }
        )

    if not payloads:
        return None

    return ActionOption(
        ActionType.JIA_GANG,
        priority=ACTION_PRIORITY[ActionType.MING_GANG],
        payload_options=payloads,
        display_hint="加杠",
    )


def build_hu_response_option(
    *,
    player: Any,
    discarded_tile: Any,
    min_fan: int = 1,
    source_player_id: str | None = None,
    rule_adapter: RuleAdapter | None = None,
    win_method: WinMethod = WinMethod.DISCARD,
) -> ActionOption | None:
    """Return HU when adding the discarded tile creates a winning hand."""
    adapter = rule_adapter or ArithmeticMahjongRuleAdapter(min_fan=min_fan)
    result = adapter.evaluate_win(
        HandSnapshot.from_player(player),
        WinContext(
            winner_id=player.player_id,
            winning_tile=discarded_tile,
            win_method=win_method,
            source_player_id=source_player_id,
        ),
    )
    if not result.can_win:
        return None

    return _hu_option(result)


def build_self_hu_option(
    *,
    player: Any,
    min_fan: int = 1,
    rule_adapter: RuleAdapter | None = None,
    winning_tile: Any = None,
) -> ActionOption | None:
    """Return HU when the player's current hand is already a winning hand."""
    if winning_tile is None:
        winning_tile = player.hand_tiles[-1] if player.hand_tiles else None
    adapter = rule_adapter or ArithmeticMahjongRuleAdapter(min_fan=min_fan)
    result = adapter.evaluate_win(
        HandSnapshot.from_player(player),
        WinContext(
            winner_id=player.player_id,
            winning_tile=winning_tile,
            win_method=WinMethod.SELF_DRAW,
        ),
    )
    if not result.can_win:
        return None

    return _hu_option(result)


def _hu_option(result: WinResult) -> ActionOption:
    return ActionOption(
        ActionType.HU,
        priority=ACTION_PRIORITY[ActionType.HU],
        payload_options=[result.to_action_payload()],
        requires_detail=False,
        display_hint="胡",
    )


def _meld_group_type(meld: Any) -> str | None:
    if isinstance(meld, MeldedGroup):
        return meld.group_type
    return meld.get("group_type") or meld.get("type")


def _meld_tile_value(meld: Any) -> Any:
    tiles = meld.tiles if isinstance(meld, MeldedGroup) else meld.get("tiles", [])
    if not tiles:
        return None
    first = tiles[0]
    return first.value if hasattr(first, "value") else first


def _matching_indices(tiles: list[Any], value: Any) -> list[int]:
    return [index for index, tile in enumerate(tiles) if tile == value]


def _next_seat(seat: int) -> int:
    return (seat + 1) % 4


def _formula_display(hand_tiles: list[Any], claimed_tile: Any) -> dict[str, Any]:
    """Return canonical arithmetic display data for a chi formula.

    Display order is always: smaller operand, operator, larger operand, result.
    The claimed tile can appear in any of those positions, so the payload also
    includes claimed_display_index for UI rotation/highlighting.
    """
    values = [_tile_value(tile) for tile in hand_tiles] + [_tile_value(claimed_tile)]
    symbols = [value for value in values if not isinstance(value, int)]
    numbers = [value for value in values if isinstance(value, int)]

    if len(symbols) != 1 or len(numbers) != 3:
        return _fallback_formula_display(hand_tiles, claimed_tile)

    op = symbols[0]
    for i, a in enumerate(numbers):
        for j, b in enumerate(numbers):
            if i == j:
                continue
            c = numbers[3 - i - j]
            if _formula_matches(op, a, b, c):
                left, right = sorted([a, b])
                display_tiles = [left, op, right, c]
                claimed_value = _tile_value(claimed_tile)
                claimed_display_index = _claimed_display_index(display_tiles, claimed_value)
                return {
                    "preview": " ".join(str(value) for value in display_tiles),
                    "display_tiles": display_tiles,
                    "claimed_display_index": claimed_display_index,
                }

    return _fallback_formula_display(hand_tiles, claimed_tile)


def _fallback_formula_display(hand_tiles: list[Any], claimed_tile: Any) -> dict[str, Any]:
    display_tiles = [_tile_value(tile) for tile in hand_tiles] + [_tile_value(claimed_tile)]
    return {
        "preview": " ".join(str(value) for value in display_tiles),
        "display_tiles": display_tiles,
        "claimed_display_index": len(display_tiles) - 1,
    }


def _formula_matches(op: Any, a: int, b: int, c: int) -> bool:
    return (op == "+" and a + b == c) or (op == "×" and a * b == c)


def _claimed_display_index(display_tiles: list[Any], claimed_value: Any) -> int:
    for index, value in enumerate(display_tiles):
        if value == claimed_value:
            return index
    return len(display_tiles) - 1


def _tile_value(tile: Any) -> Any:
    return tile.value if hasattr(tile, "value") else tile

