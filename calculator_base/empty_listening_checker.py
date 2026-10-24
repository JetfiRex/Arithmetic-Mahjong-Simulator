"""
Empty-listening checks for the simplified arithmetic mahjong rules.
"""

from collections import Counter

from calculator_base.constants import TILE_COUNTS


TILE_POOL = TILE_COUNTS.copy()


def count_tiles_used(hand_tiles):
    """Count tile values already visible in a hand/list of tiles."""
    values = [tile.value if hasattr(tile, "value") else tile for tile in hand_tiles]
    return Counter(values)


def check_tile_availability(tile, tiles_used):
    """
    Return ('available', None) if at least one copy remains, else ('empty', None).
    """
    value = tile.value if hasattr(tile, "value") else tile
    total = TILE_POOL.get(value, 0)
    used = tiles_used.get(value, 0)

    if total > used:
        return "available", None
    return "empty", None


def analyze_ready_tiles(ready_tiles, hand_tiles):
    """
    Analyze whether each ready tile is still available in the simplified tile pool.
    """
    tiles_used = count_tiles_used(hand_tiles)
    result = {}

    for tile in ready_tiles:
        value = tile.value if hasattr(tile, "value") else tile
        status, _ = check_tile_availability(value, tiles_used)
        display = str(value) if status == "available" else f"{value}（空听）"
        result[value] = {
            "status": status,
            "joker_type": None,
            "display": display,
        }

    return result


def format_ready_tiles_with_status(ready_analysis):
    available = []
    empty = []

    for info in ready_analysis.values():
        if info["status"] == "available":
            available.append(info["display"])
        else:
            empty.append(info["display"])

    lines = []
    if available:
        lines.append(f"  正常听牌: {', '.join(available)}")
    if empty:
        lines.append(f"  空听: {', '.join(empty)}")
    return lines
