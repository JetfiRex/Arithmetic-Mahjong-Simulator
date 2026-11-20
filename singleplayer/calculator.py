"""13/14-tile analysis and the monotone-shanten DP used by the calculator UI."""

import asyncio
from collections import Counter, OrderedDict
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
from threading import RLock
import time

from bots.common_bot_helper.observation import TILE_INDEX, TILE_VALUES
from bots.math_bot_helper.analysis import AnalysisBudget
from bots.math_bot_helper.features import counts_from_values, possible_shape, values_from_counts
from bots.search_bot_helper.distance import DistanceAnalyzer
from calculator_base.constants import TILE_COUNTS
from calculator_base.hand_structure import Hand, MeldedGroup, create_tile_from_value
from calculator_base.mahjong_checker import ArithmeticMahjong
from simulator.rule_adapter import MeldSnapshot
from singleplayer.practice import _ARITHMETIC, _TRADITIONAL, _SPECIAL, _group_view, _worker_initializer


SUPPLY = tuple(TILE_COUNTS[v] for v in TILE_VALUES)
_DISTANCE = DistanceAnalyzer(cache_size=8192)
_SHAPES, _WINS, _VALUES = OrderedDict(), OrderedDict(), OrderedDict()
_TRANSITIONS, _DECISIONS, _RUNS = OrderedDict(), OrderedDict(), OrderedDict()


def _remember(cache, key, value, limit):
    cache[key] = value
    cache.move_to_end(key)
    if len(cache) > limit:
        cache.popitem(last=False)
    return value


class CalculationBusy(ValueError):
    pass


class _Interrupted(Exception):
    pass


def _values(values, label):
    if type(values) is not list or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in values):
        raise ValueError(f"{label}须为合法牌面列表")
    return tuple(values)


def validate_position(command):
    hand = _values(command.get("hand"), "手牌")
    visible = _values(command.get("visible", []), "已见牌")
    raw_melds = command.get("melds", [])
    if type(raw_melds) is not list or len(raw_melds) > 3:
        raise ValueError("固定副露须为最多三组的列表")
    melds = []
    for item in raw_melds:
        if type(item) is not dict or type(item.get("group_type")) is not str:
            raise ValueError("副露格式不正确")
        values = _values(item.get("tiles"), "副露牌")
        MeldedGroup([create_tile_from_value(v) for v in values], item["group_type"])
        melds.append(MeldSnapshot(item["group_type"], values))
    melds = tuple(melds)
    size = 14 - 4 * len(melds)
    if len(hand) not in (size - 1, size):
        raise ValueError(f"当前副露数量下请输入 {size - 1} 或 {size} 张手牌")
    fixed = tuple(v for meld in melds for v in meld.tiles)
    if len(visible) > 107 or any(n > TILE_COUNTS[v] for v, n in Counter(hand + visible + fixed).items()):
        raise ValueError("手牌和已见牌合计超出牌库张数")
    min_fan = command.get("min_fan", 1)
    if type(min_fan) is not int or not 0 <= min_fan <= 6:
        raise ValueError("起胡番须为 0 到 6，0 表示不设门槛")
    draws = command.get("draws_left")
    if draws is not None and (type(draws) is not int or not 0 <= draws <= 108):
        raise ValueError("剩余自摸次数须为 0 到 108，或不限制")
    winning_tile = command.get("winning_tile", hand[-1] if len(hand) == size else None)
    if len(hand) == size and (type(winning_tile) not in (int, str) or winning_tile not in hand):
        raise ValueError("摸入牌须在当前手牌中")
    context = command.get("context", {})
    if type(context) is not dict or set(context) - {"self_draw_allowed", "is_heavenly"}:
        raise ValueError("计番场景格式不正确")
    allowed, heavenly = context.get("self_draw_allowed", True), context.get("is_heavenly", False)
    if type(allowed) is not bool or type(heavenly) is not bool or type(command.get("quick", False)) is not bool:
        raise ValueError("计番场景须使用布尔值")
    counts, known = counts_from_values(hand), counts_from_values(visible + fixed)
    unseen = tuple(n - h - v for n, h, v in zip(SUPPLY, counts, known))
    return counts, unseen, min_fan, draws, winning_tile if len(hand) == size else None, melds, allowed, heavenly


def _remove(counts, index):
    result = list(counts)
    result[index] -= 1
    return tuple(result)


