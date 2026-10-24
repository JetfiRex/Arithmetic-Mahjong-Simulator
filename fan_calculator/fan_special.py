from typing import Optional

from calculator_base.hand_structure import Hand
from fan_calculator.fan_base import FanResult, FanResults, FanType


def check_lian_qi_dui(hand: Hand) -> Optional[FanResult]:
    if hand.win_type == "连七对":
        return FanResult(FanType.LIAN_QI_DUI)
    return None


def check_qi_xiao_dui(hand: Hand) -> Optional[FanResult]:
    if hand.win_type in {"七小对", "连七对"}:
        return FanResult(FanType.QI_XIAO_DUI)
    return None


def check_qing_long(hand: Hand) -> Optional[FanResult]:
    if hand.win_type == "清龙":
        return FanResult(FanType.QING_LONG)
    return None


def check_chuan_tong_majiang(hand: Hand) -> Optional[FanResult]:
    if hand.win_type == "传统麻将" and not hand.melded_groups:
        return FanResult(FanType.CHUAN_TONG_MAJIANG)
    return None


def check_all_special_fans(hand: Hand) -> FanResults:
    results = FanResults()

    for check in [
        check_lian_qi_dui,
        check_qing_long,
        check_qi_xiao_dui,
        check_chuan_tong_majiang,
    ]:
        fan = check(hand)
        if fan:
            results.add(fan)

    return results
