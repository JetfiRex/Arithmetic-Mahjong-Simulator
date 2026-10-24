"""Configuration shared by the normal round's action and settlement flow."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GameConfig:
    allow_multiple_hu: bool = True
