from collections import Counter
from itertools import combinations

from calculator_base.constants import MULTIPLY, PLUS, SYMBOLS
from calculator_base.hand_structure import Hand
from calculator_base.parser import tile_sort_key


class ArithmeticMahjongChecker:
    """Normal arithmetic-mahjong shape checker: three groups plus one pair."""

    def is_valid_formula(self, tiles):
        """Check whether four tiles form a + or x arithmetic formula."""
        values = [self._tile_value(tile) for tile in tiles]
        if len(values) != 4:
            return False

        symbols = [value for value in values if value in SYMBOLS]
        numbers = [value for value in values if isinstance(value, int)]
        if len(symbols) != 1 or len(numbers) != 3:
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

    def is_kezi(self, tiles):
        values = [self._tile_value(tile) for tile in tiles]
        return len(values) in {4, 5} and len(set(values)) == 1

    def is_valid_group(self, tiles):
        return self.is_kezi(tiles) or self.is_valid_formula(tiles)

    def is_pair(self, tiles):
        values = [self._tile_value(tile) for tile in tiles]
        return len(values) == 2 and values[0] == values[1]

    def can_win(self, hand: Hand):
        """Check the normal arithmetic shape with any existing melded groups."""
        group_options = self.all_win_groups(hand)
        if not group_options:
            return False, []
        return True, group_options[0]

    def all_win_groups(self, hand: Hand):
        """Return every valid normal arithmetic grouping for the hand."""
        melded_groups = hand.melded_groups or []
        meld_count = len(melded_groups)
        needed_groups = 3 - meld_count

        if needed_groups < 0:
            return []

        if hand.hand_groups:
            if not self._validate_grouped_arithmetic(hand.hand_groups, needed_groups):
                return []
            return [melded_groups + hand.hand_groups]

        hand_tiles = list(hand.hand_tiles)
        if len(hand_tiles) != needed_groups * 4 + 2:
            return []

        return [
            melded_groups + hand_groups
            for hand_groups in self._partition_arithmetic_all(hand_tiles, needed_groups)
        ]

    def _validate_grouped_arithmetic(self, groups, expected_four_tile_groups):
        four_tile_groups = 0
        pair_count = 0

        for group in groups:
            if len(group) == 2:
                if not self.is_pair(group):
                    return False
                pair_count += 1
            elif len(group) == 4:
                if not self.is_valid_group(group):
                    return False
                four_tile_groups += 1
            else:
                return False

        return four_tile_groups == expected_four_tile_groups and pair_count == 1

    def _partition_arithmetic(self, tiles, n_groups):
        group_options = self._partition_arithmetic_all(tiles, n_groups)
        if not group_options:
            return False, []
        return True, group_options[0]

    def _partition_arithmetic_all(self, tiles, n_groups):
        if len(tiles) != n_groups * 4 + 2:
            return []

        results = []
        seen = set()

        for pair_indices in combinations(range(len(tiles)), 2):
            pair = [tiles[idx] for idx in pair_indices]
            if not self.is_pair(pair):
                continue

            remaining = [
                tile for idx, tile in enumerate(tiles) if idx not in set(pair_indices)
            ]
            for groups in self._partition_groups_all(remaining, n_groups):
                candidate = groups + [pair]
                key = self._groups_signature(candidate)
                if key in seen:
                    continue
                seen.add(key)
                results.append(candidate)

        return results

    def _partition_groups(self, tiles, n_groups):
        group_options = self._partition_groups_all(tiles, n_groups)
        if not group_options:
            return False, []
        return True, group_options[0]

    def _partition_groups_all(self, tiles, n_groups):
        if n_groups == 0:
            return [[]] if len(tiles) == 0 else []
        if len(tiles) != n_groups * 4:
            return []

        tiles_sorted = sorted(tiles, key=lambda tile: tile_sort_key(self._tile_value(tile)))
        return self._try_partition_groups_all(tiles_sorted, [])

    def _try_partition_groups(self, remaining, groups):
        group_options = self._try_partition_groups_all(remaining, groups)
        if not group_options:
            return False, []
        return True, group_options[0]

    def _try_partition_groups_all(self, remaining, groups):
        if not remaining:
            return [groups]

        if len(remaining) % 4 != 0:
            return []

        results = []
        seen = set()
        value_counter = Counter(self._tile_value(tile) for tile in remaining)
        for value, count in sorted(value_counter.items(), key=lambda item: tile_sort_key(item[0])):
            if count >= 4:
                group, rest = self._take_matching_tiles(remaining, value, 4)
                for result in self._try_partition_groups_all(rest, groups + [group]):
                    key = self._groups_signature(result)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(result)

        first_tile = remaining[0]
        for combo in combinations(range(1, len(remaining)), 3):
            used_indices = {0, *combo}
            group = [first_tile] + [remaining[idx] for idx in combo]
            if not self.is_valid_formula(group):
                continue

            rest = [
                tile for idx, tile in enumerate(remaining) if idx not in used_indices
            ]
            for result in self._try_partition_groups_all(rest, groups + [group]):
                key = self._groups_signature(result)
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)

        return results

    def _take_matching_tiles(self, tiles, value, count):
        group = []
        rest = []
        for tile in tiles:
            if self._tile_value(tile) == value and len(group) < count:
                group.append(tile)
            else:
                rest.append(tile)
        return group, rest

    def _groups_signature(self, groups):
        normalized_groups = []
        for group in groups:
            values = tuple(sorted((self._tile_value(tile) for tile in group), key=tile_sort_key))
            normalized_groups.append(values)
        return tuple(
            sorted(
                normalized_groups,
                key=lambda values: tuple(tile_sort_key(value) for value in values),
            )
        )

    @staticmethod
    def _tile_value(tile):
        return tile.value if hasattr(tile, "value") else tile
