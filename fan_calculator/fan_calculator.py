from calculator_base.hand_structure import Hand
from fan_calculator.fan_base import FanResults, apply_exclusion_rules
from fan_calculator.fan_context import check_all_context_fans
from fan_calculator.fan_special import check_all_special_fans
from fan_calculator.fan_standard import check_all_standard_fans


class FanCalculator:
    """Simplified fan calculator."""

    def __init__(self, min_fan=1):
        self.min_fan = min_fan

    def calculate(self, hand: Hand) -> FanResults:
        all_fans = FanResults()

        for source in [
            check_all_standard_fans(hand),
            check_all_special_fans(hand),
            check_all_context_fans(hand),
        ]:
            for fan in source.results:
                all_fans.add(fan)

        final_fans = apply_exclusion_rules(all_fans)
        final_fans.sort_by_value()
        return final_fans

    def can_win(self, hand: Hand) -> bool:
        return self.calculate(hand).get_starting_fan(hand) >= self.min_fan

    def format_result(self, fan_results: FanResults, verbose: bool = False) -> str:
        lines = [
            "=" * 60,
            f"总番: {fan_results.get_total_fan()}番",
            f"封顶后: {fan_results.get_capped_fan()}番",
            f"应赔付筹码: {fan_results.get_payment()}",
            "=" * 60,
        ]

        if fan_results.results:
            lines.append("番种明细:")
            for result in fan_results.results:
                lines.append(f"  {result}")
        else:
            lines.append("没有满足的番种")

        if verbose and fan_results.excluded:
            lines.append("被排除的番种:")
            for fan_type, reason in fan_results.excluded:
                lines.append(f"  {fan_type.fan_name}: {reason}")

        if fan_results.get_starting_fan() >= self.min_fan:
            lines.append(f"满足起胡条件（{self.min_fan}番起胡）")
        else:
            lines.append(
                f"不满足起胡条件（需要{self.min_fan}番，当前{fan_results.get_starting_fan()}番）"
            )

        return "\n".join(lines)


def calculate_fan(hand: Hand, min_fan: int = 1) -> FanResults:
    return FanCalculator(min_fan=min_fan).calculate(hand)


def get_total_fan(hand: Hand, min_fan: int = 1) -> int:
    return calculate_fan(hand, min_fan).get_total_fan()


def can_win_with_fan(hand: Hand, min_fan: int = 1) -> tuple[bool, int]:
    calculator = FanCalculator(min_fan=min_fan)
    result = calculator.calculate(hand)
    return result.get_starting_fan(hand) >= min_fan, result.get_total_fan()


def format_fan_result(hand: Hand, verbose: bool = False, min_fan: int = 1) -> str:
    calculator = FanCalculator(min_fan=min_fan)
    return calculator.format_result(calculator.calculate(hand), verbose)
