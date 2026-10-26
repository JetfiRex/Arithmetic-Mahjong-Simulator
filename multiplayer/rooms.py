"""Password rooms and four human seats, independent of the developer bots."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import json
import secrets
import threading
import time
import unicodedata
from uuid import uuid4

from simulator import ActionType, GameConfig, GamePhase, GameState, PlayerState, TileWall
from simulator.game_state import GameStateError
from multiplayer.settings import DEFAULT_TIMEOUTS, parse_timeouts
from multiplayer.round_review import RecordingRandom, RoundWallRecord


class RoomError(ValueError):
    """An invalid local-room operation, safe to display to the caller."""


class RoomPhase(str, Enum):
    WAITING_PLAYERS = "WAITING_PLAYERS"
    PLAYING = "PLAYING"
    REVIEWING = "REVIEWING"
    ARRANGING = "ARRANGING"


def _check(condition, message):
    if not condition:
        raise RoomError(message)


def _name(value, *, maximum=32):
    _check(type(value) is str, "请填写名称")
    value = unicodedata.normalize("NFKC", value).strip()
    _check(0 < len(value) <= maximum and all(ord(char) >= 32 for char in value), f"名称须为 1 到 {maximum} 个字符")
    return value


def _password(value):
    _check(type(value) is str and 1 <= len(value) <= 128, "房间密码须为 1 到 128 个字符")
    return value


@dataclass
class LocalClient:
    member_id: str = field(default_factory=lambda: str(uuid4()))
    room_id: str | None = None
    notice: str = ""
    sequence: int = 0
    signature: str = ""


@dataclass
class Member:
    member_id: str
    name: str
    spectator_mode: str = "public"


@dataclass
class Room:
    room_id: str
    name: str
    host_id: str
    salt: bytes
    password_hash: bytes
    members: dict[str, Member] = field(default_factory=dict)
    seats: list[str | None] = field(default_factory=lambda: [None] * 4)
    round_members: list[str] = field(default_factory=list)
    revision: int = 1
    round_number: int = 0
    phase: RoomPhase = RoomPhase.WAITING_PLAYERS
    recovery_pending: bool = False
    frozen_at: float | None = None
    recovery_id: str | None = None
    game: GameState | None = None
    wall_records: dict[str, RoundWallRecord] = field(default_factory=dict)
    timeouts: dict = field(default_factory=lambda: dict(vars(DEFAULT_TIMEOUTS)))
    allow_multiple_hu: bool = True
    hand_revisions: dict[str, int] = field(default_factory=dict)
    hand_signatures: dict[str, str] = field(default_factory=dict)
    seen_commands: OrderedDict = field(default_factory=OrderedDict)


class LocalRooms:
    """Serialize local commands; no disconnect inference, reconnect or AI policy."""

    def __init__(self):
        self.lock = threading.RLock()
        self.instance_id = str(uuid4())
        self.clients: dict[str, LocalClient] = {}
        self.rooms: dict[str, Room] = {}
        self.empty_game = GameState(
            players=[PlayerState(f"p{i}", i, f"座位 {i + 1}") for i in range(4)],
            wall=TileWall([]), game_id="local-lobby", round_id="local-lobby",
        )

    def new_client(self) -> str:
        with self.lock:
            _check(len(self.clients) < 256, "本地测试身份已达到上限，请重启测试服务")
            token = secrets.token_urlsafe(32)
            self.clients[hashlib.sha256(token.encode()).hexdigest()] = LocalClient()
            return token

    def client(self, token: str) -> LocalClient:
        key = hashlib.sha256(token.encode()).hexdigest()
        _check(key in self.clients, "本地测试身份已失效")
        return self.clients[key]

    def tick(self, *, now=None):
        """Only timers and real draws advance automatically, never human passes."""
        with self.lock:
            timestamp = time.monotonic() if now is None else now
            for room in self.rooms.values():
                if room.game is not None and not room.recovery_pending:
                    room.game.expire_timers(timestamp)
                    self._advance(room, timestamp)

    @staticmethod
    def _advance(room, now):
        game = room.game
        if game.phase == GamePhase.WAITING_DRAW:
            try:
                game.draw_tile(game.current_player().player_id, now=now)
            except GameStateError:
                if game.round_result is None:
                    raise
        record = room.wall_records.get(game.round_id)
        if record is not None:
            record.sync(game)
        if game.phase == GamePhase.ROUND_ENDED and game.round_result is not None and room.phase == RoomPhase.PLAYING:
            room.phase = RoomPhase.REVIEWING
            room.revision += 1
        for player in game.players:
            signature = json.dumps(player.to_public_dict(reveal_hand=True), ensure_ascii=False, sort_keys=True)
            if room.hand_signatures.get(player.player_id) != signature:
                room.hand_signatures[player.player_id] = signature
                room.hand_revisions[player.player_id] = room.hand_revisions.get(player.player_id, 0) + 1

    def dispatch(self, token, command, *, now=None):
        with self.lock:
            timestamp = time.monotonic() if now is None else now
            self.tick(now=timestamp)
            client = self.client(token)
            kind = command.get("type")
            if kind in {"create", "join"}:
                _check(client.room_id is None, "每个本地测试身份只能进入一个房间")
                name = _name(command.get("room_name"))
                username = _name(command.get("username"), maximum=24)
                password = _password(command.get("password"))
                existing = next((room for room in self.rooms.values() if room.name.casefold() == name.casefold()), None)
                if kind == "create":
                    _check(existing is None, "房间名已被使用")
                    _check(len(self.rooms) < 16, "本地测试最多同时创建 16 个房间")
                    salt = secrets.token_bytes(16)
                    room = Room(str(uuid4()), name, client.member_id, salt,
                                hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000))
                    self.rooms[room.room_id] = room
                else:
                    _check(existing is not None, "房间名或密码不正确")
                    room = existing
                    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), room.salt, 600000)
                    _check(hmac.compare_digest(digest, room.password_hash), "房间名或密码不正确")
                    _check(len(room.members) < 10, "房间最多容纳 10 人")
                    _check(all(member.name.casefold() != username.casefold() for member in room.members.values()), "房间内已有人使用这个用户名")
                room.members[client.member_id] = Member(client.member_id, username)
                client.room_id, client.notice = room.room_id, ""
                room.revision += 1
                return self.view(token, now=timestamp)

            _check(client.room_id in self.rooms, "请先创建或加入房间")
            room = self.rooms[client.room_id]
            _check(command.get("room_id") == room.room_id, "房间已变化，请刷新")
            member = room.members[client.member_id]
            command_id = command.get("command_id")
            _check(type(command_id) is str and 0 < len(command_id) <= 100, "缺少操作编号")
            cache_key = (client.member_id, command_id)
            fingerprint = json.dumps(command, sort_keys=True, ensure_ascii=False)
            if cache_key in room.seen_commands:
                _check(room.seen_commands[cache_key] == fingerprint, "重复操作编号包含不同内容")
                return self.view(token, now=timestamp)
            active = room.game is not None and room.game.phase != GamePhase.ROUND_ENDED
            is_host = client.member_id == room.host_id

            if kind == "start":
                _check(is_host, "只有房主可以开局")
                _check(not active, "本局已开始，不能修改人员或设置")
                _check(room.phase in {RoomPhase.WAITING_PLAYERS, RoomPhase.ARRANGING}, "请房主先点击下一局，结束赛后查看")
                _check(command.get("room_revision") == room.revision, "房间成员已变化，请重新确认")
                seats = command.get("seats")
                _check(type(seats) is list and len(seats) == 4 and all(type(item) is str for item in seats), "请指定四个座位")
                _check(len(set(seats)) == 4 and set(seats) <= set(room.members), "需要四个不同的房间成员才能开局")
                settings = parse_timeouts(command.get("timeouts"))
                multiple = command.get("allow_multiple_hu")
                _check(type(multiple) is bool, "请指定是否允许一炮多响")
                rng = RecordingRandom()
                if room.game is not None:
                    _check(command.get("round_id") == room.game.round_id, "上一局已变化")
                    _check(dict(vars(settings)) == room.timeouts and multiple == room.allow_multiple_hu, "本房间开局配置已锁定，需要更改请新建房间")
                    room.game.start_next_round(source_round_id=room.game.round_id, rng=rng)
                    for index, player in enumerate(room.game.players):
                        player.name = room.members[seats[index]].name
                else:
                    room.game = GameState.new_game(
                        player_names=[room.members[member_id].name for member_id in seats],
                        timeout_config=settings, config=GameConfig(allow_multiple_hu=multiple),
                        rng=rng,
                    )
                for player in room.game.players:
                    player.is_human = True
                room.seats, room.round_members = list(seats), list(seats)
                room.timeouts, room.allow_multiple_hu = dict(vars(settings)), multiple
                room.round_number += 1
                room.wall_records[room.game.round_id] = RoundWallRecord.begin(room.game, room.round_number, rng)
                room.phase = RoomPhase.PLAYING
                room.revision += 1
                self._advance(room, timestamp)
            elif kind == "prepare_next_round":
                _check(is_host, "只有房主可以结束赛后查看")
                _check(room.game is not None and command.get("round_id") == room.game.round_id, "该操作属于旧牌局")
                _check(room.phase == RoomPhase.REVIEWING and room.game.round_result is not None, "当前不在赛后查看阶段")
                # Closing review does not shuffle, deal, rotate or change scores.
                room.phase = RoomPhase.ARRANGING
                room.revision += 1
            elif kind in {"assign", "kick", "leave"}:
                if kind == "assign":
                    _check(is_host and room.phase in {RoomPhase.WAITING_PLAYERS, RoomPhase.ARRANGING}, "只能由房主在安排阶段调整座位")
                    _check(command.get("room_revision") == room.revision, "成员已变化，请重新安排")
                    seats = command.get("seats")
                    _check(type(seats) is list and len(seats) == 4, "需要四个座位")
                    populated = [item for item in seats if item is not None]
                    _check(all(type(item) is str and item in room.members for item in populated) and len(set(populated)) == len(populated), "座位不能重复或包含已离开的成员")
                    room.seats = list(seats)
                    room.revision += 1
                else:
                    target = command.get("member_id") if kind == "kick" else client.member_id
                    _check(type(target) is str and target in room.members, "该成员已离开")
                    _check(kind != "kick" or (is_host and target != room.host_id), "只有房主可以移除其他成员")
                    _check(not active or target not in room.round_members or target == room.host_id, "本地原型暂不支持移除正在打牌的人，请等本局结束")
                    if target == room.host_id:
                        for other in self.clients.values():
                            if other.room_id == room.room_id:
                                other.room_id, other.notice = None, "房主已关闭房间"
                        del self.rooms[room.room_id]
                    else:
                        del room.members[target]
                        room.seats = [None if item == target else item for item in room.seats]
                        for other in self.clients.values():
                            if other.member_id == target:
                                other.room_id, other.notice = None, "你已离开房间" if kind == "leave" else "房主已将你移出房间"
                        room.revision += 1
            elif kind == "spectate":
                _check(not active or member.member_id not in room.round_members, "本局参赛者不能切换上帝视角")
                mode = command.get("mode")
                _check(mode in {"public", "god"}, "未知的观战视角")
                member.spectator_mode = mode
                room.revision += 1
            else:
                _check(active and member.member_id in room.round_members, "只有本局参赛者可以操作自己的牌")
                game = room.game
                pid = f"p{room.round_members.index(member.member_id)}"
                _check(command.get("round_id") == game.round_id, "该操作属于旧牌局")
                if kind in {"discard", "move", "group", "disband", "chi"}:
                    _check(command.get("hand_revision") == room.hand_revisions[pid], "手牌已变化，请重新选择")
                self._game_command(game, pid, kind, command, timestamp)
                self._advance(room, timestamp)
            room.seen_commands[cache_key] = fingerprint
            while len(room.seen_commands) > 2048:
                room.seen_commands.popitem(last=False)
            return self.view(token, now=timestamp)

    def wall_record(self, token, *, room_id, round_id):
        """Authorize a read against the current room AND its current review phase."""
        with self.lock:
            client = self.client(token)
            _check(client.room_id == room_id and room_id in self.rooms, "无权查看该房间牌山")
            room = self.rooms[room_id]
            _check(client.member_id in room.members, "你已不在该房间")
            _check(not room.recovery_pending, "房间已暂停，请先恢复")
            _check(room.game is not None and room.game.round_id == round_id, "只能查看当前结算局的牌山")
            _check(room.phase == RoomPhase.REVIEWING and room.game.phase == GamePhase.ROUND_ENDED
                   and room.game.round_result is not None, "仅在正常结算后的赛后查看阶段开放牌山")
            record = room.wall_records.get(round_id)
            _check(record is not None and record.valid, "本局无完整牌序记录")
            return record.to_dict()

    @staticmethod
    def _game_command(game, pid, kind, command, now):
        """Bind every intention to its real seat and require its original window."""
        turn = command.get("turn_window_id")
        if kind in {"discard", "turn_action", "move", "group", "disband"}:
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
            game.move_hand_tile_by_id(pid, command["tile_id"], command["index"], group_id=command.get("group_id"), source_turn_window_id=turn)
        elif kind == "group":
            game.create_hand_group(pid, command["tile_ids"], source_turn_window_id=turn)
        elif kind == "disband":
            game.disband_hand_group(pid, command["group_id"], source_turn_window_id=turn)
        else:
            raise RoomError("本地房间不支持该操作")

    def view(self, token, *, now=None):
        """Build a viewer-specific packet; never attach the developer wall/editor."""
        with self.lock:
            timestamp = time.monotonic() if now is None else now
            client = self.client(token)
            room = self.rooms.get(client.room_id)
            game = (room.game if room else None) or self.empty_game
            pid = None
            if room and room.game and client.member_id in room.round_members:
                pid = f"p{room.round_members.index(client.member_id)}"
            result = game.to_public_dict(viewer_player_id=pid)
            # These engine records are useful for debugging, but contain intentions
            # and tile-selection metadata that must not be broadcast to a table.
            result["resolved_actions"] = []
            visible = {"round_started", "initial_hands_dealt", "tile_drawn", "tile_discarded", "round_ended", "chi_applied", "peng_applied", "ming_gang_applied", "an_gang_applied", "jia_gang_declared", "win_settled"}
            result["event_log"] = [
                {"sequence": index, **{key: value for key, value in event.items() if key in {"type", "player_id", "seat", "tile", "reason"}}}
                for index, event in enumerate(event for event in game.event_log if event["type"] in visible and "_private_to_player_id" not in event)
            ]
            if result["pending_action_detail"] and result["pending_action_detail"]["player_id"] != pid:
                detail = result["pending_action_detail"]
                result["pending_action_detail"] = {key: value for key, value in detail.items() if key in {"player_id", "action_type", "source_player_id", "claimed_tile", "detail_id"}}
            member = room.members[client.member_id] if room else None
            god = member is not None and pid is None and member.spectator_mode == "god"
            if god:
                for player, public in zip(game.players, result["players"]):
                    public["observer_tiles"] = list(player.hand_tiles)
                result["active_timer"] = game.active_timer.to_dict() if game.active_timer else None
                rob = game.rob_kong_window
                if rob is not None and rob.paused_turn_timer is not None:
                    result["paused_turn_remaining_seconds"] = rob.paused_turn_timer.remaining_seconds(rob.opened_at)
            started = room is not None and room.game is not None
            ended = game.phase == GamePhase.ROUND_ENDED
            stage = game.phase.value
            if not started:
                stage = "LOBBY"
            elif ended:
                stage = "ROUND_ENDED"
            elif pid is None:
                stage = "OBSERVING"
            elif game.phase == GamePhase.WAITING_TURN_ACTION:
                stage = "OWN_TURN" if pid == game.current_player().player_id else "BOT_TURN"
            elif game.phase == GamePhase.WAITING_ACTION_DETAIL and game.pending_action_detail.player_id != pid:
                stage = "WAITING_OTHER"
            result.update(
                session_id=client.member_id, server_instance_id=self.instance_id, viewer_player_id=pid,
                viewer_seat=None if pid is None else game.get_player(pid).seat,
                hand_revision=room.hand_revisions.get(pid, 0) if room else 0,
                server_now=timestamp, notice=client.notice,
                room=None if room is None else {
                    "room_id": room.room_id, "name": room.name, "host_id": room.host_id,
                    "revision": room.revision, "seats": list(room.seats),
                    "phase": room.phase.value,
                    "wall_review_available": room.phase == RoomPhase.REVIEWING and game.round_id in room.wall_records and room.wall_records[game.round_id].valid,
                    "round_members": list(room.round_members), "is_host": room.host_id == client.member_id,
                    "members": [{"member_id": person.member_id, "name": person.name, "spectator_mode": person.spectator_mode} for person in room.members.values()],
                    "spectator_mode": member.spectator_mode, "allow_multiple_hu": room.allow_multiple_hu,
                },
                ui={"stage": stage, "mode": "four", "started": started,
                    "round_number": room.round_number if room else 0,
                    "settings_locked": started, "timeout_settings": room.timeouts if room else dict(vars(DEFAULT_TIMEOUTS)),
                    "grouping_allowed": pid is not None and game.phase == GamePhase.WAITING_TURN_ACTION,
                    "editing_allowed": False, "scene": "", "scenes": {}, "phase_history": [],
                    "drawn_tile_id": game.drawn_tile_id if pid == game.current_player().player_id else None},
            )
            # Passage of time alone does not change a view's content revision.
            stable = dict(result)
            stable.pop("server_now")
            signature = json.dumps(stable, ensure_ascii=False, sort_keys=True)
            if signature != client.signature:
                client.sequence += 1
                client.signature = signature
            # Detach engine-owned meld dictionaries before HTTP encodes off-lock.
            return {**json.loads(signature), "server_now": timestamp, "revision": client.sequence}
