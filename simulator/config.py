"""Immutable round rules; ordinary play remains the default."""

from dataclasses import dataclass
from enum import Enum


class GameMode(str, Enum):
    NORMAL = "normal"
    BLOOD_BATTLE = "blood_battle"


@dataclass(frozen=True)
class GameConfig:
    allow_multiple_hu: bool = True
    mode: GameMode = GameMode.NORMAL

    def __post_init__(self):
        object.__setattr__(self, "mode", GameMode(self.mode))
        if type(self.allow_multiple_hu) is not bool:
            raise ValueError("allow_multiple_hu must be a boolean")
