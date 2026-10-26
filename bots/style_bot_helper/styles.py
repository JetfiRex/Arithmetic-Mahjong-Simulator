"""Initial, explicitly different playing preferences; no neural training needed."""

from dataclasses import dataclass

PROFILE_VERSION = 'beginner-style-profiles-v1'


@dataclass(frozen=True)
class StyleProfile:
    name: str
    progress: float
    ready: float
    width: float
    payment: float
    target: float
    danger: float
    feeding: float
    claim_cost: float
    containment: float


PROFILES = {
    'fast': StyleProfile('fast', 1.0, 18, 2.2, .15, 0, .5, 2, .05, 0),
    'big': StyleProfile('big', .75, 6, .4, 1.5, 5, 1.2, 2, .4, .1),
    'defensive': StyleProfile('defensive', .6, 9, .8, .3, 0, 8, 5, 1.5, .35),
}
