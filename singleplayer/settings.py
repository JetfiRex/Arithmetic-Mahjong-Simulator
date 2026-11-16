"""Single-player round timer settings."""

from dataclasses import asdict
import math
from typing import Any

from simulator import TimeoutConfig
from simulator.game_state import GameStateError


DEFAULT_TIMEOUTS = TimeoutConfig(5, 5, 30, 5)
DEFAULT_HAND_PREPARATION_SECONDS = 30


def parse_hand_preparation_seconds(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 600:
        raise GameStateError("理牌时间须为 0 到 600 秒，0 表示跳过")
    return value


def parse_timeouts(values: Any) -> TimeoutConfig:
    """Validate all four settings before constructing an immutable round config."""
    fields = asdict(DEFAULT_TIMEOUTS)
    if not isinstance(values, dict) or set(values) != set(fields):
        raise GameStateError("请提供完整的操作计时配置")
    for value in values.values():
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600
        ):
            raise GameStateError("计时必须关闭，或设置为 1 到 600 秒")
    return TimeoutConfig(**values)
