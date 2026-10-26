"""Private, versioned game checkpoints. Never send these to a player client."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import math
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from calculator_base.constants import ALL_TILES
from simulator.actions import (
    ActionDetailState, ActionOption, ActionRequest, ActionType, ResolvedAction,
    ResponseWindow, ResponseWindowStatus, RobKongWindow, TimeoutConfig,
    TimerKind, TimerState,
)
from simulator.config import GameConfig
from simulator.game_state import DiscardRecord, GamePhase, GameState, PlayerState, TileWall
from simulator.hand_state import HandGroup, HandTile
from simulator.rule_adapter import ArithmeticMahjongRuleAdapter, FanItem, WinMethod, WinResult
from simulator.settlement import (
    NormalScoringAdapter, PlayerScore, RoundEndReason, RoundResult, ScoreDelta,
    SettlementEvent,
)


SCHEMA_VERSION = 1
_RECORDS = {cls.__name__: cls for cls in (
    GameState, PlayerState, TileWall, DiscardRecord, HandGroup, HandTile,
    ActionDetailState, ActionOption, ActionRequest, ResolvedAction, ResponseWindow,
    RobKongWindow, TimeoutConfig, TimerState, GameConfig, FanItem, WinResult,
    PlayerScore, RoundResult, ScoreDelta, SettlementEvent,
)}
_ENUMS = {cls.__name__: cls for cls in (
    GamePhase, ActionType, ResponseWindowStatus, TimerKind, WinMethod, RoundEndReason,
)}
_SKIP = {GameState: {"rule_adapter", "scoring_adapter"}, PlayerState: {"_identity_values"}}
_TRANSIENT_PHASES = {GamePhase.DEALING, GamePhase.RESOLVING_ACTION, GamePhase.SETTLING}


class SnapshotError(ValueError):
    """A checkpoint is incompatible, malformed, or unsafe to restore."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SnapshotError(message)


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        _require(_ENUMS.get(type(value).__name__) is type(value), "Unsupported checkpoint enum")
        return {"enum": type(value).__name__, "value": value.value}
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        _require(math.isfinite(value), "Non-finite checkpoint number")
        return value
    if type(value) is list:
        return [_encode(item) for item in value]
    if type(value) in (tuple, set):
        items = sorted(value) if type(value) is set else value
        return {"set" if type(value) is set else "tuple": [_encode(item) for item in items]}
    if type(value) is dict:
        _require(all(type(key) is str for key in value), "Checkpoint maps require string keys")
        return {"map": {key: _encode(item) for key, item in value.items()}}
    if is_dataclass(value) and _RECORDS.get(type(value).__name__) is type(value):
        return {"record": type(value).__name__, "fields": {
            field.name: _encode(getattr(value, field.name)) for field in fields(value)
            if field.name not in _SKIP.get(type(value), set())
        }}
    raise SnapshotError(f"Unsupported checkpoint value: {type(value).__name__}")


def _matches(value: Any, annotation: Any) -> bool:
    if annotation is Any:
        return True
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        return any(_matches(value, item) for item in args)
    if origin in (list, set):
        return type(value) is origin and all(_matches(item, args[0]) for item in value)
    if origin is dict:
        return type(value) is dict and all(
            _matches(key, args[0]) and _matches(item, args[1]) for key, item in value.items()
        )
    if origin is tuple:
        if type(value) is not tuple:
            return False
        if len(args) == 2 and args[1] is Ellipsis:
            return all(_matches(item, args[0]) for item in value)
        return len(args) == len(value) and all(_matches(item, hint) for item, hint in zip(value, args))
    if annotation is float:
        return _number(value)
    return type(value) is annotation


