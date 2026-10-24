"""Resolve competing discard responses without mutating game state."""

from __future__ import annotations

from typing import Any

from simulator.actions import ActionRequest, ActionType, ResponseWindow


_PRIORITY = {
    ActionType.HU: 40,
    ActionType.MING_GANG: 30,
    ActionType.PENG: 20,
    ActionType.CHI: 10,
    ActionType.PASS: 0,
}


def resolve_response_requests(
    window: ResponseWindow,
    players_by_id: dict[str, Any],
    *,
    allow_multiple_hu: bool = True,
) -> list[ActionRequest]:
    """Return winning requests under HU > GANG > PENG > CHI > PASS rules."""
    requests = [
        request
        for request in window.requests_by_player.values()
        if request.action_type != ActionType.PASS
    ]
    if not requests:
        return []

    hu_requests = [
        request for request in requests if request.action_type == ActionType.HU
    ]
    if hu_requests:
        ordered = sorted(
            hu_requests,
            key=lambda request: _seat_distance(
                players_by_id[request.player_id].seat,
                window.discard_record.seat,
            ),
        )
        return ordered if allow_multiple_hu else ordered[:1]

    highest_priority = max(_PRIORITY[request.action_type] for request in requests)
    candidates = [
        request
        for request in requests
        if _PRIORITY[request.action_type] == highest_priority
    ]
    return [
        min(
            candidates,
            key=lambda request: _seat_distance(
                players_by_id[request.player_id].seat,
                window.discard_record.seat,
            ),
        )
    ]


def _seat_distance(candidate_seat: int, source_seat: int) -> int:
    return (candidate_seat - source_seat) % 4
