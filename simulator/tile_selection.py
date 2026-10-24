"""Select concrete hand tile IDs after an action's required values are known."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional


class TileSelectionError(ValueError):
    """Raised when explicit or automatic tile selection cannot satisfy an action."""


@dataclass(frozen=True)
class TileSelectionResult:
    tile_ids: list[str]
    selection_source: str
    source_group_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_ids": list(self.tile_ids),
            "selection_source": self.selection_source,
            "source_group_ids": list(self.source_group_ids),
        }


def select_tiles(
    player: Any,
    required_values: list[Any],
    *,
    selected_tile_ids: Optional[list[str]] = None,
    allow_explicit_selection: bool = False,
) -> TileSelectionResult:
    """Choose physical tiles using explicit, exact-group, containing-group, mixed order."""
    player._ensure_hand_identity()
    required = Counter(required_values)
    values_by_id = dict(zip(player.hand_tile_ids, player.hand_tiles))

    if selected_tile_ids is not None:
        if not allow_explicit_selection:
            raise TileSelectionError("该动作不允许手动选择具体牌")
        _validate_explicit_selection(selected_tile_ids, required, values_by_id)
        return TileSelectionResult(
            tile_ids=list(selected_tile_ids),
            selection_source="explicit",
            source_group_ids=_source_group_ids(player, selected_tile_ids),
        )

    for group in player.hand_groups:
        group_values = [values_by_id[tile_id] for tile_id in group.tile_ids]
        if Counter(group_values) == required:
            return TileSelectionResult(
                tile_ids=list(group.tile_ids),
                selection_source="exact_group",
                source_group_ids=[group.group_id],
            )

    for group in player.hand_groups:
        selected = _select_from_ids(group.tile_ids, required, values_by_id)
        if len(selected) == len(required_values):
            return TileSelectionResult(
                tile_ids=selected,
                selection_source="containing_group",
                source_group_ids=[group.group_id],
            )

    return _select_by_region_order(
        player,
        required,
        values_by_id,
        selection_source="mixed",
    )


def select_tiles_by_region_order(
    player: Any,
    required_values: list[Any],
) -> TileSelectionResult:
    """Choose from loose tiles first, then groups from left to right.

    This is priority 4 of the normal automatic selector without attempting an
    exact-group or containing-group match. Jia-gang uses this rule explicitly.
    """
    player._ensure_hand_identity()
    required = Counter(required_values)
    values_by_id = dict(zip(player.hand_tile_ids, player.hand_tiles))
    return _select_by_region_order(
        player,
        required,
        values_by_id,
        selection_source="region_order",
    )


def _select_by_region_order(
    player: Any,
    required: Counter,
    values_by_id: dict[str, Any],
    *,
    selection_source: str,
) -> TileSelectionResult:
    ordered_regions = [(None, player.loose_tile_ids())] + [
        (group.group_id, group.tile_ids) for group in player.hand_groups
    ]
    remaining = required.copy()
    selected_ids: list[str] = []
    source_group_ids: list[str] = []
    for group_id, tile_ids in ordered_regions:
        region_selected = _select_from_ids(tile_ids, remaining, values_by_id)
        if region_selected and group_id is not None:
            source_group_ids.append(group_id)
        for tile_id in region_selected:
            selected_ids.append(tile_id)
            remaining[values_by_id[tile_id]] -= 1
        if not +remaining:
            break

    if +remaining:
        raise TileSelectionError("手牌无法提供动作所需的全部牌")
    return TileSelectionResult(
        tile_ids=selected_ids,
        selection_source=selection_source,
        source_group_ids=source_group_ids,
    )


def _validate_explicit_selection(
    selected_tile_ids: list[str],
    required: Counter,
    values_by_id: dict[str, Any],
) -> None:
    if len(selected_tile_ids) != sum(required.values()):
        raise TileSelectionError("手动选牌数量不正确")
    if len(selected_tile_ids) != len(set(selected_tile_ids)):
        raise TileSelectionError("不能重复选择同一张实体牌")
    if any(tile_id not in values_by_id for tile_id in selected_tile_ids):
        raise TileSelectionError("选中的牌已不在手牌中")
    if Counter(values_by_id[tile_id] for tile_id in selected_tile_ids) != required:
        raise TileSelectionError("选中的牌值与动作方案不一致")


def _select_from_ids(
    tile_ids: list[str],
    required: Counter,
    values_by_id: dict[str, Any],
) -> list[str]:
    remaining = required.copy()
    selected = []
    for tile_id in tile_ids:
        value = values_by_id[tile_id]
        if remaining[value] <= 0:
            continue
        selected.append(tile_id)
        remaining[value] -= 1
    return selected


def _source_group_ids(player: Any, tile_ids: list[str]) -> list[str]:
    selected = set(tile_ids)
    return [
        group.group_id
        for group in player.hand_groups
        if selected.intersection(group.tile_ids)
    ]
