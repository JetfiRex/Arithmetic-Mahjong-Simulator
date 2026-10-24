"""
Core hand data structures for the simplified arithmetic mahjong rules.
"""

from typing import Iterable, List, Optional, Union

from calculator_base.constants import ALL_TILES, MULTIPLY, PLUS, SYMBOLS


TileValue = Union[int, str]


class Tile:
    """A single simplified-rule tile."""

    def __init__(self, value: TileValue, winning: Optional[bool] = None):
        if value not in ALL_TILES:
            raise ValueError(f"无效的牌: {value}")

        self.value = value
        self.winning = winning

        # Compatibility attributes while the complex-version modules are being
        # trimmed. New simplified-rule parsing never sets these to True.
        self.is_dora = False
        self.is_joker_used = False
        self.joker_type = None

    def __repr__(self):
        result = str(self.value)
        if self.winning:
            return f"[{result}]"
        return result

    def __str__(self):
        return self.__repr__()

    def __eq__(self, other):
        return isinstance(other, Tile) and self.value == other.value

    def __hash__(self):
        return hash(self.value)

    @staticmethod
    def infer_joker_type(value: TileValue) -> None:
        """Compatibility stub: simplified rules have no jokers."""
        return None

    def to_simple_value(self) -> TileValue:
        return self.value


class MeldedGroup:
    """
    A revealed group.

    Valid simplified-rule group types:
    - 吃: four tiles forming one arithmetic formula
    - 碰: four identical tiles
    - 明杠/暗杠/加杠: five identical tiles, with one tile outside the normal
      four-tile group count
    """

    VALID_GROUP_TYPES = {"吃", "碰", "明杠", "暗杠", "加杠"}

    def __init__(self, tiles: List[Tile], group_type: str):
        if group_type not in self.VALID_GROUP_TYPES:
            raise ValueError(f"无效的面子类型: {group_type}")

        expected_len = 5 if group_type in {"明杠", "暗杠", "加杠"} else 4
        if len(tiles) != expected_len:
            raise ValueError(f"{group_type}必须是{expected_len}张牌，实际{len(tiles)}张")
        if not self._is_valid_group_shape(tiles, group_type):
            raise ValueError(f"{group_type}牌型不合法")

        self.tiles = tiles
        self.group_type = group_type

    @classmethod
    def _is_valid_group_shape(cls, tiles: List[Tile], group_type: str) -> bool:
        values = [tile.value for tile in tiles]
        if group_type == "吃":
            return cls._is_formula_values(values)
        if group_type in {"碰", "明杠", "暗杠", "加杠"}:
            return len(set(values)) == 1
        return False

    @staticmethod
    def _is_formula_values(values: List[TileValue]) -> bool:
        symbols = [value for value in values if value in SYMBOLS]
        numbers = [value for value in values if isinstance(value, int)]
        if len(values) != 4 or len(symbols) != 1 or len(numbers) != 3:
            return False

        op = symbols[0]
        for i, a in enumerate(numbers):
            for j, b in enumerate(numbers):
                if i == j:
                    continue
                c = numbers[3 - i - j]
                if op == PLUS and a + b == c:
                    return True
                if op == MULTIPLY and a * b == c:
                    return True
        return False

    def __repr__(self):
        tiles_str = " ".join(str(t) for t in self.tiles)
        if self.group_type in {"明杠", "暗杠", "加杠"}:
            marker = self.group_type[0]
            return f"({tiles_str} {marker})"
        return f"({tiles_str})"

    def __str__(self):
        return self.__repr__()

    def tile_count(self) -> int:
        return len(self.tiles)

    def group_count_value(self) -> int:
        """Count this meld as one four-tile group for hand-size arithmetic."""
        return 4

    def __len__(self) -> int:
        return len(self.tiles)

    def __iter__(self) -> Iterable[Tile]:
        return iter(self.tiles)

    def __contains__(self, item: Tile | int | str) -> bool:
        if isinstance(item, Tile):
            return item in self.tiles
        return any(tile.value == item for tile in self.tiles)

    def to_simple_tiles(self) -> List[TileValue]:
        return [tile.to_simple_value() for tile in self.tiles]


class Hand:
    """
    A parsed hand.

    hand_tiles is used for ungrouped checks and ready checks.
    hand_groups is used when a user provides an already-grouped winning hand.
    """

    def __init__(
        self,
        melded_groups: Optional[List[MeldedGroup]] = None,
        hand_tiles: Optional[List[Tile]] = None,
        hand_groups: Optional[List[List[Tile]]] = None,
        winning_tile: Optional[Tile] = None,
        winning_method: Optional[str] = None,
        win_type: Optional[str] = None,
        should_win_in_mode: Optional[bool] = None,
    ):
        self.melded_groups = melded_groups or []
        self.hand_tiles = hand_tiles or []
        self.hand_groups = hand_groups or []
        self.winning_tile = winning_tile
        self.winning_method = winning_method
        self.win_type = win_type
        self.should_win_in_mode = should_win_in_mode

    def __repr__(self):
        parts = []

        if self.melded_groups:
            parts.append(" ".join(str(group) for group in self.melded_groups))

        if self.hand_groups:
            parts.append(
                " / ".join(" ".join(str(tile) for tile in group) for group in self.hand_groups)
            )
        elif self.hand_tiles:
            parts.append(" ".join(str(tile) for tile in self.hand_tiles))

        result = " ".join(parts)
        if self.winning_tile and self.winning_tile not in self.hand_tiles:
            result += f" [{self.winning_tile.value}]"
        if self.winning_method:
            result += f" {{{self.winning_method}}}"
        return result

    def __str__(self):
        return self.__repr__()

    def __len__(self):
        return len(self.hand_tiles)

    def total_tile_count(self) -> int:
        """Count tiles by simplified hand-size formula, excluding extra gang tiles."""
        hand_count = sum(len(group) for group in self.hand_groups) if self.hand_groups else len(self.hand_tiles)
        return len(self.melded_groups) * 4 + hand_count

    def actual_tile_count(self) -> int:
        hand_count = sum(len(group) for group in self.hand_groups) if self.hand_groups else len(self.hand_tiles)
        return sum(group.tile_count() for group in self.melded_groups) + hand_count

    def to_simple_hand(self) -> List[TileValue]:
        return [tile.to_simple_value() for tile in self.hand_tiles]

    def get_all_tiles_simple(self) -> List[TileValue]:
        result = []
        for group in self.melded_groups:
            result.extend(group.to_simple_tiles())

        if self.hand_groups:
            for group in self.hand_groups:
                result.extend(tile.to_simple_value() for tile in group)
        else:
            result.extend(self.to_simple_hand())

        return result


def create_tile_from_value(
    value: TileValue,
    is_dora: bool = False,
    is_joker_used: bool = False,
    winning: bool = False,
) -> Tile:
    """Create a Tile. Dora and joker markers are rejected in simplified rules."""
    if is_dora:
        raise ValueError("简单版没有宝牌/dora")
    if is_joker_used:
        raise ValueError("简单版没有万用牌")
    return Tile(value, winning=winning)
