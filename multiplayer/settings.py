"""Round timer settings shared by human rooms and bot practice."""

from dataclasses import asdict
import math
from typing import Any

from simulator import TimeoutConfig
from simulator.game_state import GameStateError


DEFAULT_TIMEOUTS = TimeoutConfig(5, 5, 30, 5)


def parse_timeouts(values: Any) -> TimeoutConfig:
    """Validate all four settings before constructing an immutable round config."""
    fields = asdict(DEFAULT_TIMEOUTS)
    if not isinstance(values, dict) or set(values) != set(fields):
        raise GameStateError("请提供完整的 t1、t2、t3、t4 配置")
    for value in values.values():
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600
        ):
            raise GameStateError("计时必须关闭，或设置为 1 到 600 秒")
    return TimeoutConfig(**values)
