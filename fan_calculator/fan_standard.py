from collections import Counter
from functools import reduce
from math import gcd
from typing import List, Optional, Tuple

from calculator_base.arithmetic_mahjong import ArithmeticMahjongChecker
from calculator_base.constants import MULTIPLY, PLUS, SYMBOLS
from calculator_base.hand_structure import Hand, MeldedGroup, Tile
from fan_calculator.fan_base import (
    FanResult,
    FanResults,
    FanType,
    get_all_number_tiles,
    get_all_tiles_for_fan,
    is_power_of_2,
    is_prime,
)


_ARITHMETIC_CHECKER = ArithmeticMahjongChecker()


def _tile_value(tile):
    return tile.value if hasattr(tile, "value") else tile


def _group_tiles(group):
    return group.tiles if isinstance(group, MeldedGroup) else group


def get_shape_groups(hand: Hand):
    groups = []
    groups.extend(hand.melded_groups)
    groups.extend(hand.hand_groups)
    return groups


def is_formula_group(group) -> bool:
    return _ARITHMETIC_CHECKER.is_valid_formula(_group_tiles(group))


def is_kezi_group(group) -> bool:
    return _ARITHMETIC_CHECKER.is_kezi(_group_tiles(group))


def normalize_formula(group) -> Optional[Tuple[str, int, int, int]]:
    tiles = _group_tiles(group)
    if len(tiles) != 4:
        return None

    values = [_tile_value(tile) for tile in tiles]
    symbols = [value for value in values if value in SYMBOLS]
    numbers = [value for value in values if isinstance(value, int)]
    if len(symbols) != 1 or len(numbers) != 3:
        return None

    op = symbols[0]
    for i, a in enumerate(numbers):
        for j, b in enumerate(numbers):
            if i == j:
                continue
            c = numbers[3 - i - j]
            if op == PLUS and a + b == c:
                return (op, min(a, b), max(a, b), c)
            if op == MULTIPLY and a * b == c:
                return (op, min(a, b), max(a, b), c)

    return None


def get_formulas(hand: Hand) -> List[Tuple[str, int, int, int]]:
    formulas = []
    for group in get_shape_groups(hand):
        formula = normalize_formula(group)
        if formula is not None:
            formulas.append(formula)
    return formulas


def count_kezi(hand: Hand) -> int:
    return sum(1 for group in get_shape_groups(hand) if is_kezi_group(group))


def check_da_san_yuan(hand: Hand) -> Optional[FanResult]:
    symbol_count = sum(1 for tile in get_all_tiles_for_fan(hand) if tile.value in SYMBOLS)
    if symbol_count >= 8:
        return FanResult(FanType.DA_SAN_YUAN)
    return None


def check_da_si_xi(hand: Hand) -> Optional[FanResult]:
    count = sum(1 for tile in get_all_tiles_for_fan(hand) if tile.value in {0, 10, 20})
    if count >= 6:
        return FanResult(FanType.DA_SI_XI)
    return None


def check_qi_yi_se(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(number % 2 == 1 for number in numbers):
        return FanResult(FanType.QI_YI_SE)
    return None


def check_quan_zhi_shu(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(is_prime(number) or number == 1 for number in numbers):
        return FanResult(FanType.QUAN_ZHI_SHU)
    return None


def check_quan_er_mi(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(is_power_of_2(number) for number in numbers):
        return FanResult(FanType.QUAN_ER_MI)
    return None


def check_san_ke_zi(hand: Hand) -> Optional[FanResult]:
    if count_kezi(hand) >= 3:
        return FanResult(FanType.SAN_KE_ZI)
    return None


def check_san_tong_shi(hand: Hand) -> Optional[FanResult]:
    formula_counts = Counter(get_formulas(hand))
    for formula, count in formula_counts.items():
        if count >= 3:
            op, a, b, c = formula
            return FanResult(FanType.SAN_TONG_SHI, reason=f"{a} {op} {b} = {c}")
    return None


def check_quan_duo_bei(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if not numbers:
        return None

    divisor = reduce(gcd, numbers)
    if divisor >= 4:
        return FanResult(FanType.QUAN_DUO_BEI, reason=f"{divisor}的倍数")
    return None


def check_quan_er_wei(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(number >= 10 for number in numbers):
        return FanResult(FanType.QUAN_ER_WEI)
    return None


def check_quan_san_bei(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(number % 3 == 0 for number in numbers):
        return FanResult(FanType.QUAN_SAN_BEI)
    return None


def check_quan_yi_wei(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(0 <= number <= 9 for number in numbers):
        return FanResult(FanType.QUAN_YI_WEI)
    return None


def check_quan_ou_shu(hand: Hand) -> Optional[FanResult]:
    numbers = get_all_number_tiles(hand)
    if numbers and all(number % 2 == 0 for number in numbers):
        return FanResult(FanType.QUAN_OU_SHU)
    return None


def check_san_men_qi(hand: Hand) -> Optional[FanResult]:
    formulas = get_formulas(hand)
    has_plus = any(formula[0] == PLUS for formula in formulas)
    has_multiply = any(formula[0] == MULTIPLY for formula in formulas)
    has_kezi = count_kezi(hand) >= 1

    if has_kezi and has_plus and has_multiply:
        return FanResult(FanType.SAN_MEN_QI)
    return None


def check_yi_dui_shi(hand: Hand) -> Optional[FanResult]:
    formula_counts = Counter(get_formulas(hand))
    for formula, count in formula_counts.items():
        if count >= 2:
            op, a, b, c = formula
            return FanResult(FanType.YI_DUI_SHI, reason=f"{a} {op} {b} = {c}")
    return None


def check_jia_yi_se(hand: Hand) -> Optional[FanResult]:
    formulas = get_formulas(hand)
    if len(formulas) == 3 and all(formula[0] == PLUS for formula in formulas):
        return FanResult(FanType.JIA_YI_SE)
    return None


def check_cheng_yi_se(hand: Hand) -> Optional[FanResult]:
    formulas = get_formulas(hand)
    if len(formulas) == 3 and all(formula[0] == MULTIPLY for formula in formulas):
        return FanResult(FanType.CHENG_YI_SE)
    return None


def check_all_standard_fans(hand: Hand) -> FanResults:
    results = FanResults()

    for check in [
        check_da_san_yuan,
        check_da_si_xi,
        check_qi_yi_se,
        check_quan_zhi_shu,
        check_quan_er_mi,
        check_san_ke_zi,
        check_san_tong_shi,
        check_quan_duo_bei,
        check_quan_er_wei,
        check_quan_san_bei,
        check_quan_yi_wei,
        check_quan_ou_shu,
        check_san_men_qi,
        check_yi_dui_shi,
        check_jia_yi_se,
        check_cheng_yi_se,
    ]:
        fan = check(hand)
        if fan:
            results.add(fan)

    return results
