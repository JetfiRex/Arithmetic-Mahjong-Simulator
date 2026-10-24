"""Action and timer primitives for the game-flow simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class ActionType(str, Enum):
    PASS = "PASS"
    DISCARD = "DISCARD"
    CHI = "CHI"
    PENG = "PENG"
    MING_GANG = "MING_GANG"
    AN_GANG = "AN_GANG"
    JIA_GANG = "JIA_GANG"
    HU = "HU"


class TimerKind(str, Enum):
    RESPONSE_WINDOW = "RESPONSE_WINDOW"
    DETAIL_SELECTION = "DETAIL_SELECTION"
    TURN_DECISION = "TURN_DECISION"
    ROB_KONG_RESPONSE = "ROB_KONG_RESPONSE"


class ResponseWindowStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class TimeoutConfig:
    discard_response_seconds: Optional[float] = None
    action_detail_seconds: Optional[float] = None
    turn_action_seconds: Optional[float] = None
    rob_kong_seconds: Optional[float] = None


@dataclass
class TimerState:
    kind: TimerKind
    started_at: float
    deadline_at: float
    timeout_seconds: float

    @classmethod
    def start(cls, kind: TimerKind, now: float, timeout_seconds: float) -> "TimerState":
        return cls(
            kind=kind,
            started_at=now,
            deadline_at=now + timeout_seconds,
            timeout_seconds=timeout_seconds,
        )

    def is_expired(self, now: float) -> bool:
        return now >= self.deadline_at

    def remaining_seconds(self, now: float) -> float:
        return max(0.0, self.deadline_at - now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ActionOption:
    action_type: ActionType
    priority: int = 0
    payload_options: list[dict[str, Any]] = field(default_factory=list)
    requires_detail: bool = False
    display_hint: str = ""

    def is_pass_only(self) -> bool:
        return self.action_type == ActionType.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "priority": self.priority,
            "payload_options": list(self.payload_options),
            "requires_detail": self.requires_detail,
            "display_hint": self.display_hint,
        }


@dataclass
class ActionRequest:
    player_id: str
    action_type: ActionType
    payload: dict[str, Any] = field(default_factory=dict)
    submitted_at: Optional[float] = None
    source_window_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "player_id": self.player_id,
            "action_type": self.action_type.value,
            "payload": dict(self.payload),
            "submitted_at": self.submitted_at,
            "source_window_id": self.source_window_id,
        }


@dataclass
class ResolvedAction:
    action_type: ActionType
    player_id: Optional[str] = None
    source_player_id: Optional[str] = None
    claimed_tile: Any = None
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "player_id": self.player_id,
            "source_player_id": self.source_player_id,
            "claimed_tile": self.claimed_tile,
            "payload": dict(self.payload),
            "reason": self.reason,
        }


@dataclass
class ActionDetailState:
    player_id: str
    action_type: ActionType
    source_player_id: str
    claimed_tile: Any
    selection_mode: str
    payload_options: list[dict[str, Any]]
    opened_at: float
    deadline_at: Optional[float]
    source_window_id: str
    detail_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self, *, include_options: bool = True) -> dict[str, Any]:
        data = {
            "detail_id": self.detail_id,
            "player_id": self.player_id,
            "action_type": self.action_type.value,
            "source_player_id": self.source_player_id,
            "claimed_tile": self.claimed_tile,
            "selection_mode": self.selection_mode,
            "opened_at": self.opened_at,
            "deadline_at": self.deadline_at,
            "source_window_id": self.source_window_id,
        }
        if include_options:
            data["payload_options"] = list(self.payload_options)
        return data


@dataclass
class ResponseWindow:
    source_player_id: str
    discard_record: Any
    opened_at: float
    deadline_at: Optional[float]
    options_by_player: dict[str, list[ActionOption]]
    window_id: str = field(default_factory=lambda: str(uuid4()))
    requests_by_player: dict[str, ActionRequest] = field(default_factory=dict)
    auto_passed_players: set[str] = field(default_factory=set)
    status: ResponseWindowStatus = ResponseWindowStatus.OPEN

    def players(self) -> set[str]:
        return set(self.options_by_player)

    def players_requiring_input(self) -> set[str]:
        return self.players() - self.auto_passed_players

    def is_complete(self) -> bool:
        return self.players_requiring_input().issubset(self.requests_by_player)

    def submit(self, request: ActionRequest) -> None:
        self.requests_by_player[request.player_id] = request

    def auto_pass_unsubmitted(self, submitted_at: Optional[float] = None) -> None:
        for player_id in self.players():
            if player_id in self.requests_by_player:
                continue
            self.requests_by_player[player_id] = ActionRequest(
                player_id=player_id,
                action_type=ActionType.PASS,
                submitted_at=submitted_at,
                source_window_id=self.window_id,
            )
            self.auto_passed_players.add(player_id)

    def visible_options_for_player(self, player_id: str) -> list[ActionOption]:
        if player_id in self.auto_passed_players:
            return []
        return self.options_by_player.get(player_id, [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "source_player_id": self.source_player_id,
            "discard_record": self.discard_record.to_dict(),
            "opened_at": self.opened_at,
            "deadline_at": self.deadline_at,
            "status": self.status.value,
            "options_by_player": {
                player_id: [option.to_dict() for option in options]
                for player_id, options in self.options_by_player.items()
            },
            "requests_by_player": {
                player_id: request.to_dict()
                for player_id, request in self.requests_by_player.items()
            },
            "auto_passed_players": sorted(self.auto_passed_players),
        }


@dataclass
class RobKongWindow:
    """Hold a proposed jia-gang while only eligible winners choose HU/PASS."""

    source_player_id: str
    tile: Any
    payload: dict[str, Any]
    selected_tile_ids: list[str]
    opened_at: float
    deadline_at: Optional[float]
    options_by_player: dict[str, list[ActionOption]]
    paused_turn_timer: Optional[TimerState]
    source_turn_window_id: Optional[str]
    window_id: str = field(default_factory=lambda: str(uuid4()))
    requests_by_player: dict[str, ActionRequest] = field(default_factory=dict)

    def is_complete(self) -> bool:
        return self.options_by_player.keys() <= self.requests_by_player.keys()

    def to_player_dict(self, player_id: str) -> dict[str, Any]:
        """Never include another player's options, response, or countdown."""
        return {
            "window_id": self.window_id,
            "source_player_id": self.source_player_id,
            "tile": self.tile,
            "opened_at": self.opened_at,
            "deadline_at": self.deadline_at,
            "options": [option.to_dict() for option in self.options_by_player[player_id]],
            "submitted": player_id in self.requests_by_player,
        }
