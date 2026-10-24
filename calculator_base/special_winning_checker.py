"""
Special winning shape checks for the simplified arithmetic mahjong rules.

Only three special shapes live here:
- 七小对: seven pairs
- 连七对: seven pairs whose values form an arithmetic progression
- 清龙: twelve consecutive numbers plus one pair
"""

from collections import Counter
from typing import List, Tuple

from calculator_base.constants import ALL_TILES


def tile_value(tile):
    return tile.value if hasattr(tile, "value") else tile


class SevenPairsChecker:
    """
    Seven-pairs checker.

    The old complex-version method names used "eight_pairs"; they are retained
    as aliases while the caller modules are being renamed.
    """

    def can_win_seven_pairs(self, tiles_list):
        if len(tiles_list) != 14:
            return False, []

        value_to_tiles = {}
        for tile in tiles_list:
            value_to_tiles.setdefault(tile_value(tile), []).append(tile)

        pairs = []
        for tiles in value_to_tiles.values():
            if len(tiles) % 2 != 0:
                return False, []
            for idx in range(0, len(tiles), 2):
                pairs.append(list(tiles[idx : idx + 2]))

        return len(pairs) == 7, pairs if len(pairs) == 7 else []

    def is_ready_seven_pairs(self, tiles_list):
        if len(tiles_list) != 13:
            return False, []

        values = [tile_value(tile) for tile in tiles_list]
        counter = Counter(values)
        singles = [value for value, count in counter.items() if count % 2 == 1]

        if len(singles) == 1:
            return True, singles

        return False, []

    def can_win_eight_pairs(self, tiles_list):
        return self.can_win_seven_pairs(tiles_list)

    def is_ready_eight_pairs(self, tiles_list):
        return self.is_ready_seven_pairs(tiles_list)


class SpecialWinningChecker:
    """Checker for 七小对, 连七对, and 清龙."""

    def _extract_all_tiles(self, hand):
        if isinstance(hand, list):
            return hand

        tiles = []

        if hasattr(hand, "hand_groups") and hand.hand_groups:
            for group in hand.hand_groups:
                tiles.extend(group)
        elif hasattr(hand, "hand_tiles"):
            tiles.extend(hand.hand_tiles)

        if hasattr(hand, "melded_groups"):
            for group in hand.melded_groups:
                tiles.extend(group.tiles)

        return tiles

    def can_win_qi_xiao_dui(self, hand) -> Tuple[bool, List]:
        return SevenPairsChecker().can_win_seven_pairs(self._extract_all_tiles(hand))

    def is_ready_qi_xiao_dui(self, hand) -> Tuple[bool, List]:
        return SevenPairsChecker().is_ready_seven_pairs(self._extract_all_tiles(hand))

    def can_win_lian_qi_dui(self, hand) -> Tuple[bool, List]:
        can_win, pairs = self.can_win_qi_xiao_dui(hand)
        if not can_win:
            return False, []

        pair_values = []
        for pair in pairs:
            value = tile_value(pair[0])
            if not isinstance(value, int):
                return False, []
            pair_values.append(value)

        pair_values.sort()
        if len(set(pair_values)) != 7:
            return False, []

        diff = pair_values[1] - pair_values[0]
        if diff <= 0:
            return False, []

        for idx in range(1, len(pair_values)):
            if pair_values[idx] - pair_values[idx - 1] != diff:
                return False, []

        return True, pairs

    def is_ready_lian_qi_dui(self, hand) -> Tuple[bool, List]:
        tiles = self._extract_all_tiles(hand)
        if len(tiles) != 13:
            return False, []

        ready_tiles = []
        for tile in ALL_TILES:
            if not isinstance(tile, int):
                continue
            can_win, _ = self.can_win_lian_qi_dui(list(tiles) + [tile])
            if can_win:
                ready_tiles.append(tile)

        return bool(ready_tiles), sorted(set(ready_tiles))

    def can_win_qing_long(self, hand) -> Tuple[bool, List]:
        tiles = list(self._extract_all_tiles(hand))
        if len(tiles) != 14:
            return False, []

        value_to_tiles = {}
        for tile in tiles:
            value_to_tiles.setdefault(tile_value(tile), []).append(tile)

        pair_candidates = [
            value for value, same_tiles in value_to_tiles.items() if len(same_tiles) >= 2
        ]

        for pair_value in pair_candidates:
            remaining = list(tiles)
            pair = []

            for tile in list(remaining):
                if tile_value(tile) == pair_value and len(pair) < 2:
                    pair.append(tile)
                    remaining.remove(tile)

            sequence = self._find_twelve_consecutive_numbers(remaining)
            if sequence is not None:
                return True, [sequence, pair]

        return False, []

    def is_ready_qing_long(self, hand) -> Tuple[bool, List]:
        tiles = self._extract_all_tiles(hand)
        if len(tiles) != 13:
            return False, []

        ready_tiles = []
        for tile in ALL_TILES:
            can_win, _ = self.can_win_qing_long(list(tiles) + [tile])
            if can_win:
                ready_tiles.append(tile)

        return bool(ready_tiles), sorted(set(ready_tiles), key=lambda value: (not isinstance(value, int), str(value)))

    def _find_twelve_consecutive_numbers(self, tiles):
        number_to_tiles = {}
        for tile in tiles:
            value = tile_value(tile)
            if isinstance(value, int):
                number_to_tiles.setdefault(value, []).append(tile)

        for start in range(0, 10):
            needed = list(range(start, start + 12))
            if all(number in number_to_tiles for number in needed):
                return [number_to_tiles[number][0] for number in needed]

        return None
