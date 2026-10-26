"""Trusted, serialized adapter. No human command is routed through this class."""

from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from simulator.actions import ActionType
from simulator.game_state import GamePhase, GameState
from bots.common_bot_helper.decision import DecisionRequest, eligible_players, enumerate_bindings, fallback_binding, window_key
from bots.common_bot_helper.observation import DecisionContext, VisibleHistory, observe


class Control(str, Enum):
    HUMAN = "HUMAN"
    BOT = "BOT"


class DecisionError(ValueError):
    pass


@dataclass
class _Pending:
    request: DecisionRequest
    round_id: str
    window: tuple
    epoch: int
    fingerprint: tuple
    bindings: dict
    fallback: object


def _fingerprint(game, player_id):
    p = game.get_player(player_id)
    return (tuple(sorted(zip(p.hand_tile_ids, p.hand_tiles))),
            tuple((m["group_type"], tuple(m["tiles"])) for m in p.melded_groups),
            game.drawn_tile_id if game.current_player().player_id == player_id else None,
            game.turn_gang_prompt_passed if game.phase == GamePhase.WAITING_TURN_ACTION else None,
            game.turn_hu_prompt_passed if game.phase == GamePhase.WAITING_TURN_ACTION else None)


class DecisionDriver:
    def __init__(self, game: GameState, controls=None, history=None):
        self.game = game
        self.controls = {p.player_id: Control.HUMAN for p in game.players}
        self.epochs = {p.player_id: 0 for p in game.players}
        self.pending: dict[str, _Pending] = {}
        self.history = history or VisibleHistory(game)
        for pid, control in (controls or {}).items():
            self.set_control(pid, control)

    def set_control(self, player_id, control):
        if player_id not in self.controls:
            raise DecisionError("Unknown seat")
        self.controls[player_id] = Control(control)
        self.epochs[player_id] += 1
        self.invalidate(player_id)

    def invalidate(self, player_id=None):
        for key, p in list(self.pending.items()):
            if player_id is None or p.request.player_id == player_id:
                del self.pending[key]

    def valid(self, decision_id):
        p = self.pending.get(decision_id)
        if p is None:
            return False
        pid = p.request.player_id
        return (self.controls[pid] == Control.BOT and self.epochs[pid] == p.epoch
                and self.game.round_id == p.round_id and window_key(self.game) == p.window
                and pid in eligible_players(self.game) and _fingerprint(self.game, pid) == p.fingerprint)

    def prepare(self, player_id, *, now=0.0, context=None):
        if self.controls.get(player_id) != Control.BOT:
            raise DecisionError("Driver may only operate a BOT seat")
        if player_id not in eligible_players(self.game):
            raise DecisionError("Seat has no pending decision")
        if self.game.active_timer and self.game.active_timer.is_expired(now):
            raise DecisionError("Window has expired; advance engine timers first")
        for p in self.pending.values():
            if p.request.player_id == player_id and self.valid(p.request.decision_id):
                return p.request
        # Prepare fallback before potentially larger chi enumeration.
        fallback = fallback_binding(self.game, player_id)
        bindings = enumerate_bindings(self.game, player_id)
        if not bindings:
            raise DecisionError("No legal candidates")
        request = DecisionRequest(str(uuid4()), player_id,
                                  observe(self.game, player_id, self.history, context or DecisionContext()),
                                  tuple(b.action for b in bindings))
        self.pending[request.decision_id] = _Pending(
            request, self.game.round_id, window_key(self.game), self.epochs[player_id],
            _fingerprint(self.game, player_id), {b.action.action_id: b for b in bindings}, fallback,
        )
        return request

    def submit(self, decision_id, action_id=None, *, now=0.0, fallback=False):
        # The adapter must not use an old timestamp to bypass a real deadline.
        if not self.valid(decision_id):
            raise DecisionError("Stale decision or control ownership")
        if self.game.active_timer and self.game.active_timer.is_expired(now):
            self.game.expire_timers(now)
            self.history.sync(self.game)
            self.invalidate()
            raise DecisionError("Game deadline expired")
        pending = self.pending[decision_id]
        binding = pending.fallback if fallback else pending.bindings.get(action_id)
        if binding is None:
            raise DecisionError("Action is not a candidate of this decision")
        game, pid, a = self.game, pending.request.player_id, binding.action
        if game.phase == GamePhase.WAITING_TURN_ACTION:
            if a.action_type == ActionType.DISCARD:
                options = game.turn_options_by_player.get(pid, [])
                if any((o.action_type == ActionType.HU and not game.turn_hu_prompt_passed)
                       or (o.action_type in (ActionType.AN_GANG, ActionType.JIA_GANG) and not game.turn_gang_prompt_passed)
                       for o in options):
                    game.submit_turn_action(pid, ActionType.PASS, now=now, source_turn_window_id=pending.window[1])
                game.discard_tile_by_id(pid, binding.tile_id, now=now, source_turn_window_id=pending.window[1])
            else:
                game.submit_turn_action(pid, a.action_type, option_id=binding.option_id,
                                        now=now, source_turn_window_id=pending.window[1])
        elif game.phase == GamePhase.WAITING_RESPONSE:
            game.submit_response(pid, a.action_type, now=now, source_window_id=pending.window[1])
        elif game.phase == GamePhase.WAITING_ROB_KONG:
            game.submit_rob_kong_response(pid, a.action_type, now=now, source_window_id=pending.window[1])
        elif a.action_type == ActionType.PASS:
            game.pass_action_detail(pid)
        else:
            success = game.submit_chi_detail(pid, option_id=binding.option_id,
                                            selected_tile_ids=list(binding.selected_tile_ids) or None,
                                            now=now, detail_id=pending.window[1])
            if not success:
                raise DecisionError("Chi binding failed engine validation")
        del self.pending[decision_id]
        self.history.action(game, pid, a)
        self.history.sync(game)
        return a
