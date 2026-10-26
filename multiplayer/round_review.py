"""Private round-wall records, published only by the room's review endpoint."""

from dataclasses import dataclass, field
import random
from typing import Any

from simulator import GameState


class RecordingRandom(random.Random):
    """Use the engine's RNG injection to capture the wall before any dealing."""

    def __init__(self):
        super().__init__()
        self.draw_order: tuple[int | str, ...] = ()

    def shuffle(self, values):
        super().shuffle(values)
        # TileWall.draw() pops the right edge; the review shows actual draw order.
        self.draw_order = tuple(reversed(values))


@dataclass
class RoundWallRecord:
    round_id: str
    round_number: int
    ruleset_id: str
    players: tuple[tuple[str, str, int], ...]
    draw_order: tuple[int | str, ...]
    draws: list[dict[str, Any]] = field(default_factory=list)
    event_cursor: int = 0
    valid: bool = True
    _pending_ming_gang: str | None = None

    @classmethod
    def begin(cls, game: GameState, round_number: int, rng: RecordingRandom):
        return cls(game.round_id, round_number, game.rule_adapter.ruleset_id,
                   tuple((p.player_id, p.name, p.seat) for p in game.players), rng.draw_order)

    def sync(self, game: GameState):
        """Annotate actual draws, including engine-internal kong replacements."""
        if not self.valid:
            return
        if game.round_id != self.round_id or self.event_cursor > len(game.event_log):
            self.valid = False
            return
        for event in game.event_log[self.event_cursor:]:
            kind = event["type"]
            if kind == "initial_hands_dealt":
                if self.draws:
                    self.valid = False
                    break
                for _ in range(event["tiles_per_player"]):
                    for player_id, _, _ in self.players:
                        self._append(player_id, "deal")
            elif kind == "meld_created" and event["group_type"] == "明杠":
                self._pending_ming_gang = event["player_id"]
            elif kind == "tile_drawn":
                pid = event["player_id"]
                replacement = self._pending_ming_gang == pid
                self._append(pid, "replacement" if replacement else "draw", "MING_GANG" if replacement else None)
                self._pending_ming_gang = None
            elif kind == "gang_replacement_drawn":
                self._append(event["player_id"], "replacement", event["action_type"])
        self.event_cursor = len(game.event_log)
        # A manually injected/edited wall cannot masquerade as an original record.
        if tuple(reversed(game.wall.tiles)) != self.draw_order[len(self.draws):]:
            self.valid = False

    def _append(self, player_id, kind, action_type=None):
        index = len(self.draws)
        if index >= len(self.draw_order):
            self.valid = False
            return
        self.draws.append({"position": index + 1, "tile": self.draw_order[index],
                           "kind": kind, "player_id": player_id, "action_type": action_type})

    def to_dict(self):
        return {
            "round_id": self.round_id, "round_number": self.round_number,
            "ruleset_id": self.ruleset_id,
            "players": [{"player_id": pid, "name": name, "seat": seat} for pid, name, seat in self.players],
            "total_count": len(self.draw_order), "drawn_count": len(self.draws),
            "tiles": [dict(self.draws[index]) if index < len(self.draws) else {
                "position": index + 1, "tile": value, "kind": "remaining", "player_id": None, "action_type": None,
            } for index, value in enumerate(self.draw_order)],
        }
