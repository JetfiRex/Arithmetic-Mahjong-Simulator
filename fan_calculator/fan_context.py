from typing import Optional

from calculator_base.hand_structure import Hand
from fan_calculator.fan_base import FanResult, FanResults, FanType


def check_tian_hu(hand: Hand) -> Optional[FanResult]:
    if hand.winning_method == "天胡":
        return FanResult(FanType.TIAN_HU)
    return None


def check_bu_qiu_ren(hand: Hand) -> Optional[FanResult]:
    if hand.winning_method != "自摸":
        return None

    for melded_group in hand.melded_groups:
        if melded_group.group_type in {"吃", "碰", "明杠", "加杠"}:
            return None

    return FanResult(FanType.BU_QIU_REN)


def check_all_context_fans(hand: Hand) -> FanResults:
    results = FanResults()

    for check in [check_tian_hu, check_bu_qiu_ren]:
        fan = check(hand)
        if fan:
            results.add(fan)

    return results