def _add(counts, index):
    result = list(counts)
    result[index] += 1
    return tuple(result)


class Calculator:
    def __init__(self, hand, unseen, min_fan, draws_left, winning_tile, melds=(), self_draw_allowed=True, is_heavenly=False, *, seconds=10):
        self.hand, self.unseen = hand, unseen
        self.min_fan, self.draws_left, self.winning_tile = min_fan, draws_left, winning_tile
        self.melds, self.size = melds, 14 - 4 * len(melds)
        self.self_draw_allowed, self.is_heavenly = self_draw_allowed, is_heavenly
        self.budget = AnalysisBudget(time.monotonic() + seconds)
        self.shapes, self.wins, self.cache = _SHAPES, _WINS, _VALUES
        self.nodes = 0
        self.result = {"hand": values_from_counts(hand), "hand_size": sum(hand),
                       "unseen_total": sum(unseen), "min_fan": min_fan, "draws_left": draws_left,
                       "melds": [m.to_dict() for m in melds], "drawn": sum(hand) == self.size,
                       "model": "monotone-shanten", "complete": False, "value": None,
                       "shanten": None, "shanten_bounds": None, "win": None,
                       "improvements": [], "candidates": []}
        self.resume_key = (hand, unseen, min_fan, draws_left, winning_tile, melds, self_draw_allowed, is_heavenly)
        saved = _RUNS.get(self.resume_key)
        self.passes = 1 if saved is None else saved["passes"] + 1
        self.total_nodes = 0 if saved is None else saved["total_nodes"]
        self.total_ms = 0 if saved is None else saved["total_elapsed_ms"]
        if saved is not None:
            self.result = saved["result"]

    def check(self):
        if self.budget.expired():
            raise _Interrupted()

    def shanten(self, hand):
        key = self.melds, hand
        if key not in self.shapes:
            self.check()
            # Structural distance intentionally permits empty waits. Inventory
            # affects draw probabilities, not the definition of shanten.
            result = _DISTANCE.analyze(hand, self.melds, unseen=tuple(14 - n for n in hand), budget=self.budget)
            if not result.complete:
                if hand == self.hand:
                    self.result["shanten_bounds"] = [result.lower - 1, None if result.upper is None else result.upper - 1]
                raise _Interrupted()
            if result.value is None:
                raise ValueError("无法找到成牌目标")
            _remember(self.shapes, key, result.value - 1, 16384)
        return self.shapes[key]

    def win(self, hand, incoming, *, heavenly=False):
        key = self.melds, self.min_fan, hand, incoming, heavenly
        if key in self.wins:
            return self.wins[key]
        self.check()
        if not possible_shape(hand, self.melds):
            _remember(self.wins, key, None, 4096)
            return None
        values = list(values_from_counts(hand))
        values.remove(incoming)
        tiles = [create_tile_from_value(v) for v in values]
        tiles.append(create_tile_from_value(incoming, winning=True))
        source = Hand(melded_groups=[MeldedGroup([create_tile_from_value(v) for v in m.tiles], m.group_type) for m in self.melds],
                      hand_tiles=tiles, winning_tile=tiles[-1], winning_method="自摸", is_heavenly=heavenly)
        won, groups, family, info = ArithmeticMahjong(min_fan=self.min_fan).can_win(source)
        if not won or info is None:
            raise ValueError("牌型与计番结果不一致")
        qualifies = bool(info["can_start"])
        fans = info.get("fan_result")
        result = {"fan": info["total_fan"], "qualifies": qualifies,
                  "value": 2 ** max(info["total_fan"], 1) if qualifies else 0,
                  "family": family, "groups": [_group_view(group, family) for group in groups],
                  "fans": [{"name": item.fan_type.fan_name, "fan": item.get_total_fan()}
                           for item in fans.results] if fans is not None else []}
        return _remember(self.wins, key, result, 4096)

    def discards(self, hand):
        rows = []
        for index, number in enumerate(hand):
            if number:
                after = _remove(hand, index)
                rows.append((index, after, self.shanten(after)))
        return rows

    def value14(self, hand, unseen, draws, incoming):
        win = self.win(hand, incoming)
        if win is not None and win["qualifies"]:
            return float(win["value"])
        if draws == 0:
            return 0.0
        options = self.discards(hand)
        minimum = min(s for _, _, s in options)
        key = self.melds, self.min_fan, hand, unseen, draws, incoming
        scores = _DECISIONS.get(key)
        if scores is None:
            scores = _remember(_DECISIONS, key, {}, 2048)
        for index, after, s in options:
            if s == minimum and index not in scores:
                scores[index] = self.value13(after, unseen, draws, shanten=s)
        best = max(scores.values())
        _DECISIONS.pop(key, None)
        return best

    def transitions(self, hand, unseen, draws, shanten, records=None, *, heavenly=False):
        total = sum(unseen)
        allowed = bool(total) and (draws is None or draws >= shanten+1)
        if not allowed and records is None:
            return 0.0
        key = self.melds, self.min_fan, hand, unseen, draws, shanten, heavenly
        progress = _TRANSITIONS.get(key)
        if progress is None:
            progress = _remember(_TRANSITIONS, key, {"index":0,"value":0.0,"rows":[]}, 2048)
        try:
            for index in range(progress["index"], len(TILE_VALUES)):
                self.check()
                face, copies = TILE_VALUES[index], unseen[index]
                added = _add(hand, index)
                if shanten == 0:
                    if not possible_shape(added, self.melds):
                        progress["index"] = index+1
                        continue
                    win = self.win(added, face, heavenly=heavenly)
                    next_shanten = -1
                else:
                    if not copies or self.shanten(added) != shanten-1:
                        progress["index"] = index+1
                        continue
                    win, next_shanten = None, shanten-1
                row = next((r for r in progress["rows"] if r["tile"] == face), None)
                if row is None:
                    row = {"tile":face,"remaining_count":copies,"empty":copies == 0,
                           "next_shanten":next_shanten,"win":win,"value":None,"contribution":None}
                    progress["rows"].append(row)
                if not allowed or not copies or win is not None and not win["qualifies"]:
                    following = 0.0
                elif shanten == 0:
                    following = float(win["value"])
                else:
                    following = self.value14(added, _remove(unseen,index), None if draws is None else draws-1, face)
                contribution = copies/total*following if total else 0.0
                row.update(value=following, contribution=contribution)
                progress["value"] += contribution
                progress["index"] = index+1
            _TRANSITIONS.pop(key, None)
            return progress["value"]
        finally:
            if records is not None:
                records[:] = deepcopy(progress["rows"])

    def value13(self, hand, unseen, draws, *, shanten=None):
        key = self.melds, self.min_fan, hand, unseen, draws
        if key not in self.cache:
            self.check()
            self.nodes += 1
            n = self.shanten(hand) if shanten is None else shanten
            _remember(self.cache, key, self.transitions(hand, unseen, draws, n), 8192)
        return self.cache[key]

    def facts13(self, hand, unseen, draws, n, destination, *, heavenly=False):
        # Always show structural waits, including zero-stock ones, even when
        # there is no time left for scoring.
        return self.transitions(hand, unseen, draws, n, destination, heavenly=heavenly)

    def run(self):
        started = time.monotonic()
        try:
            n = self.shanten(self.hand)
            self.result["shanten"] = n
            if sum(self.hand) == self.size-1:
                self.result["value"] = self.facts13(self.hand,self.unseen,self.draws_left,n,
                                                    self.result["improvements"],heavenly=self.is_heavenly)
            else:
                self.result["win"] = self.win(self.hand,self.winning_tile,heavenly=self.is_heavenly) if self.self_draw_allowed else None
                if self.result["win"] and self.result["win"]["qualifies"]:
                    self.result["value"] = self.result["win"]["value"]
                else:
                    options = []
                    for index, count in enumerate(self.hand):
                        if not count:
                            continue
                        row = next((r for r in self.result["candidates"] if r["discard"] == TILE_VALUES[index]),None)
                        if row is None:
                            row = {"discard":TILE_VALUES[index],"shanten":None,"eligible":None,
                                   "value":None,"complete":False,"improvements":[]}
                            self.result["candidates"].append(row)
                        after = _remove(self.hand,index)
                        if row["shanten"] is None:
                            row["shanten"] = self.shanten(after)
                        options.append((row,after))
                    minimum = min(row["shanten"] for row, _ in options)
                    for row, _ in options:
                        row["eligible"] = row["shanten"] == minimum
                    for row, after in options:
                        if not row["eligible"] or row["complete"]:
                            continue
                        row["value"] = self.facts13(after,self.unseen,self.draws_left,minimum,row["improvements"])
                        row["complete"] = True
                    self.result["value"] = max(row["value"] for row, _ in options if row["eligible"])
            self.result["complete"] = True
            self.result.pop("message",None)
        except _Interrupted:
            self.result["message"] = "正在继续比较出牌；本轮已保存候选和分支进度，可接着计算。"
        elapsed = (time.monotonic()-started)*1000
        self.result.update(nodes=self.nodes,elapsed_ms=elapsed,passes=self.passes,
                           total_nodes=self.total_nodes+self.nodes,total_elapsed_ms=self.total_ms+elapsed)
        if self.result["complete"]:
            _RUNS.pop(self.resume_key,None)
        else:
            _remember(_RUNS,self.resume_key,{"result":self.result,"passes":self.passes,
                      "total_nodes":self.result["total_nodes"],"total_elapsed_ms":self.result["total_elapsed_ms"]},8)
        return deepcopy(self.result)


