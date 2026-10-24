"""Meld execution helpers.

These helpers mutate PlayerState hand/meld data after GameState has already
decided an action is legal. Meld records keep `tiles` as plain values for rule
adapters, and store UI orientation in `display_tiles`.
"""

from __future__ import annotations

from typing import Any


ORIENTATION_UPRIGHT = "upright"
ORIENTATION_SIDEWAYS = "sideways"
ORIENTATION_BACK = "back"


def remove_leftmost_values(hand_tiles: list[Any], values: list[Any]) -> list[Any]:
    """Remove the leftmost matching hand tiles for each requested value.

    If a hand contains `a` copies of x and the meld consumes `b` copies, this
    removes the leftmost `b` copies. This is the default simulator rule for
    chi/peng/gang execution; discarding still uses exact hand index elsewhere.
    """
    removed = []
    for value in values:
        for index, tile in enumerate(hand_tiles):
            if tile == value:
                removed.append(hand_tiles.pop(index))
                break
        else:
            raise ValueError(f"手牌中没有可移除的牌: {value}")
    return removed


def make_chi_meld(display_tiles: list[Any], claimed_display_index: int) -> dict[str, Any]:
    """Create a chi meld; the claimed tile is sideways at its display position."""
    return {
        "group_type": "吃",
        "tiles": list(display_tiles),
        "display_tiles": [
            _display_tile(
                tile,
                ORIENTATION_SIDEWAYS
                if index == claimed_display_index
                else ORIENTATION_UPRIGHT,
            )
            for index, tile in enumerate(display_tiles)
        ],
        "claimed_display_index": claimed_display_index,
    }


def make_peng_meld(tile: Any) -> dict[str, Any]:
    """Create a peng meld; the claimed tile is the rightmost sideways tile."""
    tiles = [tile] * 4
    return {
        "group_type": "碰",
        "tiles": tiles,
        "display_tiles": [
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_SIDEWAYS),
        ],
        "claimed_display_index": 3,
    }


def make_ming_gang_meld(tile: Any) -> dict[str, Any]:
    """Create a ming-gang meld; the claimed tile is rightmost and sideways."""
    tiles = [tile] * 5
    return {
        "group_type": "明杠",
        "tiles": tiles,
        "display_tiles": [
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_SIDEWAYS),
        ],
        "claimed_display_index": 4,
    }


def make_an_gang_meld(tile: Any) -> dict[str, Any]:
    """Create an an-gang meld; left/right tiles are face down, no sideways tile."""
    tiles = [tile] * 5
    return {
        "group_type": "暗杠",
        "tiles": tiles,
        "display_tiles": [
            _display_tile(tile, ORIENTATION_BACK),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_UPRIGHT),
            _display_tile(tile, ORIENTATION_BACK),
        ],
    }


def upgrade_peng_to_jia_gang(meld: dict[str, Any], tile: Any) -> dict[str, Any]:
    """Upgrade a peng meld into jia-gang by adding an upright rightmost tile."""
    meld["group_type"] = "加杠"
    meld.setdefault("tiles", []).append(tile)
    meld.setdefault("display_tiles", []).append(_display_tile(tile, ORIENTATION_UPRIGHT))
    return meld


def _display_tile(value: Any, orientation: str) -> dict[str, Any]:
    return {
        "value": value,
        "orientation": orientation,
    }
