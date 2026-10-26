"""Seeded reference policies. No simulator state or wall RNG reaches act()."""

from dataclasses import dataclass, field
import random
from simulator.actions import ActionType


@dataclass
class TsumogiriBot:
    def act(self, observation, legal_actions):
        for a in legal_actions:
            if a.action_type == ActionType.PASS:
                return a
        discards = [a for a in legal_actions if a.action_type == ActionType.DISCARD]
        return next((a for a in discards if a.tile == observation.drawn_tile), discards[-1])


@dataclass
class RandomBot:
    seed: int = 0
    rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def act(self, observation, legal_actions):
        return self.rng.choice(legal_actions)


class WinThenRandomBot(RandomBot):
    def act(self, observation, legal_actions):
        return next((a for a in legal_actions if a.action_type == ActionType.HU), None) or super().act(observation, legal_actions)