def analyze_position(command, *, seconds=10):
    return Calculator(*validate_position(command), seconds=min(seconds, 2) if command.get("quick", False) else seconds).run()


def explain_position(command):
    hand, unseen, _, _, _, melds, _, _ = validate_position(command)
    tile = command.get("tile")
    if sum(hand) != 13 - 4 * len(melds) or type(tile) not in (int, str) or tile not in TILE_COUNTS:
        raise ValueError("分组解答须提供打牌后的手牌及一张待牌")
    tiles = [create_tile_from_value(v, winning=False) for v in values_from_counts(hand)]
    tiles.append(create_tile_from_value(tile, winning=True))
    fixed = [MeldedGroup([create_tile_from_value(v) for v in m.tiles], m.group_type) for m in melds]
    source = Hand(melded_groups=fixed, hand_tiles=tiles, winning_tile=tiles[-1])
    solutions = [{"family": "算术麻将", "groups": [_group_view(g, "算术麻将") for g in groups]}
                 for groups in _ARITHMETIC.all_win_groups(source)]
    all_tiles = tiles + [t for group in fixed for t in group]
    checks = ([] if fixed else [("传统麻将", _TRADITIONAL.can_win_traditional)]) + [
        ("连七对", _SPECIAL.can_win_lian_qi_dui), ("七小对", _SPECIAL.can_win_qi_xiao_dui), ("清龙", _SPECIAL.can_win_qing_long)]
    for family, check in checks:
        won, groups = check(all_tiles)
        if won:
            solutions.append({"family": family, "groups": [_group_view(g, family) for g in groups]})
    if not solutions:
        raise ValueError("该牌不能构成成牌分组")
    result = {"tile": tile, "solutions": solutions}
    result["remaining_count"] = unseen[TILE_INDEX[tile]]
    result["empty"] = result["remaining_count"] == 0
    return result


