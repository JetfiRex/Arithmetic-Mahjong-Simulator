"""Immutable round rules; ordinary play remains the default."""

from dataclasses import dataclass
from enum import Enum
import math


class GameMode(str, Enum):
    NORMAL = "normal"
    BLOOD_BATTLE = "blood_battle"


@dataclass(frozen=True)
class GameConfig:
    allow_multiple_hu: bool = True
    mode: GameMode = GameMode.NORMAL
    hand_preparation_seconds: float = 0

    def __post_init__(self):
        object.__setattr__(self, "mode", GameMode(self.mode))
        if type(self.allow_multiple_hu) is not bool:
            raise ValueError("allow_multiple_hu must be a boolean")
        if (type(self.hand_preparation_seconds) not in (int, float)
                or not math.isfinite(self.hand_preparation_seconds)
                or not 0 <= self.hand_preparation_seconds <= 600):
            raise ValueError("hand_preparation_seconds must be between 0 and 600")
