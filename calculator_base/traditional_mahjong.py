"""
Traditional-mahjong shape checks for the simplified arithmetic mahjong rules.

Traditional mahjong uses the tile artwork mapping from the PDF rulebook. The
valid shape is exactly four triples plus one pair: 3 + 3 + 3 + 3 + 2 = 14.
There are no jokers, dora, single-tile gangs, or second pair in the simplified
rules.
"""

from collections import Counter
from typing import List, Tuple

from calculator_base.constants import ALL_TILES, MULTIPLY, PLUS, SYMBOLS


TradTile = Tuple[str, object]


class TraditionalMahjongChecker:
    """Traditional mahjong checker for four melds plus one pair."""

    def __init__(self):
        self.num_to_tile = self._init_mapping()

    def _init_mapping(self):
        """Map arithmetic-mahjong values to traditional mahjong faces."""
        mapping = {}

        for i in range(1, 10):
            mapping[i] = ("条", i)

        for i in range(1, 10):
            mapping[10 + i] = ("筒", i)

        mapping.update(
            {
                0: ("风", "西"),
                10: ("风", "北"),
                20: ("风", "南"),
                24: ("万", 4),
                PLUS: ("箭", "中"),
                MULTIPLY: ("箭", "发"),
            }
        )

        return mapping

    def can_win_traditional(self, tiles_list):
        """
        Check an ungrouped 14-tile hand.

        Returns:
            (can_win, groups), where groups are original Tile objects grouped as
            four triples plus one pair. The pair is returned first.
        """
        trad_tiles, original_tiles = self._convert_tiles_with_mapping(tiles_list)
        if trad_tiles is None or len(trad_tiles) != 14:
            return False, []

        can_win, trad_groups = self._check_win_no_joker(trad_tiles)
        if not can_win:
            return False, []

        return True, self._map_back_to_tiles(trad_groups, original_tiles)

    def can_win_traditional_groups(self, groups):
        """
        Check an already grouped traditional-mahjong hand.

        The grouped shape must contain exactly four valid triples and one pair.
        This is useful for mode-1 inputs where the user has already separated
        traditional-mahjong groups.
        """
        if len(groups) != 5:
            return False

        pair_count = 0
        meld_count = 0

        for group in groups:
            values = [self._tile_value(tile) for tile in group]
            trad_group = [self.num_to_tile.get(value) for value in values]
            if any(tile is None for tile in trad_group):
                return False

            if len(trad_group) == 2 and self._is_pair(trad_group):
                pair_count += 1
            elif len(trad_group) == 3 and self._is_meld(trad_group):
                meld_count += 1
            else:
                return False

        return pair_count == 1 and meld_count == 4

    def _convert_tiles_with_mapping(self, tiles_list):
        trad_tiles = []
        original_tiles = []

        for tile_obj in tiles_list:
            value = self._tile_value(tile_obj)
            if value not in self.num_to_tile:
                return None, []

            trad_tiles.append(self.num_to_tile[value])
            original_tiles.append(tile_obj)

        return trad_tiles, original_tiles

    @staticmethod
    def _tile_value(tile_obj):
        return tile_obj.value if hasattr(tile_obj, "value") else tile_obj

    def _check_win_no_joker(self, tiles):
        """Check four melds plus one pair using traditional faces."""
        if len(tiles) != 14:
            return False, []

        tile_counter = Counter(tiles)
        pair_candidates = [tile for tile, count in tile_counter.items() if count >= 2]

        for pair_tile in pair_candidates:
            remaining = list(tiles)
            remaining.remove(pair_tile)
            remaining.remove(pair_tile)

            melds = []
            if self._can_form_melds_with_record(remaining, melds):
                return True, [[pair_tile, pair_tile]] + melds

        return False, []

    def _can_form_melds_with_record(self, tiles, melds):
        if not tiles:
            return True
        if len(tiles) % 3 != 0:
            return False

        counter = Counter(tiles)
        return self._try_remove_melds_with_record(counter, melds)

    def _try_remove_melds_with_record(self, counter, melds):
        if sum(counter.values()) == 0:
            return True

        first_tile = self._first_remaining_tile(counter)
        if first_tile is None:
            return True

        if counter[first_tile] >= 3:
            counter[first_tile] -= 3
            melds.append([first_tile, first_tile, first_tile])

            if self._try_remove_melds_with_record(counter, melds):
                return True

            counter[first_tile] += 3
            melds.pop()

        suit, value = first_tile
        if suit in {"条", "筒", "万"} and isinstance(value, int) and value <= 7:
            tile2 = (suit, value + 1)
            tile3 = (suit, value + 2)

            if counter[tile2] > 0 and counter[tile3] > 0:
                counter[first_tile] -= 1
                counter[tile2] -= 1
                counter[tile3] -= 1
                melds.append([first_tile, tile2, tile3])

                if self._try_remove_melds_with_record(counter, melds):
                    return True

                counter[first_tile] += 1
                counter[tile2] += 1
                counter[tile3] += 1
                melds.pop()

        return False

    def _first_remaining_tile(self, counter):
        remaining = [tile for tile, count in counter.items() if count > 0]
        if not remaining:
            return None
        return sorted(remaining, key=self._trad_sort_key)[0]

    @staticmethod
    def _trad_sort_key(tile):
        suit, value = tile
        suit_order = {"条": 0, "筒": 1, "万": 2, "风": 3, "箭": 4}
        return suit_order.get(suit, 99), str(value)

    @staticmethod
    def _is_pair(group: List[TradTile]) -> bool:
        return len(group) == 2 and group[0] == group[1]

    def _is_meld(self, group: List[TradTile]) -> bool:
        return self._is_triplet(group) or self._is_sequence(group)

    @staticmethod
    def _is_triplet(group: List[TradTile]) -> bool:
        return len(group) == 3 and group[0] == group[1] == group[2]

    @staticmethod
    def _is_sequence(group: List[TradTile]) -> bool:
        if len(group) != 3:
            return False

        suits = {tile[0] for tile in group}
        if len(suits) != 1:
            return False

        suit = group[0][0]
        if suit not in {"条", "筒", "万"}:
            return False

        values = sorted(tile[1] for tile in group)
        return values[1] == values[0] + 1 and values[2] == values[1] + 1

    def _map_back_to_tiles(self, trad_groups, original_tiles):
        available_tiles = list(original_tiles)
        tile_groups = []

        for trad_group in trad_groups:
            tile_group = []

            for trad_tile in trad_group:
                for idx, tile_obj in enumerate(available_tiles):
                    value = self._tile_value(tile_obj)
                    if self.num_to_tile.get(value) == trad_tile:
                        tile_group.append(tile_obj)
                        available_tiles.pop(idx)
                        break

            if len(tile_group) != len(trad_group):
                return []

            tile_groups.append(tile_group)

        return tile_groups

    def is_ready_traditional(self, tiles_list):
        """
        Check a 13-tile ready hand.

        Returns:
            (is_ready, ready_tile_values)
        """
        if len(tiles_list) != 13:
            return False, []

        ready_tiles = []
        for tile_value in self._get_all_possible_nums():
            test_hand = list(tiles_list) + [tile_value]
            can_win, _ = self.can_win_traditional(test_hand)
            if can_win:
                ready_tiles.append(tile_value)

        return bool(ready_tiles), ready_tiles

    def _get_all_possible_nums(self):
        return sorted(ALL_TILES, key=lambda value: (value in SYMBOLS, str(value)))
