"""One human and three bots, with no rooms, accounts or connection lifecycle."""

from collections import Counter, OrderedDict
from dataclasses import dataclass
from enum import Enum
import json
import math
import secrets
from threading import RLock
import time
from uuid import uuid4

from bots.common_bot_helper.decision import eligible_players, window_key
from bots.common_bot_helper.decision_driver import Control, DecisionDriver, DecisionError
from bots.common_bot_helper.observation import DecisionContext, VisibleHistory
from simulator import (ActionType, BloodBattleScoringAdapter, GameConfig, GameMode,
                       GamePhase, GameState, NormalScoringAdapter, PlayerState, TileWall)
from simulator.game_state import GameStateError
from singleplayer.bot_catalog import GROUPS, PROFILES, profile_states
from singleplayer.round_review import RecordingRandom, RoundWallRecord
from singleplayer.replay import RoundReplay, ReplayLibrary, claimed_tiles
from simulator.state_snapshot import shift_game_clock
from singleplayer.settings import (DEFAULT_HAND_PREPARATION_SECONDS, DEFAULT_TIMEOUTS,
                                   parse_hand_preparation_seconds, parse_timeouts)


class PlayError(ValueError):
    """Invalid single-player operation, safe to show in the UI."""


def _check(condition, message):
    if not condition:
        raise PlayError(message)


class TablePhase(str, Enum):
    SETUP = "SETUP"
    PLAYING = "PLAYING"
    REVIEWING = "REVIEWING"
    ARRANGING = "ARRANGING"


@dataclass
class BotTask:
    driver: DecisionDriver
    request: object
    player_id: str
    deadline: float


