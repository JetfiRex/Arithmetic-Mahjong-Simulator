"""
Base types and helpers for simplified-rule fan calculation.

Fan values are ordinary fan counts: 1, 2, ..., 6. Payment is multiplicative:
1 fan pays 2 chips and each additional fan doubles, capped at 6 fan.
"""

from enum import Enum
from typing import List, Tuple

from calculator_base.constants import MULTIPLY, PLUS, SYMBOLS, TILE_COUNTS
from calculator_base.hand_structure import Hand, Tile


MAX_FAN = 6


class FanType(Enum):
    """Simplified fan table."""

    # 6 fan
    DA_SAN_YUAN = (6, "大三元")
    DA_SI_XI = (6, "大四喜")
    QI_YI_SE = (6, "奇一色")
    LIAN_QI_DUI = (6, "连七对")
    LIAN_BA_DUI = (6, "连七对")  # legacy enum name compatibility
    QUAN_ZHI_SHU = (6, "全质数")
    WU_HE_SHU = (6, "全质数")  # legacy enum name compatibility
    QUAN_ER_MI = (6, "全二幂")

    # 5 fan
    SAN_KE_ZI = (5, "三刻子")
    SAN_TONG_SHI = (5, "三同式")
    QING_LONG = (5, "清龙")

    # 4 fan
    QUAN_DUO_BEI = (4, "全多倍")
    QUAN_ER_WEI = (4, "全二位")
    TIAN_HU = (4, "天胡")

    # 3 fan
    QUAN_SAN_BEI = (3, "全三倍")
    QUAN_YI_WEI = (3, "全一位")

    # 2 fan
    QI_XIAO_DUI = (2, "七小对")
    BA_XIAO_DUI = (2, "七小对")  # legacy enum name compatibility
    QUAN_OU_SHU = (2, "全偶数")
    SAN_MEN_QI = (2, "三门齐")
    SI_MEN_QI = (2, "三门齐")  # legacy enum name compatibility
    CHUAN_TONG_MAJIANG = (2, "传统麻将")

    # 1 fan
    YI_DUI_SHI = (1, "一对式")
    YI_BAN_GAO = (1, "一对式")  # legacy enum name compatibility
    JIA_YI_SE = (1, "加一色")
    CHENG_YI_SE = (1, "乘一色")
    BU_QIU_REN = (1, "不求人")

    def __init__(self, fan_value: int, name: str):
        self.fan_value = fan_value
        self.fan_name = name


class FanResult:
    """Single fan-pattern result."""

    def __init__(self, fan_type: FanType, count: int = 1, reason: str = ""):
        self.fan_type = fan_type
        self.count = count
        self.reason = reason

    def get_total_fan(self) -> int:
        return self.fan_type.fan_value * self.count

    def __repr__(self):
        suffix = f"：{self.reason}" if self.reason else ""
        if self.count > 1:
            return f"{self.fan_type.fan_name} x{self.count} ({self.get_total_fan()}番){suffix}"
        return f"{self.fan_type.fan_name} ({self.get_total_fan()}番){suffix}"


class FanResults:
    """Complete fan calculation result."""

    def __init__(self):
        self.results: List[FanResult] = []
        self.excluded: List[Tuple[FanType, str]] = []

    def add(self, fan_result: FanResult):
        self.results.append(fan_result)

    def exclude(self, fan_type: FanType, reason: str):
        self.excluded.append((fan_type, reason))

    def get_total_fan(self) -> int:
        return sum(result.get_total_fan() for result in self.results)

    def get_capped_fan(self) -> int:
        return min(self.get_total_fan(), MAX_FAN)

    def get_payment(self) -> int:
        """Payment per payer after the 6-fan cap."""
        capped_fan = self.get_capped_fan()
        if capped_fan <= 0:
            return 0
        return 2 ** capped_fan

    def get_starting_fan(self, hand=None) -> int:
        return self.get_total_fan()

    def has_fan_type(self, fan_type: FanType) -> bool:
        return any(result.fan_type == fan_type for result in self.results)

    def sort_by_value(self):
        self.results.sort(key=lambda result: result.fan_type.fan_value, reverse=True)

    def __repr__(self):
        lines = [
            f"总番: {self.get_total_fan()}番",
            f"封顶后: {self.get_capped_fan()}番",
            f"应赔付筹码: {self.get_payment()}",
            "-" * 40,
        ]
        lines.extend(str(result) for result in self.results)
        return "\n".join(lines)


FAN_EXCLUSIONS = {
    FanType.DA_SAN_YUAN: [FanType.SAN_KE_ZI],
    FanType.LIAN_QI_DUI: [FanType.QI_XIAO_DUI],
    FanType.LIAN_BA_DUI: [FanType.BA_XIAO_DUI],
    FanType.SAN_TONG_SHI: [FanType.YI_DUI_SHI, FanType.YI_BAN_GAO],
    FanType.QUAN_DUO_BEI: [FanType.QUAN_SAN_BEI, FanType.QUAN_OU_SHU],
    FanType.QUAN_SAN_BEI: [FanType.QUAN_OU_SHU],
}


def apply_exclusion_rules(fan_results: FanResults) -> FanResults:
    final_results = FanResults()
    present_types = {result.fan_type for result in fan_results.results}
    excluded_types = set()

    for result in fan_results.results:
        for excluded_type in FAN_EXCLUSIONS.get(result.fan_type, []):
            if excluded_type in present_types:
                excluded_types.add(excluded_type)
                final_results.exclude(excluded_type, f"被{result.fan_type.fan_name}包含")

    for result in fan_results.results:
        if result.fan_type not in excluded_types:
            final_results.add(result)

    return final_results


def get_tile_count(tile_value) -> int:
    return TILE_COUNTS.get(tile_value, 0)


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    for i in range(3, int(n**0.5) + 1, 2):
        if n % i == 0:
            return False
    return True


def is_composite(n: int) -> bool:
    return n > 1 and not is_prime(n)


def is_power_of_2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def get_all_number_tiles(hand: Hand) -> List[int]:
    numbers = []
    for tile in get_all_tiles_for_fan(hand):
        if isinstance(tile.value, int):
            numbers.append(tile.value)
    return numbers


def get_all_tiles_for_fan(hand: Hand, include_single_gang: bool = False) -> List[Tile]:
    tiles = []

    for group in hand.melded_groups:
        tiles.extend(group.tiles)

    if hand.hand_groups:
        for group in hand.hand_groups:
            tiles.extend(group)
    else:
        tiles.extend(hand.hand_tiles)

    return tiles
