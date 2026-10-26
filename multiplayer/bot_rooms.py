"""Live human/bot practice, reusing the untouched bot contract and human command path."""

from collections import Counter
from dataclasses import dataclass
import json
import math
import secrets
import time
from uuid import uuid4

from bots.common_bot_helper.decision import eligible_players, window_key
from bots.common_bot_helper.decision_driver import Control, DecisionDriver, DecisionError
from bots.common_bot_helper.observation import DecisionContext, VisibleHistory
from multiplayer.bot_worker import PROFILES
from multiplayer.bot_catalog import GROUPS, profile_states
from multiplayer.rooms import Member, RoomPhase, _check
from multiplayer.runtime import LiveRooms, Presence


@dataclass
class BotMember:
    room_id: str
    number: int
    profile: str
    seed: int
    last_status: str = "ready"


@dataclass
class BotTask:
    room_id: str
    driver: object
    request: object
    member_id: str
    deadline: float


class BotRooms(LiveRooms):
    """Opt-in, one-room practice service; at most one CPU inference in flight."""

    def __init__(self, worker, model=None, *, compute_ms=500.0, think_seconds=0.4, **kwargs):
        super().__init__(**kwargs)
        _check(10 <= compute_ms <= 1000, "Bot 计算预算须为 10 到 1000 ms")
        self.worker, self.model, self.compute_ms = worker, model, compute_ms
        self.think_seconds = think_seconds
        self.bots: dict[str, BotMember] = {}
        self.drivers = {}
        self.task = None
        self.gate = (None, 0.0)
        self.counts = Counter()

    def _models(self):
        return self.worker.info.get("models", {"rl": {"available": self.model is not None,
            "sha256": self.model.sha256 if self.model else None,
            "source_name": self.model.source_name if self.model else None,
            "iteration": self.worker.info.get("iteration")}})

    def _check_profile(self, profile):
        _check(type(profile) is str and profile in PROFILES, "未知 Bot 策略")
        _check(profile_states(self._models())[profile]["available"], "所选 Bot 没有可用的冻结模型，请换策略或在开局前配置模型")

    def _connected(self, member_id):
        return member_id in self.bots or super()._connected(member_id)

    def _connection_members(self, room):
        return (mid for mid in room.members if mid not in self.bots)

    def _advance(self, room, now):
        game = room.game
        driver = self.drivers.get(room.room_id)
        if driver is None or driver.game is not game or driver.history.round_id != game.round_id:
            controls = {p.player_id: Control.BOT if room.round_members[p.seat] in self.bots else Control.HUMAN for p in game.players}
            driver = DecisionDriver(game, controls, VisibleHistory(game))
            self.drivers[room.room_id] = driver
            for p in game.players:
                p.is_human = controls[p.player_id] == Control.HUMAN
        driver.history.sync(game)
        super()._advance(room, now)
        driver.history.sync(game)

    def _add_bot(self, room, profile):
        self._check_profile(profile)
        _check(len(room.members) < 10 and sum(b.room_id == room.room_id for b in self.bots.values()) < 3,
               "试玩房间最多三位 Bot，总人数最多十人")
        number = 1
        while (any(b.room_id == room.room_id and b.number == number for b in self.bots.values())
               or any(m.name.casefold() == f"Bot {number} · {PROFILES[profile]}".casefold() for m in room.members.values())):
            number += 1
        mid = str(uuid4())
        self.bots[mid] = BotMember(room.room_id, number, profile, secrets.randbits(32))
        self.presence[mid] = Presence()
        room.members[mid] = Member(mid, f"Bot {number} · {PROFILES[profile]}")
        room.revision += 1
        return mid

    def _cleanup(self):
        for mid, bot in list(self.bots.items()):
            if bot.room_id not in self.rooms or mid not in self.rooms[bot.room_id].members:
                self.bots.pop(mid)
                self.presence.pop(mid, None)
        for rid in list(self.drivers):
            if rid not in self.rooms:
                self.drivers.pop(rid)

    def _finish(self, task, result, now):
        room = self.rooms.get(task.room_id)
        if (room is None or room.recovery_pending or room.game is not task.driver.game
                or self.drivers.get(task.room_id) is not task.driver
                or not task.driver.valid(task.request.decision_id)):
            task.driver.invalidate(task.request.player_id)
            return
        action_id = result.get("action_id")
        fallback = bool(result.get("error")) or action_id not in {a.action_id for a in task.request.legal_actions}
        try:
            task.driver.submit(task.request.decision_id, action_id, now=now, fallback=fallback)
        except DecisionError:
            task.driver.invalidate(task.request.player_id)
            return
        status = result.get("status", "worker_unavailable")
        if fallback:
            status = "default"
        if task.member_id in self.bots:
            self.bots[task.member_id].last_status = status
        self.counts[status] += 1
        self._advance(room, now)

    def tick(self, *, now=None):
        with self.lock:
            timestamp = self._now(now)
            super().tick(now=timestamp)
            self._cleanup()
            if self.task is not None:
                task = self.task
                result = self.worker.poll()
                completed = result.get("completed_at") if result is not None else None
                on_time = result is not None and (
                    (completed is None and timestamp < task.deadline)
                    or (type(completed) in (float, int) and math.isfinite(completed) and completed <= task.deadline))
                # A polling delay is not a compute overrun; engine windows are
                # still rechecked at the actual commit time by DecisionDriver.
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
            for room in self.rooms.values():
                if room.phase != RoomPhase.PLAYING or room.recovery_pending:
                    continue
                driver = self.drivers[room.room_id]
                eligible = [pid for pid in eligible_players(room.game) if driver.controls[pid] == Control.BOT]
                if not eligible:
                    continue
                key = (room.game.round_id, window_key(room.game))
                if self.gate[0] != key:
                    self.gate = (key, timestamp + self.think_seconds)
                if timestamp < self.gate[1]:
                    continue
                pid = eligible[0]
                mid = room.round_members[room.game.get_player(pid).seat]
                timer = room.game.active_timer
                remaining = timer.remaining_seconds(timestamp) if timer else None
                budget = min(self.compute_ms / 1000, max(0.0, remaining - 0.03)) if remaining is not None else self.compute_ms / 1000
                if budget <= 0:
                    return
                deadline = timestamp + budget
                try:
                    request = driver.prepare(pid, now=timestamp, context=DecisionContext(budget * 1000, deadline, remaining * 1000 if remaining is not None else None))
                except DecisionError:
                    return
                task = BotTask(room.room_id, driver, request, mid, deadline)
                if len(request.legal_actions) == 1:
                    self._finish(task, {"action_id": request.legal_actions[0].action_id, "status": "forced"}, timestamp)
                else:
                    spec = self.bots[mid]
                    if self.worker.submit(room.game.round_id, pid, spec.profile, spec.seed, request, deadline,
                                          seat=room.game.get_player(pid).seat):
                        self.task = task
                    else:
                        self._finish(task, {"error": True, "status": "worker_unavailable"}, timestamp)
                return

    def dispatch(self, token, command, *, now=None):
        timestamp = self._now(now)
        # Only validated, between-round host requests may restart a disabled worker.
        if command.get("type") == "start" and not self.worker.ready:
            with self.lock:
                client = self.client(token)
                room = self.rooms.get(client.room_id)
                _check(room is not None and client.member_id == room.host_id and room.phase in {RoomPhase.WAITING_PLAYERS, RoomPhase.ARRANGING}
                       and not room.recovery_pending and command.get("server_instance_id") == self.instance_id
                       and command.get("connection_id") in self.presence[client.member_id].connections, "只有房主可在局间准备 Bot")
            self.worker.restart()
            timestamp = self._now(now)
        with self.lock:
            self.tick(now=timestamp)
            kind = command.get("type")
            if kind in {"add_bot", "set_bot"}:
                client = self.client(token)
                room = self.rooms.get(client.room_id)
                _check(command.get("server_instance_id") == self.instance_id and command.get("connection_id") in self.presence[client.member_id].connections,
                       "连接或开服身份已变化")
                _check(room is not None and command.get("room_id") == room.room_id and room.host_id == client.member_id, "只有本房房主可以配置 Bot")
                _check(room.phase in {RoomPhase.WAITING_PLAYERS, RoomPhase.ARRANGING} and not room.recovery_pending, "只能在开局前或安排下一局时配置 Bot")
                cid = command.get("command_id")
                _check(type(cid) is str and 0 < len(cid) <= 100, "缺少操作编号")
                cache_key = (client.member_id, cid)
                fingerprint = json.dumps(command, ensure_ascii=False, sort_keys=True)
                if cache_key in room.seen_commands:
                    _check(room.seen_commands[cache_key] == fingerprint, "重复编号内容不同")
                    return self.view(token, now=timestamp)
                _check(command.get("room_revision") == room.revision, "房间已变化，请重新操作")
                profile = command.get("profile")
                self._check_profile(profile)
                if kind == "add_bot":
                    self._add_bot(room, profile)
                else:
                    mid = command.get("member_id")
                    _check(type(mid) is str and mid in self.bots and self.bots[mid].room_id == room.room_id, "Bot 已离开")
                    bot = self.bots[mid]
                    name = f"Bot {bot.number} · {PROFILES[profile]}"
                    _check(all(m.member_id == mid or m.name.casefold() != name.casefold() for m in room.members.values()), "新的 Bot 显示名与成员重名")
                    bot.profile = profile
                    room.members[mid].name = name
                    room.revision += 1
                room.seen_commands[cache_key] = fingerprint
                while len(room.seen_commands) > 2048:
                    room.seen_commands.popitem(last=False)
                return self.view(token, now=timestamp)
            if kind == "create":
                _check(not self.rooms, "低占用试玩服务一次只开一个房间")
            if kind == "start" and type(command.get("seats")) is list:
                for mid in command["seats"]:
                    if type(mid) is str and mid in self.bots:
                        self._check_profile(self.bots[mid].profile)
            super().dispatch(token, command, now=timestamp)
            client = self.client(token)
            room = self.rooms.get(client.room_id)
            if kind == "create" and room:
                use_rl = profile_states(self._models())["rl-fast"]["available"]
                mids = [self._add_bot(room, ("rl-" if use_rl else "") + p) for p in ("fast", "big", "defensive")]
                room.seats = [client.member_id, *mids]
            self._cleanup()
            return self.view(token, now=timestamp)

    def view(self, token, *, now=None):
        with self.lock:
            client = self.client(token)
            sequence, signature = client.sequence, client.signature
            packet = super().view(token, now=now)
            client.sequence, client.signature = sequence, signature
            models = self._models()
            rl = models.get("rl", {})
            packet["bot_play"] = {
                "enabled": True, "profiles": PROFILES, "worker_ready": self.worker.ready,
                "profile_groups": GROUPS, "profile_states": profile_states(models), "models": models,
                "compute_ms": self.compute_ms, "model": {"sha256": rl["sha256"],
                    "source_name": rl["source_name"], "iteration": rl.get("iteration"), "cpu_only": True} if rl.get("available") else None,
                "decisions": dict(self.counts) if packet["room"] and packet["room"]["phase"] in {"REVIEWING", "ARRANGING"} else {},
            }
            if packet["room"]:
                for member in packet["room"]["members"]:
                    bot = self.bots.get(member["member_id"])
                    if bot:
                        status = bot.last_status if packet["room"]["phase"] in {"REVIEWING", "ARRANGING"} else "active"
                        member.update(is_bot=True, bot_profile=bot.profile, bot_status=status, connection_status="bot")
            stable = {k: v for k, v in packet.items() if k not in {"server_now", "revision"}}
            fingerprint = json.dumps(stable, ensure_ascii=False, sort_keys=True)
            if fingerprint != client.signature:
                client.signature = fingerprint
                client.sequence += 1
            packet["revision"] = client.sequence
            return packet

    def close(self):
        self.worker.close()
