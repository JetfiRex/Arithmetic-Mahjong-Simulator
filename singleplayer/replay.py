"""Versioned, detached board frames for replay; never re-execute bot decisions."""

from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from calculator_base.constants import TILE_COUNTS
from calculator_base.hand_structure import MeldedGroup, create_tile_from_value

FORMAT = "arithmetic-mahjong-replay"
VERSION = 1
MAX_FRAMES = 2048
EVENT_NAMES = {"round_started": "开局", "initial_hands_dealt": "发牌", "tile_drawn": "摸牌",
               "tile_discarded": "出牌", "meld_created": "副露成立", "meld_upgraded": "加杠成立",
               "gang_replacement_drawn": "杠后补牌", "jia_gang_declared": "宣告加杠",
               "win_settled": "胡牌结算", "round_ended": "本局结束",
               "response_submitted": "提交响应", "rob_kong_response_submitted": "抢杠响应",
               "turn_action_submitted": "回合操作", "chi_detail_submitted": "选择吃法",
               "action_detail_started": "选择吃法", "action_detail_passed": "放弃吃牌",
               "response_window_resolved": "响应结束", "turn_gang_prompt_passed": "跳过胡杠提示",
               "timer_expired": "操作超时", "rob_kong_passed": "抢杠响应结束"}
ACTION_NAMES = {"PASS": "过", "HU": "胡牌", "CHI": "吃", "PENG": "碰", "MING_GANG": "明杠",
                "AN_GANG": "暗杠", "JIA_GANG": "加杠", "DISCARD": "出牌", "discard": "出牌",
                "turn_action": "回合操作", "response": "吃碰杠胡响应", "rob_response": "抢杠响应", "chi": "选择吃法"}


def claimed_tiles(game):
    """Robbed tiles leave the source hand without entering a discard river."""
    result, pending = [], None
    for event in game.event_log:
        if event["type"] == "jia_gang_declared":
            pending = event["tile"]
        elif event["type"] in {"meld_upgraded", "rob_kong_passed"}:
            pending = None
        elif event["type"] == "win_settled" and pending is not None and event["settlement"]["win_result"]["win_method"] == "抢杠":
            result.append(pending)
            pending = None  # Multiple winners share the same physical tile.
    return result


def board_frame(game):
    """Board data contains no wall order, executable state or live deadlines."""
    return deepcopy({
        "round_id": game.round_id, "phase": game.phase.value,
        "current_turn_seat": game.current_turn_seat, "drawn_tile_id": game.drawn_tile_id,
        "self_draw_allowed": not game.turn_hu_prompt_passed,
        "players": [p.to_public_dict(reveal_hand=True) for p in game.players],
        "last_discard": game.last_discard.to_dict() if game.last_discard else None,
        "discard_history": [r.to_dict() for r in game.discard_history],
        "wall_count": len(game.wall.tiles), "min_fan": getattr(game.rule_adapter, "min_fan", 1),
        "claimed_tiles": claimed_tiles(game),
        "round_result": game.round_result.to_dict() if game.round_result else None,
        "settlement_events": [e.to_dict() for e in game.settlement_events],
    })


class RoundReplay:
    def __init__(self, game, number, experience, profiles, now):
        self.cursor = 0
        self.origin = now
        self.data = {"format": FORMAT, "version": VERSION, "round_id": game.round_id,
                     "round_number": number, "created_at": datetime.now(timezone.utc).isoformat(),
                     "ruleset_id": game.rule_adapter.ruleset_id,
                     "mode": game.config.mode.value, "experience": experience,
                     "config": {"allow_multiple_hu": game.config.allow_multiple_hu,
                                "hand_preparation_seconds": game.config.hand_preparation_seconds,
                                "timeouts": asdict(game.timeout_config)},
                     "profiles": dict(profiles), "complete": False, "frames": []}

    def capture(self, game, now, action=None):
        events = game.event_log[self.cursor:]
        self.cursor = len(game.event_log)
        meaningful = [e for e in events if e["type"] not in {"hand_tile_moved", "hand_tile_reordered"}]
        if action:
            action = {**action, "label": ACTION_NAMES.get(action["label"], action["label"])}
            meaningful.append({"type": "recorded_action", **action})
        if self.data["frames"] and not meaningful:
            return
        if len(self.data["frames"]) >= MAX_FRAMES:
            return
        frame = board_frame(game)
        event = next((e for e in reversed(meaningful) if e["type"] != "recorded_action"), meaningful[-1] if meaningful else {"type": "initial_hands_dealt"})
        name = next((p.name for p in game.players if p.player_id == event.get("player_id")), "")
        face = event.get("tile", "")
        if face == "" and event["type"] in {"tile_drawn", "gang_replacement_drawn"} and game.drawn_tile_id:
            face = game.current_player().tile_value(game.drawn_tile_id)
        frame.update(index=len(self.data["frames"]), elapsed=max(0, now - self.origin),
                     label=" · ".join(str(v) for v in (name, EVENT_NAMES.get(event["type"], action.get("label", "局面变化") if action else "局面变化"), face) if v != ""),
                     events=deepcopy(meaningful), wall=list(reversed(game.wall.tiles)))
        self.data["frames"].append(frame)
        self.data["complete"] = game.round_result is not None


