"""Immutable player information. Never serialize an engine/debug snapshot here."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import time

from calculator_base.constants import TILE_COUNTS
from simulator.game_state import GameState

TILE_VALUES = tuple(sorted(v for v in TILE_COUNTS if isinstance(v, int))) + ("+", "×")
TILE_INDEX = {v: i for i, v in enumerate(TILE_VALUES)}
OBSERVATION_VERSION = "bot-observation-v1"


@dataclass(frozen=True)
class DecisionContext:
    budget_ms: float = 500.0
    deadline_monotonic: float | None = None
    game_remaining_ms: float | None = None

    def remaining_ms(self) -> float:
        if self.deadline_monotonic is None:
            return self.budget_ms
        return max(0.0, min(self.budget_ms, (self.deadline_monotonic - time.monotonic()) * 1000))


@dataclass(frozen=True)
class MeldView:
    group_type: str
    tiles: tuple[int | str, ...]


@dataclass(frozen=True)
class PlayerView:
    seat: int
    hand_count: int
    score: int
    melds: tuple[MeldView, ...]
    discards: tuple[int | str, ...]


@dataclass(frozen=True)
class HistoryEvent:
    kind: str
    actor: int | None = None
    tile: int | str | None = None
    tiles: tuple[int | str, ...] = ()
    detail: str = ""
    private: bool = False


@dataclass(frozen=True)
class Observation:
    version: str
    ruleset_id: str
    min_fan: int | None
    scoring_id: str
    allow_multiple_hu: bool
    tile_supply: tuple[int, ...]
    phase: str
    dealer: int
    current_player: int
    hand_counts: tuple[int, ...]
    drawn_tile: int | str | None
    players: tuple[PlayerView, ...]
    wall_remaining: int
    source: int | None
    target_tile: int | str | None
    last_discard: tuple[int, int | str] | None
    history: tuple[HistoryEvent, ...]
    decision_context: DecisionContext


class VisibleHistory:
    """Trusted runtime recorder; each policy receives only its projected events.

    Call sync after *each* engine transition, including automatic draws/expiry.
    Starting from a mid-round fixture supplies a current-hand bootstrap, not a
    fabricated perfect-recall history of earlier private draws.
    """

    def __init__(self, game: GameState):
        self.round_id = game.round_id
        self.cursor = 0
        self.events: list[tuple[str | None, HistoryEvent]] = []
        self.ids = {}
        for p in game.players:
            p.hand_tile_records()
            self.ids[p.player_id] = set(p.hand_tile_ids)
            self.events.append((p.player_id, HistoryEvent(
                "initial_hand", p.seat, tiles=tuple(sorted(p.hand_tiles, key=TILE_INDEX.get)), private=True,
            )))
        self.sync(game)

    def sync(self, game: GameState) -> None:
        if game.round_id != self.round_id:
            raise ValueError("Create a new history recorder for a new round")
        # Explicit allowlist: no response_submitted, private grouping, raw IDs,
        # sequence gaps, eligibility lists, or engine timing events.
        for e in game.event_log[self.cursor:]:
            kind = e["type"]
            actor = game.get_player(e["player_id"]).seat if "player_id" in e else None
            event = None
            if kind in ("tile_drawn", "gang_replacement_drawn"):
                event = HistoryEvent("draw", actor)
            elif kind == "tile_discarded":
                event = HistoryEvent("discard", actor, e["tile"])
            elif kind in ("meld_created", "meld_upgraded"):
                event = HistoryEvent("meld", actor, tiles=tuple(e["meld"]["tiles"]), detail=e["group_type"])
            elif kind == "jia_gang_declared":
                event = HistoryEvent("jia_gang_declared", actor, e["tile"])
            elif kind == "round_started":
                event = HistoryEvent("round_started", e["dealer_seat"])
            elif kind == "round_ended":
                event = HistoryEvent("round_ended", detail=e["reason"])
            elif kind == "win_settled":
                s = e["settlement"]
                event = HistoryEvent("win", game.get_player(s["winner_id"]).seat,
                                     s["win_result"]["winning_tile"], detail=s["win_result"]["win_method"])
            if event is not None:
                self.events.append((None, event))
        self.cursor = len(game.event_log)
        for p in game.players:
            p.hand_tile_records()
            for tid, value in zip(p.hand_tile_ids, p.hand_tiles):
                if tid not in self.ids[p.player_id]:
                    self.events.append((p.player_id, HistoryEvent("own_draw", p.seat, value, private=True)))
            self.ids[p.player_id] = set(p.hand_tile_ids)

    def action(self, game: GameState, player_id: str, action) -> None:
        self.events.append((player_id, HistoryEvent(
            "own_action", game.get_player(player_id).seat, action.tile,
            action.required_values, action.action_type.value, True,
        )))

    def for_player(self, game: GameState, player_id: str) -> tuple[HistoryEvent, ...]:
        seat = game.get_player(player_id).seat
        return tuple(replace(e, actor=(e.actor - seat) % 4 if e.actor is not None else None)
                     for owner, e in self.events if owner is None or owner == player_id)


def observe(game: GameState, player_id: str, history: VisibleHistory,
            context: DecisionContext | None = None) -> Observation:
    p = game.get_player(player_id)
    relative = lambda seat: (seat - p.seat) % 4
    source, target = None, None
    if game.phase.value == "WAITING_RESPONSE" and game.response_window:
        source = relative(game.get_player(game.response_window.source_player_id).seat)
        target = game.response_window.discard_record.tile
    elif game.phase.value == "WAITING_ACTION_DETAIL" and game.pending_action_detail:
        source = relative(game.get_player(game.pending_action_detail.source_player_id).seat)
        target = game.pending_action_detail.claimed_tile
    elif game.phase.value == "WAITING_ROB_KONG" and game.rob_kong_window:
        source = relative(game.get_player(game.rob_kong_window.source_player_id).seat)
        target = game.rob_kong_window.tile
    players = []
    for offset in range(4):
        other = next(x for x in game.players if relative(x.seat) == offset)
        players.append(PlayerView(offset, len(other.hand_tiles), other.score,
                                  tuple(MeldView(m["group_type"], tuple(m["tiles"])) for m in other.melded_groups),
                                  tuple(other.discarded_tiles)))
    counts = Counter(p.hand_tiles)
    drawn = p.tile_value(game.drawn_tile_id) if (
        game.current_turn_seat == p.seat and game.drawn_tile_id in p.hand_tile_ids
    ) else None
    last = game.last_discard
    return Observation(
        OBSERVATION_VERSION, game.rule_adapter.ruleset_id, getattr(game.rule_adapter, "min_fan", None),
        type(game.scoring_adapter).__name__, game.config.allow_multiple_hu,
        tuple(TILE_COUNTS[v] for v in TILE_VALUES), game.phase.value,
        relative(game.dealer_seat), relative(game.current_turn_seat),
        tuple(counts[v] for v in TILE_VALUES), drawn, tuple(players), game.wall.remaining_count(),
        source, target, (relative(last.seat), last.tile) if last else None,
        history.for_player(game, player_id), context or DecisionContext(),
    )