def _decode(value: Any, *, adapters: dict[str, Any], depth: int = 0) -> Any:
    _require(depth < 80, "Checkpoint nesting is too deep")
    decode = lambda item: _decode(item, adapters=adapters, depth=depth + 1)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        _require(math.isfinite(value), "Non-finite checkpoint number")
        return value
    if type(value) is list:
        return [decode(item) for item in value]
    _require(type(value) is dict, "Invalid checkpoint value")
    if set(value) == {"enum", "value"}:
        cls = _ENUMS.get(value["enum"]) if type(value["enum"]) is str else None
        _require(cls is not None, "Unknown checkpoint enum")
        try:
            return cls(value["value"])
        except (TypeError, ValueError) as exc:
            raise SnapshotError("Invalid checkpoint enum value") from exc
    if len(value) == 1 and next(iter(value)) in {"map", "tuple", "set"}:
        kind, items = next(iter(value.items()))
        if kind == "map":
            _require(type(items) is dict and all(type(key) is str for key in items), "Invalid map")
            return {key: decode(item) for key, item in items.items()}
        _require(type(items) is list, "Invalid checkpoint collection")
        decoded = [decode(item) for item in items]
        try:
            result = tuple(decoded) if kind == "tuple" else set(decoded)
        except TypeError as exc:
            raise SnapshotError("Invalid checkpoint set") from exc
        _require(kind != "set" or len(result) == len(decoded), "Duplicate set member")
        return result
    _require(set(value) == {"record", "fields"}, "Unknown checkpoint record structure")
    cls = _RECORDS.get(value["record"]) if type(value["record"]) is str else None
    _require(cls is not None, "Unknown checkpoint record")
    field_map = {field.name: field for field in fields(cls) if field.name not in _SKIP.get(cls, set())}
    raw = value["fields"]
    _require(type(raw) is dict and set(raw) == set(field_map), "Checkpoint fields do not match schema")
    decoded = {name: decode(item) for name, item in raw.items()}
    hints = get_type_hints(cls)
    for name, item in decoded.items():
        _require(_matches(item, hints[name]), f"Invalid field type: {cls.__name__}.{name}")
    kwargs = {name: item for name, item in decoded.items() if field_map[name].init}
    if cls is PlayerState:
        # Its normal constructor intentionally clears groups for a newly dealt hand.
        kwargs.pop("hand_groups")
    if cls is GameState:
        kwargs.update(adapters)
    result = cls(**kwargs)
    for name, item in decoded.items():
        if cls is PlayerState or not field_map[name].init:
            object.__setattr__(result, name, item)
    return result


def _adapter_name(adapter: Any) -> str:
    return f"{type(adapter).__module__}.{type(adapter).__qualname__}"


def _adapter_descriptions(game: GameState) -> dict[str, Any]:
    rule = game.rule_adapter
    rule_data = {"kind": _adapter_name(rule), "ruleset_id": rule.ruleset_id}
    if type(rule) is ArithmeticMahjongRuleAdapter:
        rule_data["min_fan"] = rule.min_fan
    return {"rule": rule_data, "scoring": {"kind": _adapter_name(game.scoring_adapter)}}


def _restore_adapters(raw: Any, rule_adapter: Any, scoring_adapter: Any) -> dict[str, Any]:
    _require(type(raw) is dict and set(raw) == {"rule", "scoring"}, "Missing adapter descriptions")
    rule_data, scoring_data = raw["rule"], raw["scoring"]
    _require(type(rule_data) is dict and type(scoring_data) is dict, "Invalid adapters")
    builtin_rule = _adapter_name(ArithmeticMahjongRuleAdapter())
    if rule_adapter is None:
        _require(set(rule_data) == {"kind", "ruleset_id", "min_fan"}, "A custom rule adapter must be supplied")
        _require(rule_data["kind"] == builtin_rule, "Unknown rule adapter")
        _require(type(rule_data["min_fan"]) is int and rule_data["min_fan"] >= 0, "Invalid minimum fan")
        _require(type(rule_data["ruleset_id"]) is str and bool(rule_data["ruleset_id"]), "Invalid ruleset ID")
        rule_adapter = ArithmeticMahjongRuleAdapter(
            min_fan=rule_data["min_fan"], ruleset_id=rule_data["ruleset_id"],
        )
    if scoring_adapter is None:
        _require(scoring_data == {"kind": _adapter_name(NormalScoringAdapter())}, "A custom scoring adapter must be supplied")
        scoring_adapter = NormalScoringAdapter()
    probe = GameState(players=[], wall=TileWall([]), rule_adapter=rule_adapter, scoring_adapter=scoring_adapter)
    _require(_adapter_descriptions(probe) == raw, "Checkpoint adapter configuration does not match")
    return {"rule_adapter": rule_adapter, "scoring_adapter": scoring_adapter}