def analysis_position(frame, player_id="p0", perspective="player"):
    if player_id not in {"p0", "p1", "p2", "p3"} or perspective not in {"player", "omniscient"}:
        raise ValueError("请选择合法分析玩家和信息视角")
    player = next(p for p in frame["players"] if p["player_id"] == player_id)
    hand = list(player["hand_tiles"])
    visible = list(frame.get("claimed_tiles", []))
    for other in frame["players"]:
        visible.extend(other["discarded_tiles"])
        if other["player_id"] != player_id:
            visible.extend(v for m in other["melded_groups"] for v in m["tiles"])
            if perspective == "omniscient":
                visible.extend(other["hand_tiles"])
    drawn = next((t["value"] for t in player["hand_tile_records"] if t["tile_id"] == frame["drawn_tile_id"]), None)
    command = {"hand": hand, "melds": [{"group_type": m["group_type"], "tiles": list(m["tiles"])} for m in player["melded_groups"]],
               "visible": visible, "min_fan": frame["min_fan"], "draws_left": None,
               "context": {"self_draw_allowed": drawn is not None and frame.get("self_draw_allowed", True),
                           "is_heavenly": not any(r["player_id"] == player_id for r in frame["discard_history"])}}
    if drawn is not None:
        command["winning_tile"] = drawn
    return command


