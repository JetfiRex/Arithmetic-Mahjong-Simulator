"""Single-hand practice controller; all legal actions use the real simulator."""

from __future__ import annotations

import random
import threading
import time
from typing import Any
from uuid import uuid4

from calculator_base.constants import ALL_TILES
from simulator import ActionType, GamePhase, GameState, PlayerState, TileWall, TimeoutConfig
from simulator.game_state import GameStateError
from simulator.melds import make_peng_meld
from simulator.action_options import build_turn_action_options


PLAYER = "p0"
SOURCE = "p3"
SCENES = {
    "basic": "基础试牌",
    "random": "随机开局",
    "manual": "手动吃牌",
    "peng": "碰牌",
    "ming": "明杠补牌",
    "an": "暗杠补牌",
    "jia": "加杠补牌",
    "win": "自摸结算",
    "discard_win": "点胡结算",
    "timer": "计时回合",
}
BASIC_HAND = [3, "+", 6, 1, "+", 2, 8, 8, 8, 4, 4, 5, 5]
READY_HAND = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5]


class RevisionConflict(GameStateError):
    pass


def validate_tiles(values: Any, *, maximum: int) -> list[int | str]:
    """Editors accept plain JSON arrays, including deliberately custom counts."""
    if not isinstance(values, list) or len(values) > maximum:
        raise GameStateError(f"请提供最多 {maximum} 张牌的 JSON 数组")
    for value in values:
        if type(value) not in (int, str) or value not in ALL_TILES:
            raise GameStateError(f"无效的牌：{value!r}")
    return list(values)