def shift_game_clock(game: GameState, offset: float) -> None:
    """Move all live clock references together, including t4's paused t3."""
    _require(_number(offset), "Invalid clock offset")
    timers = [game.active_timer]
    windows = [game.response_window, game.pending_action_detail, game.rob_kong_window]
    if game.rob_kong_window is not None:
        timers.append(game.rob_kong_window.paused_turn_timer)
    for timer in timers:
        if timer is not None:
            timer.started_at += offset
            timer.deadline_at += offset
    for window in windows:
        if window is not None:
            window.opened_at += offset
            if window.deadline_at is not None:
                window.deadline_at += offset
            if isinstance(window, (ResponseWindow, RobKongWindow)):
                for request in window.requests_by_player.values():
                    if request.submitted_at is not None:
                        request.submitted_at += offset


def validate_game_state(game: GameState) -> None:
    """Reject broken identities/references instead of repairing or redealing them."""
    _require(game.phase not in _TRANSIENT_PHASES, "Cannot checkpoint an in-progress transition")
    _require(len(game.players) == 4 and [player.seat for player in game.players] == list(range(4)), "Invalid seats")
    ids = {player.player_id for player in game.players}
    _require(len(ids) == 4 and all(ids), "Invalid player identities")
    _require(game.current_turn_seat in range(4) and game.dealer_seat in range(4), "Invalid current/dealer seat")
    _require(bool(game.game_id) and bool(game.round_id), "Missing game identity")
    all_hand_ids: set[str] = set()

    def tiles(values):
        _require(all(type(tile) in (str, int) and tile in ALL_TILES for tile in values), "Invalid tile value")

    tiles(game.wall.tiles)
    for player in game.players:
        tiles(player.hand_tiles)
        tiles(player.discarded_tiles)
        _require(len(player.hand_tiles) == len(player.hand_tile_ids), "Mismatched tile identities")
        _require(all(player.hand_tile_ids) and not all_hand_ids.intersection(player.hand_tile_ids), "Duplicate/empty tile identity")
        _require(len(set(player.hand_tile_ids)) == len(player.hand_tile_ids), "Duplicate hand tile identity")
        all_hand_ids.update(player.hand_tile_ids)
        grouped = [tile_id for group in player.hand_groups for tile_id in group.tile_ids]
        _require(len(grouped) == len(set(grouped)) and set(grouped) <= set(player.hand_tile_ids), "Broken group membership")
        _require(all(group.tile_ids and group.group_id for group in player.hand_groups), "Empty hand group")
        _require(len({group.group_id for group in player.hand_groups}) == len(player.hand_groups), "Duplicate group ID")
        expected = grouped + [tile_id for tile_id in player.hand_tile_ids if tile_id not in set(grouped)]
        _require(expected == player.hand_tile_ids, "Hand/group order is inconsistent")
        for meld in player.melded_groups:
            _require(type(meld.get("tiles")) is list, "Invalid meld")
            tiles(meld["tiles"])
    if game.drawn_tile_id is not None:
        _require(game.drawn_tile_id in game.current_player().hand_tile_ids, "Missing drawn tile")
    phase_windows = (
        (GamePhase.WAITING_RESPONSE, game.response_window),
        (GamePhase.WAITING_ACTION_DETAIL, game.pending_action_detail),
        (GamePhase.WAITING_ROB_KONG, game.rob_kong_window),
    )
    for phase, window in phase_windows:
        _require((game.phase == phase) == (window is not None), "Window/phase mismatch")
        if window is not None:
            _require(_number(window.opened_at) and (window.deadline_at is None or _number(window.deadline_at)), "Invalid window clock")
    for window in (game.response_window, game.rob_kong_window):
        if window is None:
            continue
        _require(window.source_player_id in ids and bool(window.window_id), "Invalid window source")
        _require(set(window.options_by_player) <= ids - {window.source_player_id}, "Invalid responders")
        _require(set(window.requests_by_player) <= set(window.options_by_player), "Unknown response owner")
        for owner, request in window.requests_by_player.items():
            _require(request.player_id == owner and request.source_window_id == window.window_id, "Broken request reference")
            _require(request.action_type in {option.action_type for option in window.options_by_player[owner]}, "Invalid saved response")
    if game.response_window is not None:
        window = game.response_window
        _require(type(window.discard_record) is DiscardRecord, "Invalid discard record")
        _require(window.discard_record == game.last_discard and window.source_player_id == window.discard_record.player_id, "Mismatched discard window")
        _require(window.auto_passed_players <= set(window.options_by_player), "Unknown automatic responder")
    if game.pending_action_detail is not None:
        detail = game.pending_action_detail
        _require(detail.player_id in ids and detail.action_type == ActionType.CHI, "Invalid detail owner")
        _require(detail.selection_mode in {"manual", "listed"}, "Invalid detail mode")
    if game.rob_kong_window is not None:
        rob = game.rob_kong_window
        source = game.get_player(rob.source_player_id)
        _require(source is game.current_player() and rob.source_turn_window_id == game.turn_window_id, "Invalid robbed turn")
        _require(len(rob.selected_tile_ids) == 1 and rob.selected_tile_ids[0] in source.hand_tile_ids, "Missing added-kong tile")
        _require(source.tile_value(rob.selected_tile_ids[0]) == rob.tile, "Invalid added-kong tile")
    if game.phase in {GamePhase.WAITING_TURN_ACTION, GamePhase.WAITING_ROB_KONG}:
        _require(bool(game.turn_window_id), "Missing turn window")
    for duration in vars(game.timeout_config).values():
        _require(duration is None or (_number(duration) and duration > 0), "Invalid timeout configuration")
    if game.active_timer is not None:
        expected_kind = {
            GamePhase.WAITING_RESPONSE: TimerKind.RESPONSE_WINDOW,
            GamePhase.WAITING_ACTION_DETAIL: TimerKind.DETAIL_SELECTION,
            GamePhase.WAITING_TURN_ACTION: TimerKind.TURN_DECISION,
            GamePhase.WAITING_ROB_KONG: TimerKind.ROB_KONG_RESPONSE,
        }.get(game.phase)
        _require(game.active_timer.kind == expected_kind, "Timer/phase mismatch")
        timer = game.active_timer
        _require(all(_number(value) for value in (timer.started_at, timer.deadline_at, timer.timeout_seconds)) and timer.timeout_seconds > 0, "Invalid timer")
        window = game.response_window or game.pending_action_detail or game.rob_kong_window
        _require(window is None or timer.deadline_at == window.deadline_at, "Timer/window deadline mismatch")
    if game.round_result is not None:
        result = game.round_result
        _require(game.phase == GamePhase.ROUND_ENDED and result.round_id == game.round_id, "Invalid round result")
        _require(result.settlement_events == tuple(game.settlement_events), "Mismatched settlement events")
        _require({item.player_id: item.score for item in result.final_scores} == {player.player_id: player.score for player in game.players}, "Mismatched final scores")
        _require(sum(item.delta for item in result.score_deltas) == 0, "Unbalanced settlement")
    _require(len({event.settlement_id for event in game.settlement_events}) == len(game.settlement_events), "Duplicate settlement identity")


