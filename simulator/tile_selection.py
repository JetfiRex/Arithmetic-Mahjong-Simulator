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

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_ids": list(self.tile_ids),
            "selection_source": self.selection_source,
        }


def select_tiles(
    player: Any,
    required_values: list[Any],
    *,
    selected_tile_ids: Optional[list[str]] = None,
    allow_explicit_selection: bool = False,
) -> TileSelectionResult:
    """Use explicit IDs when allowed; otherwise select from left to right."""
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
        )

    selected_ids = _select_from_ids(player.hand_tile_ids, required, values_by_id)
    if len(selected_ids) != len(required_values):
        raise TileSelectionError("手牌无法提供动作所需的全部牌")
    return TileSelectionResult(
        tile_ids=selected_ids,
        selection_source="hand_order",
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