class PracticeSession:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.revision = 0
        self.round_number = 0
        self.scene = "basic"
        self.game: GameState
        self.new_scene("basic")

    def new_scene(self, scene: str, *, now: float | None = None) -> None:
        if scene not in SCENES:
            raise GameStateError("未知的试牌场景")
        timestamp = time.monotonic() if now is None else now
        pool = TileWall.from_rule_tiles(shuffle=False).tiles
        random.shuffle(pool)
        melds = []
        own_draw = scene in {"an", "jia", "win", "timer"}
        if scene == "random":
            hand = [pool.pop() for _ in range(13)]
            prefix = []
        else:
            hand = list(BASIC_HAND)
            prefix = [9, 7, 3, 8, 4, 5]
            if scene == "peng":
                prefix = [8, 7, 9]
            elif scene == "manual":
                hand = list(range(10)) + ["+", "×", 7]
                prefix = [9, 7, 3]
            elif scene == "ming":
                hand = [6] * 4 + [1, "+", 2, 3, 2, "+", 2, 4, 5]
                prefix = [6, 5]
            elif scene == "an":
                hand = [3] * 5 + [1, "+", 2, 3, 2, "+", 2, 4]
                prefix = [5, 5]
            elif scene == "jia":
                hand = [6, 1, "+", 2, 3, 2, "+", 2, 4]
                melds = [make_peng_meld(6)]
                prefix = [5, 5]
            elif scene in {"win", "discard_win"}:
                hand = list(READY_HAND)
                prefix = [6]
            elif scene == "timer":
                prefix = [7, 9, 3, 8]
            for value in hand + [tile for meld in melds for tile in meld["tiles"]] + prefix:
                if value not in pool:
                    raise GameStateError(f"场景牌池不足：{value}")
                pool.remove(value)
        self.scene = scene
        self.round_number += 1
        self.game = GameState(
            players=[
                PlayerState(
                    player_id=f"p{seat}", seat=seat,
                    name="我" if seat == 0 else "来牌" if seat == 3 else f"虚拟席 {seat}",
                    is_human=seat == 0,
                    hand_tiles=hand if seat == 0 else [],
                    melded_groups=melds if seat == 0 else [],
                )
                for seat in range(4)
            ],
            wall=TileWall(pool + list(reversed(prefix))),
            timeout_config=TimeoutConfig(5, 5, 30, 5) if scene == "timer" else TimeoutConfig(),
        )
        if own_draw:
            self.game.phase = GamePhase.WAITING_DRAW
            self.game.current_turn_seat = 0
            self.game.draw_tile(PLAYER, now=timestamp)
        else:
            self.game._begin_turn_without_draw(self.game.get_player(SOURCE), now=timestamp)
            self.game.active_timer = None
        self.revision += 1

    def _normalize(self, now: float) -> None:
        """Virtual seats only pass; a source tile is revealed on explicit request."""
        game = self.game
        window = game.response_window
        if window is not None:
            for player_id in list(window.players()):
                if game.response_window is not window:
                    break
                if player_id == PLAYER or player_id in window.auto_passed_players or player_id in window.requests_by_player:
                    continue
                game.submit_response(player_id, ActionType.PASS, now=now)
        if game.phase == GamePhase.WAITING_DRAW and game.current_turn_seat != 0:
            game._begin_turn_without_draw(game.get_player(SOURCE), now=now)
            game.active_timer = None

    def tick(self, *, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        before = (self.game.phase, len(self.game.event_log))
        self.game.expire_timers(timestamp)
        self._normalize(timestamp)
        if before != (self.game.phase, len(self.game.event_log)):
            self.revision += 1

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self.tick(now=timestamp)
        game = self.game
        state = game.to_public_dict(viewer_player_id=PLAYER)
        stage = game.phase.value
        if game.phase == GamePhase.WAITING_TURN_ACTION:
            stage = "OWN_TURN" if game.current_turn_seat == 0 else "READY_SOURCE"
        state.update(
            revision=self.revision,
            server_now=timestamp,
            practice={
                "scene": self.scene,
                "scenes": SCENES,
                "round_number": self.round_number,
                "stage": stage,
                "wall_tiles": list(reversed(game.wall.tiles)),
                "editing_allowed": game.phase in (GamePhase.WAITING_DRAW, GamePhase.WAITING_TURN_ACTION),
                "grouping_allowed": game.phase == GamePhase.WAITING_TURN_ACTION,
                "drawn_tile_id": game.drawn_tile_id if game.current_turn_seat == 0 else None,
            },
        )
        return state

    def dispatch(self, command: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self.tick(now=timestamp)
        if command.get("revision") != self.revision:
            raise RevisionConflict("牌局已更新，请重新操作")
        game = self.game
        kind = command.get("type")
        token = command.get("turn_window_id")
        if kind == "scene":
            self.new_scene(command.get("scene"), now=timestamp)
        elif kind == "reveal":
            if game.phase != GamePhase.WAITING_TURN_ACTION or game.current_turn_seat != 3:
                raise GameStateError("当前不能翻开来牌")
            game.phase = GamePhase.WAITING_DRAW
            try:
                tile = game.draw_tile(SOURCE, now=timestamp)
            except GameStateError:
                if game.round_result is None:
                    raise
            else:
                game.discard_tile(SOURCE, tile, now=timestamp)
        elif kind == "draw":
            try:
                game.draw_tile(PLAYER, now=timestamp)
            except GameStateError:
                if game.round_result is None:
                    raise
        elif kind == "discard":
            game.discard_tile_by_id(PLAYER, command["tile_id"], now=timestamp, source_turn_window_id=token)
        elif kind == "turn_action":
            game.submit_turn_action(
                PLAYER, ActionType(command["action"]), option_id=command.get("option_id"),
                now=timestamp, source_turn_window_id=token,
            )
        elif kind == "response":
            game.submit_response(
                PLAYER, ActionType(command["action"]), now=timestamp,
                source_window_id=command.get("window_id"),
            )
        elif kind == "chi":
            game.submit_chi_detail(
                PLAYER, option_id=command.get("option_id"), selected_tile_ids=command.get("tile_ids"),
                now=timestamp, detail_id=command.get("detail_id"),
            )
        elif kind == "pass_detail":
            game.pass_action_detail(PLAYER)
        elif kind == "move":
            game.move_hand_tile_by_id(
                PLAYER, command["tile_id"], command["index"], group_id=command.get("group_id"),
                source_turn_window_id=token,
            )
        elif kind == "group":
            game.create_hand_group(PLAYER, command["tile_ids"], source_turn_window_id=token)
        elif kind == "disband":
            game.disband_hand_group(PLAYER, command["group_id"], source_turn_window_id=token)
        elif kind in {"edit_wall", "edit_hand"}:
            self._edit(kind, command.get("tiles"), now=timestamp)
        elif kind == "next_round":
            game.start_next_round(source_round_id=command.get("round_id"))
            for player in game.players[1:]:
                game.wall.tiles.extend(player.hand_tiles)
                player.hand_tiles.clear()
                player._reset_hand_identity()
            random.shuffle(game.wall.tiles)
            game._begin_turn_without_draw(game.get_player(SOURCE), now=timestamp)
            game.active_timer = None
            self.round_number += 1
            self.scene = "random"
        else:
            raise GameStateError("未知的操作")
        self._normalize(timestamp)
        self.revision += 1
        return self.snapshot(now=timestamp)

    def _edit(self, kind: str, values: Any, *, now: float) -> None:
        game = self.game
        if game.phase not in (GamePhase.WAITING_DRAW, GamePhase.WAITING_TURN_ACTION, GamePhase.ROUND_ENDED):
            raise GameStateError("请先完成当前吃牌或响应，再编辑牌")
        if game.phase == GamePhase.ROUND_ENDED:
            raise GameStateError("请先开始下一局，再编辑牌")
        tiles = validate_tiles(values, maximum=512 if kind == "edit_wall" else 32)
        if kind == "edit_wall":
            game.wall.tiles = list(reversed(tiles))
            if game.phase == GamePhase.WAITING_TURN_ACTION:
                player = game.current_player()
                game.turn_options_by_player[player.player_id] = build_turn_action_options(
                    player=player, wall_remaining=len(tiles), rule_adapter=game.rule_adapter,
                    allow_self_hu=game.drawn_tile_id is not None,
                    winning_tile=player.tile_value(game.drawn_tile_id) if game.drawn_tile_id else None,
                )
            game._log_event("practice_wall_edited", tile_count=len(tiles))
            return
        player = game.get_player(PLAYER)
        if not tiles:
            raise GameStateError("手牌至少保留一张；测试流局请清空牌堆")
        timer = game.active_timer
        owns_turn = game.phase == GamePhase.WAITING_TURN_ACTION and game.current_turn_seat == 0
        player.hand_tiles = tiles
        player._reset_hand_identity()
        if owns_turn:
            game._open_turn_action_window(
                player, now=now, drawn_tile_id=player.hand_tile_ids[-1] if tiles else None,
            )
            game.active_timer = timer
        else:
            game.turn_window_id = str(uuid4())
        game._log_event("practice_hand_edited", tile_count=len(tiles))
