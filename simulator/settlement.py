"""Settlement contracts and payment rules for a normal four-player round."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from simulator.rule_adapter import WinMethod, WinResult


class RoundEndReason(str, Enum):
    HU = "hu"
    WALL_EMPTY = "wall_empty"
    BLOOD_BATTLE_COMPLETE = "blood_battle_complete"


@dataclass(frozen=True)
class ScoreDelta:
    player_id: str
    delta: int

    def to_dict(self) -> dict[str, Any]:
        return {"player_id": self.player_id, "delta": self.delta}


@dataclass(frozen=True)
class PlayerScore:
    player_id: str
    score: int

    def to_dict(self) -> dict[str, Any]:
        return {"player_id": self.player_id, "score": self.score}


@dataclass(frozen=True)
class SettlementContext:
    round_id: str
    winner_id: str
    source_player_id: str | None
    eligible_payer_ids: tuple[str, ...]


@dataclass(frozen=True)
class SettlementEvent:
    settlement_id: str
    round_id: str
    winner_id: str
    source_player_id: str | None
    win_result: WinResult
    score_deltas: tuple[ScoreDelta, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "round_id": self.round_id,
            "winner_id": self.winner_id,
            "source_player_id": self.source_player_id,
            "win_result": self.win_result.to_dict(),
            "score_deltas": [delta.to_dict() for delta in self.score_deltas],
        }


@dataclass(frozen=True)
class RoundResult:
    round_id: str
    end_reason: RoundEndReason
    dealer_seat: int
    next_dealer_seat: int
    winner_ids: tuple[str, ...]
    settlement_events: tuple[SettlementEvent, ...]
    final_scores: tuple[PlayerScore, ...]
    score_deltas: tuple[ScoreDelta, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "end_reason": self.end_reason.value,
            "dealer_seat": self.dealer_seat,
            "next_dealer_seat": self.next_dealer_seat,
            "winner_ids": list(self.winner_ids),
            "settlement_events": [
                event.to_dict() for event in self.settlement_events
            ],
            "final_scores": [score.to_dict() for score in self.final_scores],
            "score_deltas": [delta.to_dict() for delta in self.score_deltas],
        }


@runtime_checkable
class ScoringAdapter(Protocol):
    def settle_win(
        self,
        win_result: WinResult,
        context: SettlementContext,
    ) -> SettlementEvent:
        ...


class NormalScoringAdapter:
    """Use the calculator's per-payer amount without interpreting fan names."""

    def settle_win(
        self,
        win_result: WinResult,
        context: SettlementContext,
    ) -> SettlementEvent:
        if not win_result.can_win:
            raise ValueError("Cannot settle an invalid win")
        amount = win_result.base_payment
        if type(amount) is not int or amount < 0:
            raise ValueError("Base payment must be a nonnegative integer")
        eligible = context.eligible_payer_ids
        if len(eligible) != 3 or len(set(eligible)) != 3:
            raise ValueError("A normal round requires three distinct opponents")
        if context.winner_id in eligible:
            raise ValueError("The winner cannot be a payer")

        if win_result.win_method in (WinMethod.DISCARD, WinMethod.ROB_KONG):
            if context.source_player_id not in eligible:
                raise ValueError("Discard win requires an eligible discarder")
            payer_ids = (context.source_player_id,)
        elif win_result.win_method == WinMethod.SELF_DRAW:
            if context.source_player_id is not None:
                raise ValueError("Self draw cannot have a discarder")
            payer_ids = eligible
        else:
            raise ValueError("This win method is not implemented in normal scoring")

        return SettlementEvent(
            settlement_id=str(uuid4()),
            round_id=context.round_id,
            winner_id=context.winner_id,
            source_player_id=context.source_player_id,
            win_result=win_result,
            score_deltas=(
                ScoreDelta(context.winner_id, amount * len(payer_ids)),
                *(ScoreDelta(player_id, -amount) for player_id in payer_ids),
            ),
        )