def validate_replay(data):
    """Validate a display-only file. Import cannot restore or mutate a game."""
    if type(data) is not dict or data.get("format") != FORMAT or type(data.get("version")) is not int or data["version"] != VERSION:
        raise ValueError("不支持的牌谱格式或版本")
    frames = data.get("frames")
    if data.get("complete") is not True or type(frames) is not list or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("请导入已经结束的完整牌谱")
    for key in ("round_id", "created_at", "ruleset_id", "mode", "experience"):
        if type(data.get(key)) is not str or len(data[key]) > 200:
            raise ValueError("牌谱基本信息不正确")
    if type(data.get("round_number")) is not int or not 1 <= data["round_number"] <= 10**9:
        raise ValueError("牌谱局数不正确")
    for index, frame in enumerate(frames):
        if type(frame) is not dict or frame.get("index") != index or type(frame.get("label")) is not str or len(frame["label"]) > 300:
            raise ValueError("牌谱步骤不正确")
        if frame.get("round_id") != data["round_id"] or type(frame.get("phase")) is not str:
            raise ValueError("牌谱步骤与牌局不一致")
        if frame.get("drawn_tile_id") is not None and (type(frame["drawn_tile_id"]) is not str or len(frame["drawn_tile_id"]) > 100):
            raise ValueError("牌谱摸入牌记录不正确")
        if type(frame.get("self_draw_allowed", True)) is not bool:
            raise ValueError("牌谱胡牌条件不正确")
        if type(frame.get("current_turn_seat")) is not int or frame["current_turn_seat"] not in range(4) or type(frame.get("min_fan")) is not int or not 0 <= frame["min_fan"] <= 6:
            raise ValueError("牌谱局面不正确")
        if type(frame.get("players")) is not list or len(frame["players"]) != 4:
            raise ValueError("牌谱须有四位玩家")
        counts = Counter()
        for seat, player in enumerate(frame["players"]):
            if type(player) is not dict or player.get("player_id") != f"p{seat}" or player.get("seat") != seat:
                raise ValueError("牌谱玩家不正确")
            if type(player.get("name")) is not str or len(player["name"]) > 200 or type(player.get("score")) is not int:
                raise ValueError("牌谱玩家信息不正确")
            for key in ("hand_tiles", "discarded_tiles"):
                values = player.get(key)
                if type(values) is not list or len(values) > 108 or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in values):
                    raise ValueError("牌谱含无效牌面")
                counts.update(values)
            records = player.get("hand_tile_records")
            if type(records) is not list or len(records) != len(player["hand_tiles"]) or any(type(t) is not dict or type(t.get("tile_id")) is not str or len(t["tile_id"]) > 100 for t in records):
                raise ValueError("牌谱手牌记录不正确")
            if [t.get("value") for t in records] != player["hand_tiles"]:
                raise ValueError("牌谱手牌与实体牌不一致")
            melds = player.get("melded_groups")
            if type(melds) is not list or len(melds) > 3:
                raise ValueError("牌谱副露不正确")
            for meld in melds:
                if type(meld) is not dict or type(meld.get("tiles")) is not list or type(meld.get("group_type")) is not str:
                    raise ValueError("牌谱副露不正确")
                values = meld["tiles"]
                if any(type(v) not in (int, str) or v not in TILE_COUNTS for v in values):
                    raise ValueError("牌谱副露含无效牌面")
                MeldedGroup([create_tile_from_value(v) for v in values], meld["group_type"])
                counts.update(values)
        wall = frame.get("wall")
        if type(wall) is not list or len(wall) > 108 or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in wall) or frame.get("wall_count") != len(wall):
            raise ValueError("牌谱牌山不正确")
        counts.update(wall)
        claimed = frame.get("claimed_tiles", [])
        if type(claimed) is not list or len(claimed) > 3 or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in claimed):
            raise ValueError("牌谱抢杠牌记录不正确")
        counts.update(claimed)
        if any(n > TILE_COUNTS[v] for v, n in counts.items()):
            raise ValueError("牌谱超出实体牌库存")
        history = frame.get("discard_history")
        if type(history) is not list or len(history) > 108 or any(type(r) is not dict or r.get("player_id") not in {"p0", "p1", "p2", "p3"} for r in history):
            raise ValueError("牌谱弃牌历史不正确")
        events = frame.get("events")
        if type(events) is not list or len(events) > 108 or any(type(e) is not dict or type(e.get("type")) is not str for e in events):
            raise ValueError("牌谱操作记录不正确")
        settlements = frame.get("settlement_events")
        if type(settlements) is not list or len(settlements) > 3:
            raise ValueError("牌谱结算记录不正确")
        for event in settlements:
            if type(event) is not dict or event.get("winner_id") not in {"p0", "p1", "p2", "p3"}:
                raise ValueError("牌谱赢家不正确")
            won = event.get("win_result")
            if type(won) is not dict or won.get("win_method") not in {"自摸", "点胡", "抢杠", "天胡"} or type(won.get("total_fan")) is not int:
                raise ValueError("牌谱胡牌信息不正确")
            if type(won.get("winning_tile")) not in (int, str) or won["winning_tile"] not in TILE_COUNTS:
                raise ValueError("牌谱胡牌牌面不正确")
            groups, fans = won.get("groups"), won.get("fan_items")
            if type(groups) is not list or len(groups) > 8 or any(type(g) is not list or len(g) > 14 or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in g) for g in groups):
                raise ValueError("牌谱成牌分组不正确")
            if type(fans) is not list or len(fans) > 100 or any(type(f) is not dict or type(f.get("name")) is not str or len(f["name"]) > 100 or type(f.get("total_fan")) is not int for f in fans):
                raise ValueError("牌谱番种不正确")
    return deepcopy(data)


class ReplayLibrary:
    def __init__(self):
        self.records = OrderedDict()

    def add(self, data, identifier=None):
        identifier = identifier or str(uuid4())
        self.records[identifier] = data
        while len(self.records) > 20:
            self.records.popitem(last=False)
        return identifier

    def list(self):
        return [{"id": key, **{k: data[k] for k in ("round_number", "created_at", "complete", "experience")},
                 "steps": len(data["frames"])} for key, data in reversed(self.records.items())]

    def get(self, identifier):
        data = self.records.get(identifier)
        if data is None:
            raise ValueError("牌谱不存在或已移出当前会话")
        if not data["complete"]:
            raise ValueError("本局结束后可保存并回放完整牌谱")
        return deepcopy(data)