def capture_game_state(game: GameState, *, now: float) -> dict[str, Any]:
    """Create a detached private checkpoint; no timers or actions are advanced."""
    _require(_number(now), "Invalid checkpoint clock")
    validate_game_state(game)
    adapters = {"rule_adapter": game.rule_adapter, "scoring_adapter": game.scoring_adapter}
    clone = _decode(_encode(game), adapters=adapters)
    shift_game_clock(clone, -now)
    return {
        "schema_version": SCHEMA_VERSION, "kind": "game-state",
        "clock": "relative-v1", "adapters": _adapter_descriptions(game),
        "state": _encode(clone),
    }


def restore_game_state(
    snapshot: dict[str, Any], *, now: float, rule_adapter: Any = None, scoring_adapter: Any = None,
) -> GameState:
    """Rebuild a checkpoint at a new clock origin without drawing or settling."""
    _require(_number(now), "Invalid restore clock")
    _require(type(snapshot) is dict and set(snapshot) == {"schema_version", "kind", "clock", "adapters", "state"}, "Invalid checkpoint envelope")
    _require(type(snapshot["schema_version"]) is int and snapshot["schema_version"] == SCHEMA_VERSION, "Unsupported checkpoint schema")
    _require(snapshot["kind"] == "game-state" and snapshot["clock"] == "relative-v1", "Unsupported checkpoint kind/clock")
    try:
        adapters = _restore_adapters(snapshot["adapters"], rule_adapter, scoring_adapter)
        game = _decode(snapshot["state"], adapters=adapters)
        _require(type(game) is GameState, "Checkpoint root is not a GameState")
        validate_game_state(game)
        shift_game_clock(game, now)
        return game
    except (KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise SnapshotError("Malformed game checkpoint") from exc