class CalculatorService:
    """One lazily started CPU worker; no game lock and no unbounded task queue."""
    def __init__(self, *, executor=None):
        self.executor, self.pending = executor, None
        self.lock, self.cache = RLock(), OrderedDict()
        self.closed = False

    async def calculate(self, command):
        position = validate_position(command)
        action = command.get("action", "analyze")
        if type(action) is not str or action not in {"analyze", "explain"}:
            raise ValueError("未知的计算操作")
        if action == "explain" and (type(command.get("tile")) not in (int, str) or command["tile"] not in TILE_COUNTS):
            raise ValueError("请指定合法待牌")
        key = action, position, command.get("tile") if action == "explain" else None
        with self.lock:
            if self.closed:
                raise ValueError("计算服务已关闭")
            if key in self.cache:
                return self.cache[key]
            if self.pending is not None and not self.pending.done():
                raise CalculationBusy("正在完成上一项分析，请稍候再试")
            if self.executor is None:
                self.executor = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"),
                                                    initializer=_worker_initializer)
            function = analyze_position if action == "analyze" else explain_position
            self.pending = future = self.executor.submit(function, command)
        result = await asyncio.shield(asyncio.wrap_future(future))
        if action == "explain" or result["complete"]:
            with self.lock:
                self.cache[key] = result
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
        return result

    def close(self):
        with self.lock:
            self.closed = True
            executor, self.executor = self.executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
