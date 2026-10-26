"""Ephemeral hosting runtime: socket handoff, table pauses and disconnect fallback."""

from dataclasses import dataclass, field
import json
import math
import time
from uuid import uuid4

from multiplayer.autopilot import discard_drawn_tile, pass_response
from multiplayer.rooms import LocalRooms, RoomPhase, _check
from simulator import GamePhase
from simulator.state_snapshot import shift_game_clock


@dataclass
class Presence:
    connections: set[str] = field(default_factory=set)
    disconnected_at: float | None = None


@dataclass
class SeatControl:
    epoch: int = 1
    remaining_grace: float = 300.0
    grace_deadline: float | None = None
    autopilot: bool = False


@dataclass
class TableControl:
    round_id: str
    seats: dict[str, SeatControl]
    paused_at: float | None = None
    bot_turn: str | None = None
    bot_due: float | None = None


class LiveRooms(LocalRooms):
    """A new instance is a new opening: no database is loaded or deleted."""

    def __init__(self, *, disconnect_seconds=10.0, grace_seconds=300.0, bot_delay=1.0):
        super().__init__()
        for value in (disconnect_seconds, grace_seconds, bot_delay):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Runtime intervals must be positive finite numbers")
        self.disconnect_seconds, self.grace_seconds, self.bot_delay = disconnect_seconds, grace_seconds, bot_delay
        self.presence: dict[str, Presence] = {}
        self.tables: dict[str, TableControl] = {}
        self.outage_started_at = None
        self.outage_required = set()
        self._ticking = False

    def new_client(self, *, now=None):
        with self.lock:
            token = super().new_client()
            self.presence[self.client(token).member_id] = Presence(disconnected_at=self._now(now))
            return token

    @staticmethod
    def _now(now):
        return time.monotonic() if now is None else now

    def _connected(self, member_id):
        """Human transport state; optional practice controllers override for explicit bots."""
        return bool(self.presence[member_id].connections)

    def _connection_members(self, room):
        return room.members.keys()

    def open_connection(self, token, *, instance_id, now=None):
        """A new authenticated socket hands off a refresh without dropping the old one."""
        with self.lock:
            timestamp = self._now(now)
            self.tick(now=timestamp)
            _check(instance_id == self.instance_id, "服务器已重新开服，请刷新")
            client = self.client(token)
            presence = self.presence[client.member_id]
            _check(len(presence.connections) < 8, "同一身份最多打开八个连接")
            connection = str(uuid4())
            presence.connections.add(connection)
            presence.disconnected_at = None
            room = self.rooms.get(client.room_id)
            table = self.tables.get(client.room_id)
            if room and table:
                control = table.seats.get(client.member_id)
                if control and control.grace_deadline is not None:
                    control.remaining_grace = max(0.0, control.grace_deadline - (self.outage_started_at if self.outage_started_at is not None else timestamp))
                    control.grace_deadline = None
                    control.epoch += 1
                    room.revision += 1
                    if self.outage_started_at is None:
                        self._resume_if_ready(room, table, timestamp)
            self._resume_outage_if_ready(timestamp)
            return connection

    def close_connection(self, token, connection, *, now=None):
        with self.lock:
            client = self.client(token)
            presence = self.presence[client.member_id]
            if connection in presence.connections:
                presence.connections.remove(connection)
                if not presence.connections:
                    presence.disconnected_at = self._now(now)

    def _new_table(self, room):
        previous = self.tables.get(room.room_id)
        table = TableControl(room.game.round_id, {
            mid: SeatControl(epoch=(previous.seats[mid].epoch + 1 if previous and mid in previous.seats else 1),
                             remaining_grace=self.grace_seconds)
            for mid in room.round_members
        })
        self.tables[room.room_id] = table
        return table

    def _close_room(self, room):
        # An interrupted round is abandoned, not scored as a normal draw.
        for client in self.clients.values():
            if client.room_id == room.room_id:
                client.room_id, client.notice = None, "房主已离线，房间已关闭；未完成局不结算"
        self.rooms.pop(room.room_id, None)
        self.tables.pop(room.room_id, None)

    def _detect_outage(self, now):
        """Conservative signal, not proof: all sockets gone, multiple recent losses including a host."""
        people = {mid for room in self.rooms.values() for mid in self._connection_members(room)}
        if not people or any(self.presence[mid].connections for mid in people):
            return
        latest = max((self.presence[mid].disconnected_at for mid in people if self.presence[mid].disconnected_at is not None), default=now)
        recent = {mid for mid in people if self.presence[mid].disconnected_at is not None and latest - self.presence[mid].disconnected_at <= 2}
        if len(recent) < 2 or not any(r.host_id in recent for r in self.rooms.values()) or now < latest + self.disconnect_seconds:
            return
        self.outage_started_at = now
        for room in self.rooms.values():
            self.outage_required.add(room.host_id)
            if room.phase == RoomPhase.PLAYING:
                table = self.tables.get(room.room_id) or self._new_table(room)
                self.outage_required.update(mid for mid, c in table.seats.items() if not c.autopilot and mid in people)
                if table.paused_at is None:
                    table.paused_at = now
            room.recovery_pending = True
            room.frozen_at = self.tables[room.room_id].paused_at if room.phase == RoomPhase.PLAYING else now
            room.revision += 1

    def _resume_outage_if_ready(self, now):
        if self.outage_started_at is None:
            return
        required = {mid for mid in self.outage_required if any(mid in room.members for room in self.rooms.values())}
        if not all(self.presence[mid].connections for mid in required):
            return
        elapsed = now - self.outage_started_at
        self.outage_started_at, self.outage_required = None, set()
        for room in self.rooms.values():
            table = self.tables.get(room.room_id)
            if room.phase == RoomPhase.PLAYING and table:
                for control in table.seats.values():
                    if control.grace_deadline is not None:
                        control.grace_deadline += elapsed
                self._resume_if_ready(room, table, now)
            else:
                room.recovery_pending, room.frozen_at = False, None
            room.revision += 1

    @staticmethod
    def _resume_if_ready(room, table, now):
        if table.paused_at is not None and not any(c.grace_deadline is not None for c in table.seats.values()):
            shift_game_clock(room.game, now - table.paused_at)
            table.paused_at = None
            room.recovery_pending, room.frozen_at = False, None
            room.revision += 1
            # A bot's short visual delay must also start after the table resumes.
            table.bot_turn, table.bot_due = None, None

    def _fallback(self, room, table, now):
        game = room.game
        if game.phase == GamePhase.WAITING_TURN_ACTION and table.seats[room.round_members[game.current_turn_seat]].autopilot:
            if game.active_timer is not None and game.active_timer.is_expired(now):
                # A bot's default is the drawn entity, not the human timeout's
                # visual-rightmost policy. Commit it before ordinary expiry.
                game.active_timer = None
                discard_drawn_tile(game, game.current_player().player_id, now=now)
                self._advance(room, now)
                table.bot_turn, table.bot_due = None, None
                return
        game.expire_timers(now)
        self._advance(room, now)
        for index, mid in enumerate(room.round_members):
            if table.seats[mid].autopilot:
                pass_response(game, f"p{index}", now=now)
        self._advance(room, now)
        if game.phase != GamePhase.WAITING_TURN_ACTION:
            table.bot_turn, table.bot_due = None, None
            return
        mid = room.round_members[game.current_turn_seat]
        if not table.seats[mid].autopilot:
            table.bot_turn, table.bot_due = None, None
            return
        if table.bot_turn != game.turn_window_id:
            table.bot_turn, table.bot_due = game.turn_window_id, now + self.bot_delay
        if now >= table.bot_due:
            discard_drawn_tile(game, game.current_player().player_id, now=now)
            self._advance(room, now)
            table.bot_turn, table.bot_due = None, None

    def tick(self, *, now=None):
        """Detect missing players BEFORE advancing timers, and freeze all game clocks together."""
        with self.lock:
            if self._ticking:
                return
            self._ticking = True
            try:
                timestamp = self._now(now)
                if self.outage_started_at is None:
                    self._detect_outage(timestamp)
                if self.outage_started_at is not None:
                    self._resume_outage_if_ready(timestamp)
                    if self.outage_started_at is not None:
                        return
                for room in list(self.rooms.values()):
                    host = self.presence[room.host_id]
                    if not host.connections and host.disconnected_at is not None and timestamp >= host.disconnected_at + self.disconnect_seconds:
                        self._close_room(room)
                        continue
                    if room.phase != RoomPhase.PLAYING:
                        continue
                    table = self.tables.get(room.room_id)
                    if table is None or table.round_id != room.game.round_id:
                        table = self._new_table(room)
                    for mid, control in table.seats.items():
                        presence = self.presence[mid]
                        lost_at = None if self._connected(mid) or presence.disconnected_at is None else presence.disconnected_at + self.disconnect_seconds
                        if not control.autopilot and control.grace_deadline is None and lost_at is not None and timestamp >= lost_at:
                            if table.paused_at is None:
                                room.game.expire_timers(lost_at)
                                self._advance(room, lost_at)
                                if room.phase != RoomPhase.PLAYING:
                                    break
                                table.paused_at = lost_at
                                room.recovery_pending, room.frozen_at = True, lost_at
                            control.grace_deadline = lost_at + control.remaining_grace
                            control.epoch += 1
                            room.revision += 1
                        if control.grace_deadline is not None and timestamp >= control.grace_deadline:
                            control.grace_deadline, control.remaining_grace = None, 0.0
                            control.autopilot = True
                            control.epoch += 1
                            room.revision += 1
                    if room.phase != RoomPhase.PLAYING:
                        continue
                    self._resume_if_ready(room, table, timestamp)
                    if table.paused_at is None:
                        self._fallback(room, table, timestamp)
                        room.game.expire_timers(timestamp)
                        self._advance(room, timestamp)
            finally:
                self._ticking = False

    def dispatch(self, token, command, *, now=None):
        with self.lock:
            timestamp = self._now(now)
            self.tick(now=timestamp)
            _check(command.get("server_instance_id") == self.instance_id, "服务器已重新开服，旧操作已失效")
            client = self.client(token)
            _check(command.get("connection_id") in self.presence[client.member_id].connections, "实时连接未就绪，请稍后再试")
            room = self.rooms.get(client.room_id)
            table = self.tables.get(client.room_id)
            kind = command.get("type")
            _check(self.outage_started_at is None or (room and kind == "leave" and client.member_id == room.host_id), "服务连接恢复中，请等原成员返回")
            if room:
                if table and client.member_id in table.seats:
                    control = table.seats[client.member_id]
                    _check(command.get("control_epoch") == control.epoch, "控制权已变化，请重新操作")
                    _check(not control.autopilot or room.phase != RoomPhase.PLAYING or kind in {"leave"}, "本局已由系统托管，下一局才能恢复真人控制")
                _check(not room.recovery_pending or (kind == "leave" and client.member_id == room.host_id), "全桌暂停，正在等待玩家重连")
            if kind == "start":
                seats = command.get("seats")
                if type(seats) is list and all(type(mid) is str and mid in self.presence for mid in seats):
                    _check(all(self._connected(mid) for mid in seats), "请等四位参赛者实时连接就绪后开局")
            super().dispatch(token, command, now=timestamp)
            room = self.rooms.get(client.room_id)
            if room and kind == "start" and (room.room_id not in self.tables or self.tables[room.room_id].round_id != room.game.round_id):
                self._new_table(room)
            for rid in list(self.tables):
                if rid not in self.rooms:
                    del self.tables[rid]
            return self.view(token, now=timestamp)

    def view(self, token, *, now=None):
        with self.lock:
            timestamp = self._now(now)
            client = self.client(token)
            room = self.rooms.get(client.room_id)
            table = self.tables.get(client.room_id)
            effective = room.frozen_at if room and room.recovery_pending else timestamp
            sequence, signature = client.sequence, client.signature
            packet = super().view(token, now=effective)
            client.sequence, client.signature = sequence, signature
            packet["network"] = {"enabled": True, "ephemeral": True, "access_ready": False}
            control = table.seats.get(client.member_id) if table else None
            packet["control_epoch"] = control.epoch if control else 0
            if room:
                for member in packet["room"]["members"]:
                    mid = member["member_id"]
                    p = self.presence[mid]
                    c = table.seats.get(mid) if table else None
                    member["connection_status"] = "online" if p.connections else "reconnecting" if c and c.grace_deadline is not None else "offline"
                    member["autopilot"] = bool(c and c.autopilot and room.phase == RoomPhase.PLAYING)
                waiting = [{"member_id": mid, "deadline_at": c.grace_deadline} for mid, c in table.seats.items() if c.grace_deadline is not None] if table else []
                if self.outage_started_at is not None:
                    waiting = [{"member_id": mid, "deadline_at": None} for mid in room.members
                               if mid in self.outage_required and not self.presence[mid].connections]
                packet["room"]["connection"] = {"paused": room.recovery_pending, "waiting": waiting,
                                                  "reason": "server_outage" if self.outage_started_at is not None else "player_disconnect"}
                packet["ui"]["autopilot"] = bool(control and control.autopilot and room.phase == RoomPhase.PLAYING)
                if packet["ui"]["autopilot"]:
                    packet["ui"]["grouping_allowed"] = False
                    packet["ui"]["stage"] = "AUTOPILOT"
                if room.recovery_pending:
                    timer = packet["active_timer"]
                    remaining = max(0.0, timer["deadline_at"] - effective) if timer else packet["paused_turn_remaining_seconds"]
                    packet["ui"].update(recovery_pending=True, connection_paused=True, recovery_remaining_seconds=remaining,
                                         grouping_allowed=False, stage="CONNECTION_PAUSED")
                    packet["active_timer"] = None
                    packet["room"]["wall_review_available"] = False
            stable = {k: v for k, v in packet.items() if k not in {"server_now", "revision"}}
            new_signature = json.dumps(stable, ensure_ascii=False, sort_keys=True)
            if client.signature != new_signature:
                client.sequence += 1
                client.signature = new_signature
            packet.update(revision=client.sequence, server_now=timestamp)
            return packet
