from calculator_base.arithmetic_mahjong import ArithmeticMahjongChecker
from calculator_base.constants import ALL_TILES, SYMBOLS
from calculator_base.empty_listening_checker import analyze_ready_tiles
from calculator_base.hand_structure import Hand, create_tile_from_value
from calculator_base.parser import format_hand, parse_hand
from calculator_base.special_winning_checker import SpecialWinningChecker, SevenPairsChecker
from calculator_base.traditional_mahjong import TraditionalMahjongChecker

try:
    from fan_calculator.fan_calculator import calculate_fan

    FAN_CALCULATOR_AVAILABLE = True
except ImportError:
    FAN_CALCULATOR_AVAILABLE = False


class ArithmeticMahjong:
    """
    Top-level simplified arithmetic mahjong checker.

    This class coordinates normal arithmetic mahjong, traditional mahjong, and
    special winning shapes. Normal arithmetic shape logic lives in
    arithmetic_mahjong.py.
    """

    def __init__(self, min_fan=1):
        self.min_fan = min_fan
        self.symbols = SYMBOLS
        self.all_tiles = set(ALL_TILES)
        self.tiles_in_pool = set(ALL_TILES)
        self.arithmetic_checker = ArithmeticMahjongChecker()
        self.traditional_checker = TraditionalMahjongChecker()
        self.seven_pairs_checker = SevenPairsChecker()
        self.eight_pairs_checker = self.seven_pairs_checker
        self.special_winning_checker = SpecialWinningChecker()

    # Backward-compatible arithmetic helpers.
    def is_valid_formula(self, tiles):
        return self.arithmetic_checker.is_valid_formula(tiles)

    def is_kezi(self, tiles):
        return self.arithmetic_checker.is_kezi(tiles)

    def is_valid_group(self, tiles):
        return self.arithmetic_checker.is_valid_group(tiles)

    def is_pair(self, tiles):
        return self.arithmetic_checker.is_pair(tiles)

    def can_win_arithmetic(self, hand: Hand):
        return self.arithmetic_checker.can_win(hand)

    def can_win(self, hand: Hand):
        """
        Return (can_win, groups, win_type, fan_info).
        """
        win_options = []

        arithmetic_options = self.arithmetic_checker.all_win_groups(hand)
        if arithmetic_options:
            arithmetic_groups, arithmetic_fan_info = self._best_groups_for_fan(
                hand,
                arithmetic_options,
                "算术麻将",
            )
            win_options.append(
                (
                    "算术麻将",
                    arithmetic_groups,
                    arithmetic_fan_info,
                )
            )

        all_tiles = self._all_tiles_for_shape(hand)

        if not hand.melded_groups:
            can_traditional, traditional_groups = self.traditional_checker.can_win_traditional(all_tiles)
            if can_traditional:
                win_options.append(
                    (
                        "传统麻将",
                        traditional_groups,
                        self._calculate_fan(hand, traditional_groups, "传统麻将"),
                    )
                )

        special_checks = [
            ("连七对", self.special_winning_checker.can_win_lian_qi_dui),
            ("七小对", self.special_winning_checker.can_win_qi_xiao_dui),
            ("清龙", self.special_winning_checker.can_win_qing_long),
        ]
        for win_type, check in special_checks:
            can_special, groups = check(all_tiles)
            if can_special:
                win_options.append(
                    (
                        win_type,
                        groups,
                        self._calculate_fan(hand, groups, win_type),
                    )
                )

        if not win_options:
            return False, [], None, None

        win_options.sort(
            key=lambda option: (self._fan_total(option[2]), self._win_type_priority(option[0])),
            reverse=True,
        )
        best_win_type, best_groups, best_fan_info = win_options[0]
        return True, best_groups, best_win_type, best_fan_info

    def is_ready(self, hand: Hand, return_details=False):
        """
        Check ready tiles by adding each legal tile and delegating to can_win().
        """
        hand_tiles = list(hand.hand_tiles)
        melded_groups = hand.melded_groups or []
        expected_len = self._expected_ready_hand_tile_count(melded_groups)
        if len(hand_tiles) != expected_len:
            return False, {}

        ready_info = {}

        for tile_value in sorted(ALL_TILES, key=lambda value: (value in SYMBOLS, str(value))):
            test_tile = create_tile_from_value(tile_value)
            test_hand = Hand(
                melded_groups=melded_groups,
                hand_tiles=hand_tiles + [test_tile],
                winning_tile=test_tile,
                winning_method=hand.winning_method,
                should_win_in_mode=True,
            )
            can_win, groups, win_type, fan_info = self.can_win(test_hand)
            if not can_win:
                continue

            if win_type not in ready_info:
                ready_info[win_type] = {"tiles": [], "details": {}}

            ready_info[win_type]["tiles"].append(tile_value)
            if return_details:
                ready_info[win_type]["details"][tile_value] = {
                    "groups": groups,
                    "win_type": win_type,
                    "fan_info": fan_info,
                }

        if not ready_info:
            return False, {}

        if return_details:
            return True, ready_info

        visible_tiles = self._visible_tile_values(hand)
        simple_ready_info = {}
        for win_type, info in ready_info.items():
            analysis = analyze_ready_tiles(info["tiles"], visible_tiles)
            simple_ready_info[win_type] = [
                analysis[tile]["display"] for tile in info["tiles"]
            ]

        return True, simple_ready_info

    def _calculate_fan(self, source_hand, groups, win_type):
        if not FAN_CALCULATOR_AVAILABLE:
            return None

        try:
            hand_for_fan = Hand(
                melded_groups=source_hand.melded_groups,
                hand_tiles=[],
                hand_groups=[group for group in groups if isinstance(group, list)],
                winning_tile=source_hand.winning_tile,
                winning_method=source_hand.winning_method,
                win_type=win_type,
            )
            fan_result = calculate_fan(hand_for_fan, min_fan=self.min_fan)
            total_fan = fan_result.get_total_fan()
            return {
                "total_fan": total_fan,
                "starting_fan": fan_result.get_starting_fan(hand_for_fan),
                "capped_fan": getattr(fan_result, "get_capped_fan", lambda: min(total_fan, 6))(),
                "payment": getattr(fan_result, "get_payment", lambda: 0)(),
                "fan_result": fan_result,
                "can_start": total_fan >= self.min_fan,
            }
        except Exception:
            return None

    def format_result(self, success, groups, win_type=None, fan_info=None):
        if not success:
            return "无法胡牌"

        lines = [f"可以胡牌！【{win_type or '算术麻将'}】"]
        if groups:
            lines.append("胡牌组合:")
            for idx, group in enumerate(groups, 1):
                lines.append(f"第{idx}组: {' '.join(str(tile) for tile in group)}")

        if fan_info:
            lines.append(f"总番: {fan_info.get('total_fan', 0)}番")
            if "capped_fan" in fan_info:
                lines.append(f"封顶后: {fan_info['capped_fan']}番")
            if "payment" in fan_info:
                lines.append(f"应赔付筹码: {fan_info['payment']}")
            if not fan_info.get("can_start", True):
                lines.append(f"警告: 不满足起胡条件（需要{self.min_fan}番）")

        return "\n".join(lines)

    def display_ready_interactive(self, hand):
        is_ready, ready_info = self.is_ready(hand, return_details=True)
        if not is_ready:
            print("不听牌")
            return

        for win_type, info in ready_info.items():
            tiles = ", ".join(str(tile) for tile in info["tiles"])
            print(f"{win_type}: {tiles}")

    @staticmethod
    def _tile_value(tile):
        return tile.value if hasattr(tile, "value") else tile

    def _all_tiles_for_shape(self, hand: Hand):
        tiles = []

        for group in hand.melded_groups:
            tiles.extend(group.tiles)

        if hand.hand_groups:
            for group in hand.hand_groups:
                tiles.extend(group)
        else:
            tiles.extend(hand.hand_tiles)

        return tiles

    def _visible_tile_values(self, hand: Hand):
        return [self._tile_value(tile) for tile in self._all_tiles_for_shape(hand)]

    @staticmethod
    def _fan_total(fan_info):
        if not fan_info:
            return 0
        return fan_info.get("total_fan", 0)

    def _best_groups_for_fan(self, source_hand, group_options, win_type):
        best_groups = group_options[0]
        best_fan_info = self._calculate_fan(source_hand, best_groups, win_type)
        best_key = (self._fan_total(best_fan_info),)

        for groups in group_options[1:]:
            fan_info = self._calculate_fan(source_hand, groups, win_type)
            key = (self._fan_total(fan_info),)
            if key > best_key:
                best_groups = groups
                best_fan_info = fan_info
                best_key = key

        return best_groups, best_fan_info

    @staticmethod
    def _win_type_priority(win_type):
        return {
            "连七对": 6,
            "清龙": 5,
            "七小对": 2,
            "传统麻将": 2,
            "算术麻将": 1,
        }.get(win_type, 0)

    @staticmethod
    def _expected_ready_hand_tile_count(melded_groups):
        return (3 - len(melded_groups)) * 4 + 1


# Re-export parse_hand and format_hand for backward compatibility.
