"""Simplified arithmetic mahjong fan calculator."""

from fan_calculator.fan_calculator import (
    FanCalculator,
    calculate_fan,
    get_total_fan,
    can_win_with_fan,
    format_fan_result
)

from fan_calculator.fan_base import (
    FanType,
    FanResult,
    FanResults,
    apply_exclusion_rules
)

__all__ = [
    'FanCalculator',
    'calculate_fan',
    'get_total_fan',
    'can_win_with_fan',
    'format_fan_result',
    'FanType',
    'FanResult',
    'FanResults',
    'apply_exclusion_rules',
]
