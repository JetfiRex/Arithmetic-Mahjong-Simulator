"""Concrete bot candidates; the human ActionOption contract is left intact."""

from dataclasses import dataclass
from itertools import combinations

from simulator.actions import ActionType
from simulator.action_options import build_chi_detail_payload, is_valid_chi_selection
from simulator.game_state import GamePhase, GameState
from bots.common_bot_helper.observation import Observation, TILE_INDEX

ACTION_VERSION = "bot-actions-v1"


@dataclass(frozen=True)
class BotAction:
    action_id: str
    action_type: ActionType
    tile: int | str | None = None
    required_values: tuple[int | str, ...] = ()
    meld_index: int | None = None


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    player_id: str
    observation: Observation
    legal_actions: tuple[BotAction, ...]
    terminal: bool = False


@dataclass(frozen=True)
class StyleActionFeatures:
    progress: float
    danger_index: float
    feeding_index: float
    projected_discard: int | str | None
    known_ready: bool
    live_wait_copies: int
    max_ready_base_payment: int
    target_family: str | None = None
    target_missing: int | None = None
    target_base_payment: int | None = None
    target_verified: bool = False


@dataclass(frozen=True)
class StyleDiagnostics:
    version: str
    profile_version: str
    risk_version: str
    target_version: str
    profile: str
    containment: float
    target_family: str | None
    target_counts: tuple[int, ...] | None
    target_note: str
    known_ready: bool
    folded: bool
    pressure: float
    note: str = ''


@dataclass(frozen=True)
class ActionScore:
    action_id: str
    score: float | None
    potential: float | None
    family: str
    analysis_state: str
    # tile, unseen copies, discard base payment, self-draw total payment
    live_waits: tuple[tuple[int | str, int, int, int], ...] = ()
    note: str = ''
    distance_lower: int | None = None
    distance_upper: int | None = None
    distance_complete: bool = False
    improving_tiles: tuple[tuple[int | str, int, int], ...] = ()
    ukeire_complete: bool = False
    samples: int = 0
    sample_mean: float | None = None
    sample_variance: float | None = None
    baseline_score: float | None = None
    style: StyleActionFeatures | None = None


@dataclass(frozen=True)
class SearchDiagnostics:
    version: str
    belief_version: str
    rollout_version: str
    root_action_ids: tuple[str, ...]
    completed_batches: int
    discarded_batches: int
    terminal_rollouts: int
    horizon: int
    distance_nodes: int
    distance_cache_hits: int
    baseline_action_id: str
    note: str = ''


@dataclass(frozen=True)
class NeuralDiagnostics:
    version: str
    feature_version: str
    status: str
    model_sha256: str | None
    style: str
    head_weights: tuple[float, ...]
    encoding_ms: float = 0.
    inference_ms: float = 0.
    reason: str = ''


@dataclass(frozen=True)
class DecisionExplanation:
    version: str
    selected_action_id: str
    scores: tuple[ActionScore, ...]
    budget_limited: bool
    oracle_calls: int
    cache_hits: int
    elapsed_ms: float
    note: str = ''
    search: SearchDiagnostics | None = None
    style: StyleDiagnostics | None = None
    neural: NeuralDiagnostics | None = None


@dataclass(frozen=True)
class Binding:
    action: BotAction
    tile_id: str | None = None
    option_id: str | None = None
    selected_tile_ids: tuple[str, ...] = ()


def action(kind, tile=None, required=(), meld_index=None) -> BotAction:
    required = tuple(sorted(required, key=TILE_INDEX.get))
    key = f"{kind.value}:{TILE_INDEX[tile] if tile is not None else '-'}:"
    key += ",".join(str(TILE_INDEX[v]) for v in required)
    key += f":{meld_index if meld_index is not None else '-'}"
    return BotAction(key, kind, tile, required, meld_index)


def window_key(game: GameState) -> tuple[str, str | None]:
    window = {
        GamePhase.WAITING_TURN_ACTION: game.turn_window_id,
        GamePhase.WAITING_RESPONSE: game.response_window.window_id if game.response_window else None,
        GamePhase.WAITING_ACTION_DETAIL: game.pending_action_detail.detail_id if game.pending_action_detail else None,
        GamePhase.WAITING_ROB_KONG: game.rob_kong_window.window_id if game.rob_kong_window else None,
    }.get(game.phase)
    return game.phase.value, window