class SinglePlayer:
    def __init__(self, worker, *, compute_ms=500.0, think_seconds=0.4):
        _check(type(compute_ms) in (int, float) and math.isfinite(compute_ms)
               and 10 <= compute_ms <= 1000, "Bot 计算预算须为 10 到 1000 ms")
        self.lock = RLock()
        self.instance_id = str(uuid4())
        self.worker, self.compute_ms = worker, compute_ms
        self.think_seconds = think_seconds
        self.phase = TablePhase.SETUP
        self.game = GameState([PlayerState(f"p{i}", i, "我" if i == 0 else f"Bot {i}") for i in range(4)],
                              TileWall([]))
        models = self._models()
        prefix = "rl-" if profile_states(models)["rl-fast"]["available"] else ""
        self.profiles = {f"p{i}": prefix + style for i, style in enumerate(("fast", "big", "defensive"), 1)}
        self.seeds = {pid: secrets.randbits(32) for pid in self.profiles}
        self.round_number = 0
        self.settings_revision = 1
        self.timeouts = dict(vars(DEFAULT_TIMEOUTS))
        self.hand_preparation_seconds = DEFAULT_HAND_PREPARATION_SECONDS
        self.mode = GameMode.NORMAL
        self.allow_multiple_hu = True
        self.record = None
        self.replays, self.replay, self.replay_id = ReplayLibrary(), None, None
        self.experience, self.paused_at = "normal", None
        self.driver = None
        self.task = None
        self.gate = (None, 0.0)
        self.counts = Counter()
        self.hand_revision = 0
        self.hand_signature = ""
        self.sequence = 0
        self.signature = ""
        self.seen_commands = OrderedDict()

    @staticmethod
    def _now(now):
        return time.monotonic() if now is None else now

    def _models(self):
        return self.worker.info["models"]

    def _check_profile(self, profile):
        _check(type(profile) is str and profile in PROFILES, "未知 Bot 策略")
        _check(profile_states(self._models())[profile]["available"], "所选 Bot 缺少冻结模型，请换策略或配置模型")

    def _advance(self, now, action=None):
        game = self.game
        if self.replay:
            self.replay.capture(game, now, action)
        if self.driver:
            self.driver.history.sync(game)
        if game.phase == GamePhase.WAITING_DRAW:
            try:
                game.draw_tile(game.current_player().player_id, now=now)
            except GameStateError:
                if game.round_result is None:
                    raise
        if self.driver:
            self.driver.history.sync(game)
        if self.record:
            self.record.sync(game)
        if self.replay:
            self.replay.capture(game, now)
        if game.phase == GamePhase.ROUND_ENDED and game.round_result is not None and self.phase == TablePhase.PLAYING:
            self.phase = TablePhase.REVIEWING
            self.settings_revision += 1
        signature = json.dumps(game.get_player("p0").to_public_dict(reveal_hand=True), ensure_ascii=False, sort_keys=True)
        if signature != self.hand_signature:
            self.hand_signature = signature
            self.hand_revision += 1

    def _finish(self, task, result, now):
        if self.driver is not task.driver or not task.driver.valid(task.request.decision_id):
            task.driver.invalidate(task.request.player_id)
            return
        action_id = result.get("action_id")
        fallback = bool(result.get("error")) or action_id not in {a.action_id for a in task.request.legal_actions}
        try:
            action = task.driver.submit(task.request.decision_id, action_id, now=now, fallback=fallback)
        except DecisionError:
            task.driver.invalidate(task.request.player_id)
            return
        self.counts["default" if fallback else result.get("status", "worker_unavailable")] += 1
        self._advance(now, {"player_id": task.player_id, "label": action.action_type.value})

    def tick(self, *, now=None):
        with self.lock:
            timestamp = self._now(now)
            if self.phase != TablePhase.PLAYING or self.paused_at is not None:
                return
            self.game.expire_timers(timestamp)
            self._advance(timestamp)
            if self.task is not None:
                task = self.task
                result = self.worker.poll()
                completed = result.get("completed_at") if result is not None else None
                on_time = result is not None and (
                    (completed is None and timestamp < task.deadline)
                    or (type(completed) in (float, int) and math.isfinite(completed) and completed <= task.deadline))
                if on_time:
                    self.task = None
                    if result.get("task_id") != task.request.decision_id:
                        result = {"error": True, "status": "invalid_result"}
                    self._finish(task, result, timestamp)
                elif timestamp >= task.deadline or result is not None:
                    self.worker.retire()
                    self.task = None
                    self._finish(task, {"error": True, "status": "compute_timeout"}, timestamp)
                if self.task is not None:
                    return
            if self.phase != TablePhase.PLAYING:
                return
            eligible = [pid for pid in eligible_players(self.game) if self.driver.controls[pid] == Control.BOT]
            if not eligible:
                return
            key = (self.game.round_id, window_key(self.game))
            if self.gate[0] != key:
                self.gate = (key, timestamp + self.think_seconds)
            if timestamp < self.gate[1]:
                return
            pid = eligible[0]
            timer = self.game.active_timer
            remaining = timer.remaining_seconds(timestamp) if timer else None
            budget = min(self.compute_ms / 1000, max(0.0, remaining - 0.03)) if remaining is not None else self.compute_ms / 1000
            if budget <= 0:
                return
            deadline = timestamp + budget
            try:
                request = self.driver.prepare(pid, now=timestamp, context=DecisionContext(
                    budget * 1000, deadline, remaining * 1000 if remaining is not None else None))
            except DecisionError:
                return
            task = BotTask(self.driver, request, pid, deadline)
            if len(request.legal_actions) == 1:
                self._finish(task, {"action_id": request.legal_actions[0].action_id, "status": "forced"}, timestamp)
            elif self.worker.submit(self.game.round_id, pid, self.profiles[pid], self.seeds[pid], request, deadline,
                                    seat=self.game.get_player(pid).seat):
                self.task = task
            else:
                self._finish(task, {"error": True, "status": "worker_unavailable"}, timestamp)

    def dispatch(self, command, *, now=None):
        with self.lock:
            timestamp = self._now(now)
            self.tick(now=timestamp)
            _check(command.get("instance_id") == self.instance_id, "单机程序已重启，请重新操作")
            cid = command.get("command_id")
            _check(type(cid) is str and 0 < len(cid) <= 100, "缺少操作编号")
            fingerprint = json.dumps(command, ensure_ascii=False, sort_keys=True)
            if cid in self.seen_commands:
                _check(self.seen_commands[cid] == fingerprint, "重复编号内容不同")
                return self.view(now=timestamp, teaching=command.get("view") == "teaching")
            kind = command.get("type")
            if kind in {"set_bot", "start"}:
                _check(self.phase in {TablePhase.SETUP, TablePhase.ARRANGING}, "只能在开局前或安排下一局时修改配置")
                _check(command.get("settings_revision") == self.settings_revision, "设置已变化，请重新操作")
                if kind == "set_bot":
                    pid = command.get("player_id")
                    _check(type(pid) is str and pid in self.profiles, "请选择 Bot 对手")
                    self._check_profile(command.get("profile"))
                    self.profiles[pid] = command["profile"]
                    self.settings_revision += 1
                else:
                    settings = parse_timeouts(command.get("timeouts"))
                    preparation = parse_hand_preparation_seconds(
                        command.get("hand_preparation_seconds", self.hand_preparation_seconds))
                    multiple = command.get("allow_multiple_hu")
                    _check(type(multiple) is bool, "请指定是否允许一炮多响")
                    mode = command.get("mode", self.mode.value)
                    _check(type(mode) is str and mode in {m.value for m in GameMode}, "未知牌局模式")
                    experience = command.get("experience", "normal")
                    _check(type(experience) is str and experience in {"normal", "teaching"}, "未知游玩模式")
                    _check(command.get("round_id") == self.game.round_id, "牌局已变化，请重新操作")
                    for profile in self.profiles.values():
                        self._check_profile(profile)
                    if not self.worker.ready:
                        self.worker.restart()
                        timestamp = self._now(now)
                    # Discard any completed result from the previous round.
                    if self.task is not None:
                        self.worker.retire()
                        self.worker.restart()
                        self.task = None
                        timestamp = self._now(now)
                    rules = GameConfig(allow_multiple_hu=multiple, mode=GameMode(mode),
                                       hand_preparation_seconds=preparation)
                    names = ["我", *[f"Bot {i} · {PROFILES[self.profiles[f'p{i}']]}" for i in range(1, 4)]]
                    rng = RecordingRandom()
                    if self.round_number:
                        self.game.start_next_round(source_round_id=self.game.round_id, rng=rng,
                                                   config=rules, timeout_config=settings, now=timestamp)
                        self.game.scoring_adapter = BloodBattleScoringAdapter() if rules.mode == GameMode.BLOOD_BATTLE else NormalScoringAdapter()
                        for player, name in zip(self.game.players, names):
                            player.name = name
                    else:
                        self.game = GameState.new_game(player_names=names, timeout_config=settings,
                                                       config=rules, rng=rng, now=timestamp)
                    controls = {p.player_id: Control.HUMAN if p.player_id == "p0" else Control.BOT for p in self.game.players}
                    for p in self.game.players:
                        p.is_human = controls[p.player_id] == Control.HUMAN
                    self.driver = DecisionDriver(self.game, controls, VisibleHistory(self.game))
                    self.round_number += 1
                    self.record = RoundWallRecord.begin(self.game, self.round_number, rng)
                    self.experience, self.paused_at = experience, None
                    self.replay = RoundReplay(self.game, self.round_number, experience, self.profiles, timestamp)
                    self.replay_id = self.replays.add(self.replay.data, self.game.round_id)
                    self.timeouts, self.mode, self.allow_multiple_hu = dict(vars(settings)), rules.mode, multiple
                    self.hand_preparation_seconds = preparation
                    self.phase = TablePhase.PLAYING
                    self.settings_revision += 1
                    self._advance(timestamp)
            elif kind == "prepare_next_round":
                _check(command.get("round_id") == self.game.round_id, "该操作属于旧牌局")
                _check(self.phase == TablePhase.REVIEWING, "当前不在赛后查看阶段")
                self.phase = TablePhase.ARRANGING
                self.settings_revision += 1
            elif kind in {"pause", "resume"}:
                _check(self.phase == TablePhase.PLAYING, "当前没有进行中的牌局")
                _check(command.get("round_id") == self.game.round_id, "该操作属于旧牌局")
                if kind == "pause" and self.paused_at is None:
                    self.paused_at = timestamp
                elif kind == "resume" and self.paused_at is not None:
                    offset = max(0.0, timestamp - self.paused_at)
                    shift_game_clock(self.game, offset)
                    if self.task:
                        self.task.deadline += offset
                    self.gate = (self.gate[0], self.gate[1] + offset)
                    if self.replay:
                        self.replay.origin += offset
                    self.paused_at = None
            else:
                _check(self.phase == TablePhase.PLAYING, "请先开始本局")
                _check(command.get("round_id") == self.game.round_id, "该操作属于旧牌局")
                _check(self.paused_at is None or kind == "move", "牌局已暂停，请先继续")
                if kind in {"discard", "move", "chi"}:
                    _check(command.get("hand_revision") == self.hand_revision, "手牌已变化，请重新选择")
                self._game_command(kind, command, timestamp)
                self._advance(timestamp, None if kind == "move" else {"player_id": "p0", "label": command.get("action", kind)})
            self.seen_commands[cid] = fingerprint
            while len(self.seen_commands) > 2048:
                self.seen_commands.popitem(last=False)
            return self.view(now=timestamp, teaching=command.get("view") == "teaching")

    def _game_command(self, kind, command, now):
        game, pid = self.game, "p0"
        turn = command.get("turn_window_id")
        if kind in {"discard", "turn_action"}:
            _check(type(turn) is str and turn == game.turn_window_id, "出牌窗口已变化")
        if kind == "discard":
            game.discard_tile_by_id(pid, command["tile_id"], now=now, source_turn_window_id=turn)
        elif kind == "turn_action":
            game.submit_turn_action(pid, ActionType(command["action"]), option_id=command.get("option_id"), now=now, source_turn_window_id=turn)
        elif kind == "response":
            _check(game.response_window is not None and command.get("window_id") == game.response_window.window_id, "响应窗口已变化")
            game.submit_response(pid, ActionType(command["action"]), now=now, source_window_id=command["window_id"])
        elif kind == "rob_response":
            _check(game.rob_kong_window is not None and command.get("window_id") == game.rob_kong_window.window_id, "抢杠窗口已变化")
            game.submit_rob_kong_response(pid, ActionType(command["action"]), now=now, source_window_id=command["window_id"])
        elif kind == "chi":
            _check(game.pending_action_detail is not None and command.get("detail_id") == game.pending_action_detail.detail_id, "吃牌选择窗口已变化")
            game.submit_chi_detail(pid, option_id=command.get("option_id"), selected_tile_ids=command.get("tile_ids"), now=now, detail_id=command["detail_id"])
        elif kind == "move":
            game.move_hand_tile_by_id(pid, command["tile_id"], command["index"])
        else:
            raise PlayError("单机牌桌不支持该操作")

    def wall_record(self, *, round_id):
        with self.lock:
            _check(round_id == self.game.round_id, "只能查看当前结算局的牌山")
            _check(self.phase in {TablePhase.REVIEWING, TablePhase.ARRANGING}
                   and self.game.phase == GamePhase.ROUND_ENDED and self.game.round_result is not None,
                   "仅在结算后开放牌山")
            _check(self.record is not None and self.record.valid, "本局无完整牌序记录")
            return self.record.to_dict()

    def view(self, *, now=None, teaching=False):
        with self.lock:
            timestamp = self.paused_at if self.paused_at is not None else self._now(now)
            game = self.game
            result = game.to_public_dict(viewer_player_id="p0", reveal_hands=teaching)
            result["resolved_actions"] = []
            visible = {"round_started", "initial_hands_dealt", "tile_drawn", "tile_discarded", "round_ended", "chi_applied", "peng_applied", "ming_gang_applied", "an_gang_applied", "jia_gang_declared", "win_settled"}
            result["event_log"] = [
                {"sequence": index, **{k: v for k, v in event.items() if k in {"type", "player_id", "seat", "tile", "reason"}}}
                for index, event in enumerate(e for e in game.event_log if e["type"] in visible and "_private_to_player_id" not in e)]
            if result["pending_action_detail"] and result["pending_action_detail"]["player_id"] != "p0":
                result["pending_action_detail"] = {k: v for k, v in result["pending_action_detail"].items()
                                                  if k in {"player_id", "action_type", "source_player_id", "claimed_tile", "detail_id"}}
            started = self.round_number > 0
            stage = game.phase.value
            if not started:
                stage = "LOBBY"
            elif game.phase == GamePhase.ROUND_ENDED:
                stage = "ROUND_ENDED"
            elif game.get_player("p0").is_out:
                stage = "WON"
            elif game.phase == GamePhase.WAITING_TURN_ACTION:
                stage = "OWN_TURN" if game.current_player().player_id == "p0" else "BOT_TURN"
            elif game.phase == GamePhase.WAITING_ACTION_DETAIL and game.pending_action_detail.player_id != "p0":
                stage = "WAITING_OTHER"
            models = self._models()
            result.update(
                instance_id=self.instance_id, viewer_player_id="p0", viewer_seat=0,
                hand_revision=self.hand_revision, server_now=timestamp,
                table={"phase": self.phase.value, "revision": self.settings_revision,
                       "allow_multiple_hu": self.allow_multiple_hu, "mode": self.mode.value,
                       "hand_preparation_seconds": self.hand_preparation_seconds,
                       "experience": self.experience, "paused": self.paused_at is not None,
                       "wall_review_available": self.phase in {TablePhase.REVIEWING, TablePhase.ARRANGING}
                           and self.record is not None and self.record.valid},
                bot_play={"enabled": True, "profiles": PROFILES, "profile_groups": GROUPS,
                          "profile_states": profile_states(models), "models": models,
                          "worker_ready": self.worker.ready, "compute_ms": self.compute_ms,
                          "opponents": [{"player_id": pid, "name": f"Bot {i} · {PROFILES[profile]}", "profile": profile}
                                        for i, (pid, profile) in enumerate(self.profiles.items(), 1)],
                          "decisions": dict(self.counts) if self.phase in {TablePhase.REVIEWING, TablePhase.ARRANGING} else {}},
                ui={"stage": stage, "mode": "four", "started": started, "round_number": self.round_number,
                    "settings_locked": self.phase not in {TablePhase.SETUP, TablePhase.ARRANGING},
                    "timeout_settings": self.timeouts, "hand_ordering_allowed": started and game.can_organize_hand("p0"),
                    "editing_allowed": False, "scene": "", "scenes": {}, "phase_history": [],
                    "drawn_tile_id": game.drawn_tile_id if game.current_player().player_id == "p0" else None})
            if teaching:
                result["study"] = {"drawn_tile_id": game.drawn_tile_id,
                                   "self_draw_allowed": not game.turn_hu_prompt_passed,
                                   "claimed_tiles": claimed_tiles(game),
                                   "min_fan": getattr(game.rule_adapter, "min_fan", 1)}
            stable = {k: v for k, v in result.items() if k != "server_now"}
            signature = json.dumps(stable, ensure_ascii=False, sort_keys=True)
            if signature != self.signature:
                self.sequence += 1
                self.signature = signature
            return {**json.loads(signature), "server_now": timestamp, "revision": self.sequence}

    def close(self):
        self.worker.close()
