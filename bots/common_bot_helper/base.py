"""Small policy interface; runtime and engine ownership live outside policies."""

from typing import Protocol
from bots.common_bot_helper.decision import BotAction
from bots.common_bot_helper.observation import Observation


class Bot(Protocol):
    def act(self, observation: Observation, legal_actions: tuple[BotAction, ...]) -> BotAction:
        ...