def eligible_players(game: GameState) -> tuple[str, ...]:
    if game.phase == GamePhase.WAITING_TURN_ACTION:
        return (game.current_player().player_id,)
    if game.phase == GamePhase.WAITING_RESPONSE and game.response_window:
        w = game.response_window
        return tuple(sorted(w.players_requiring_input() - w.requests_by_player.keys()))
    if game.phase == GamePhase.WAITING_ACTION_DETAIL and game.pending_action_detail:
        return (game.pending_action_detail.player_id,)
    if game.phase == GamePhase.WAITING_ROB_KONG and game.rob_kong_window:
        w = game.rob_kong_window
        return tuple(sorted(w.options_by_player.keys() - w.requests_by_player.keys()))
    return ()


def fallback_binding(game: GameState, player_id: str) -> Binding:
    if game.phase != GamePhase.WAITING_TURN_ACTION:
        return Binding(action(ActionType.PASS))
    p = game.get_player(player_id)
    p.hand_tile_records()
    tid = game.drawn_tile_id if game.drawn_tile_id in p.hand_tile_ids else p.hand_tile_ids[-1]
    return Binding(action(ActionType.DISCARD, p.tile_value(tid)), tile_id=tid)


def enumerate_bindings(game: GameState, player_id: str) -> tuple[Binding, ...]:
    p = game.get_player(player_id)
    p.hand_tile_records()
    result = {}
    def add(binding):
        result.setdefault(binding.action.action_id, binding)

    if game.phase == GamePhase.WAITING_ACTION_DETAIL:
        detail = game.pending_action_detail
        add(Binding(action(ActionType.PASS)))
        if detail.selection_mode == "listed":
            for payload in detail.payload_options:
                add(Binding(action(ActionType.CHI, detail.claimed_tile, payload["required_values"]),
                            option_id=payload["option_id"]))
        else:
            # Enumeration lives here; actual arithmetic validity stays in the engine helper.
            ids = sorted(p.hand_tile_ids, key=lambda tid: TILE_INDEX[p.tile_value(tid)])
            seen = set()
            for selected in combinations(ids, 3):
                values = tuple(p.tile_value(tid) for tid in selected)
                if values in seen:
                    continue
                seen.add(values)
                if is_valid_chi_selection(list(values), detail.claimed_tile):
                    build_chi_detail_payload(list(values), detail.claimed_tile)
                    add(Binding(action(ActionType.CHI, detail.claimed_tile, values), selected_tile_ids=selected))
    else:
        if game.phase == GamePhase.WAITING_TURN_ACTION:
            options = game.turn_options_by_player.get(player_id, [])
        elif game.phase == GamePhase.WAITING_RESPONSE:
            options = game.response_window.options_by_player[player_id]
        else:
            options = game.rob_kong_window.options_by_player[player_id]
        for option in options:
            kind = option.action_type
            if game.phase == GamePhase.WAITING_TURN_ACTION:
                if kind == ActionType.PASS:
                    continue
                if kind == ActionType.HU and game.turn_hu_prompt_passed:
                    continue
                if kind in (ActionType.AN_GANG, ActionType.JIA_GANG) and game.turn_gang_prompt_passed:
                    continue
                if kind == ActionType.DISCARD:
                    for value in sorted(set(p.hand_tiles), key=TILE_INDEX.get):
                        tid = next(t for t, v in zip(p.hand_tile_ids, p.hand_tiles) if v == value)
                        if game.drawn_tile_id in p.hand_tile_ids and p.tile_value(game.drawn_tile_id) == value:
                            tid = game.drawn_tile_id
                        add(Binding(action(kind, value), tile_id=tid))
                    continue
                if kind in (ActionType.AN_GANG, ActionType.JIA_GANG):
                    for payload in option.payload_options:
                        add(Binding(action(kind, payload["tile"], payload["required_values"], payload.get("meld_index")),
                                    option_id=payload["option_id"]))
                    continue
            add(Binding(action(kind)))
    return tuple(sorted(result.values(), key=lambda b: (
        b.action.action_type.value, TILE_INDEX.get(b.action.tile, -1),
        tuple(TILE_INDEX[v] for v in b.action.required_values),
        b.action.meld_index if b.action.meld_index is not None else -1,
    )))
