"""Pre-game settings and a real four-seat developer game with pass-only bots."""

from dataclasses import asdict
import math
import time
from typing import Any

from simulator import ActionType, GamePhase, GameState, PlayerState, TileWall, TimeoutConfig
from simulator.game_state import GameStateError
from UI.practice import PLAYER, PracticeSession, RevisionConflict, SCENES


DEFAULT_TIMEOUTS = TimeoutConfig(5, 5, 30, 5)
BOT_TURN_SECONDS = 1.0


def parse_timeouts(values: Any) -> TimeoutConfig:
    """Validate all four settings before constructing an immutable round config."""
    fields = asdict(DEFAULT_TIMEOUTS)
    if not isinstance(values, dict) or set(values) != set(fields):
        raise GameStateError("请提供完整的 t1、t2、t3、t4 配置")
    for value in values.values():
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600
        ):
            raise GameStateError("计时必须关闭，或设置为 1 到 600 秒")
    return TimeoutConfig(**values)


class DeveloperSession(PracticeSession):
    def __init__(self):
        self.started = False
        self.mode = "solo"
        self.settings = DEFAULT_TIMEOUTS
        self.bot_window = None
        self.bot_due_at = None
        self.phase_history = []
        self._phase_key = None
        super().__init__()
        self.mode = "four"
        self._prepare()

    def _prepare(self):
        """Abandon the old game explicitly; only this lobby permits settings edits."""
        self.started = False
        self.round_number = 0
        self.bot_window = self.bot_due_at = None
        self.phase_history = []
        self._phase_key = None
        self.game = GameState(
            players=[PlayerState(f"p{i}", i, ["我", "下家", "对家", "上家"][i], is_human=i == 0) for i in range(4)],
            wall=TileWall.from_rule_tiles(),
        )

    def _capture_phase(self):
        """Trace window transitions, including a t4 that bots immediately pass."""
        game = self.game
        phase = {
            GamePhase.WAITING_RESPONSE: ("t1", game.response_window.window_id if game.response_window else None),
            GamePhase.WAITING_ACTION_DETAIL: ("t2", game.pending_action_detail.detail_id if game.pending_action_detail else None),
            GamePhase.WAITING_TURN_ACTION: ("t3", game.turn_window_id),
            GamePhase.WAITING_ROB_KONG: ("t4", game.rob_kong_window.window_id if game.rob_kong_window else None),
        }.get(game.phase)
        if phase is None:
            self._phase_key = None
            return
        if phase != self._phase_key:
            actor = game.pending_action_detail.player_id if phase[0] == "t2" else game.current_player().player_id
            entry = {"number": len(self.phase_history) + 1, "phase": phase[0], "player_id": actor}
            if phase[0] == "t4":
                entry["_visible_to"] = list(game.rob_kong_window.options_by_player)
            self.phase_history.append(entry)
            self._phase_key = phase

    def _normalize(self, now):
        if not self.started:
            return
        self._capture_phase()
        if self.mode == "solo":
            super()._normalize(now)
            self._capture_phase()
            return
        # Stop at a human decision or scheduled bot discard, never sleep under the lock.
        for _ in range(12):
            game = self.game
            window = game.response_window
            if window is not None:
                for player_id in sorted(window.players()):
                    if game.response_window is not window:
                        break
                    if player_id != PLAYER and player_id not in window.auto_passed_players and player_id not in window.requests_by_player:
                        game.submit_response(player_id, ActionType.PASS, now=now)
                        self._capture_phase()
            rob = game.rob_kong_window
            if rob is not None:
                for player_id in sorted(rob.options_by_player):
                    if game.rob_kong_window is not rob:
                        break
                    if player_id != PLAYER and player_id not in rob.requests_by_player:
                        game.submit_rob_kong_response(player_id, ActionType.PASS, now=now)
                        self._capture_phase()
            if game.phase == GamePhase.WAITING_DRAW:
                try:
                    game.draw_tile(game.current_player().player_id, now=now)
                except GameStateError:
                    if game.round_result is None:
                        raise
                self._capture_phase()
                continue
            if game.phase != GamePhase.WAITING_TURN_ACTION or game.current_player().player_id == PLAYER:
                break
            if self.bot_window != game.turn_window_id:
                self.bot_window = game.turn_window_id
                self.bot_due_at = now + BOT_TURN_SECONDS
            if now < self.bot_due_at:
                break
            player = game.current_player()
            options = game.turn_options_by_player.get(player.player_id, [])
            if any(o.action_type in {ActionType.HU, ActionType.AN_GANG, ActionType.JIA_GANG} for o in options):
                game.submit_turn_action(player.player_id, ActionType.PASS, now=now)
            game.discard_tile_by_id(player.player_id, player.hand_tile_ids[-1], now=now)
            self._capture_phase()
        self._capture_phase()

    def tick(self, *, now=None):
        if not self.started:
            return
        timestamp = time.monotonic() if now is None else now
        before = (self.game.phase, len(self.game.event_log), len(self.phase_history))
        self.game.expire_timers(timestamp)
        self._normalize(timestamp)
        if before != (self.game.phase, len(self.game.event_log), len(self.phase_history)):
            self.revision += 1

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        state = super().snapshot(now=now)
        practice = state["practice"]
        visible_history = [entry for entry in self.phase_history if "_visible_to" not in entry or PLAYER in entry["_visible_to"]]
        practice.update(
            mode=self.mode, started=self.started, settings_locked=self.started,
            timeout_settings=asdict(self.settings), default_timeout_settings=asdict(DEFAULT_TIMEOUTS),
            phase_history=[
                {"number": index + 1, "phase": entry["phase"], "player_id": entry["player_id"]}
                for index, entry in enumerate(visible_history)
            ][-20:],
            scenes={key: label for key, label in SCENES.items() if key != "timer"},
        )
        if not self.started:
            practice.update(stage="LOBBY", editing_allowed=False, grouping_allowed=False)
        elif self.mode == "four" and self.game.phase == GamePhase.WAITING_TURN_ACTION and self.game.current_turn_seat != 0:
            practice["stage"] = "BOT_TURN"
        return state

    def dispatch(self, command: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self.tick(now=timestamp)
        if command.get("revision") != self.revision:
            raise RevisionConflict("牌局已更新，请重新操作")
        kind = command.get("type")
        if kind == "start":
            if self.started:
                raise GameStateError("开局后不能修改计时或重新开局，请先返回开局设置")
            mode = command.get("mode", "four")
            if mode not in {"four", "solo"}:
                raise GameStateError("未知的游戏模式")
            settings = parse_timeouts(command.get("timeouts"))
            scene = command.get("scene", "basic")
            if scene not in SCENES or scene == "timer":
                raise GameStateError("未知的试牌场景")
            if mode == "four":
                game = GameState.new_game(player_names=["我", "下家", "对家", "上家"], timeout_config=settings)
                self.game = game
                self.round_number = 1
            else:
                self.new_scene(scene, now=timestamp)
                self.game.timeout_config = settings
                # A preset may already have drawn a tile under its original config.
                if self.game.current_turn_seat == 0:
                    self.game._open_turn_action_window(self.game.current_player(), now=timestamp, drawn_tile_id=self.game.drawn_tile_id)
            self.mode, self.settings, self.started = mode, settings, True
            self.phase_history = []
            self._phase_key = None
            self._normalize(timestamp)
        elif kind == "reset":
            if command.get("round_id") != self.game.round_id:
                raise GameStateError("牌局已更新，不能重置旧牌局")
            self._prepare()
        else:
            if not self.started:
                raise GameStateError("请先确认计时配置并开始游戏")
            if "timeouts" in command or kind in {"scene", "configure"}:
                raise GameStateError("本局计时和模式已锁定")
            if kind == "next_round" and self.mode == "four":
                self.game.start_next_round(source_round_id=command.get("round_id"))
                self.round_number += 1
                self.phase_history = []
                self._phase_key = None
                self._normalize(timestamp)
            elif kind == "rob_response":
                self.game.submit_rob_kong_response(PLAYER, ActionType(command["action"]), now=timestamp, source_window_id=command.get("window_id"))
                self._normalize(timestamp)
            else:
                if kind == "reveal" and self.mode == "four":
                    raise GameStateError("四人局按真实席位自动摸牌，不使用单人翻牌")
                return super().dispatch(command, now=timestamp)
        self.revision += 1
        return self.snapshot(now=timestamp)
