"""Independent listening exercises: uniform physical draws, no fan threshold."""

from collections import Counter, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from itertools import permutations
import asyncio
import math
import multiprocessing as mp
import random
from threading import RLock
import time
from uuid import uuid4

from calculator_base.arithmetic_mahjong import ArithmeticMahjongChecker
from calculator_base.constants import TILE_COUNTS
from calculator_base.hand_structure import Hand, create_tile_from_value
from calculator_base.special_winning_checker import SpecialWinningChecker
from calculator_base.traditional_mahjong import TraditionalMahjongChecker
from bots.common_bot_helper.observation import TILE_INDEX, TILE_VALUES
from bots.math_bot_helper.features import counts_from_values, possible_shape


PHYSICAL_TILES = tuple(value for value, count in TILE_COUNTS.items() for _ in range(count))
assert len(PHYSICAL_TILES) == 108
_ARITHMETIC = ArithmeticMahjongChecker()
_TRADITIONAL = TraditionalMahjongChecker()
_SPECIAL = SpecialWinningChecker()
FINISHED = {"CORRECT", "INCORRECT", "TIMED_OUT", "REVEALED"}


def validate_hand(values):
    if len(values) != 13 or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in values):
        raise ValueError("练习手牌须为 13 张合法牌")
    if any(count > TILE_COUNTS[value] for value, count in Counter(values).items()):
        raise ValueError("手牌超出牌库中的实际张数")


def find_waits(values):
    """All distinct winning-shape values, including empty waits, without fan."""
    validate_hand(values)
    counts = list(counts_from_values(values))
    waits = []
    for index, value in enumerate(TILE_VALUES):
        counts[index] += 1
        if possible_shape(tuple(counts)):
            waits.append(value)
        counts[index] -= 1
    return tuple(waits)


def acceptance_probability(wait_count):
    return 0.01 if wait_count == 0 else 0.10 if wait_count == 1 else 1.0


def _group_view(group, family):
    group = list(group)
    values = [tile.value for tile in group]
    label = "对子" if len(group) == 2 else "刻子" if len(set(values)) == 1 else "顺子"
    if family == "清龙" and len(group) == 12:
        label = "十二连数"
    elif family == "算术麻将" and len(group) == 4 and len(set(values)) > 1:
        operator = next(tile for tile in group if isinstance(tile.value, str))
        numbers = [tile for tile in group if isinstance(tile.value, int)]
        for left, right, result in permutations(numbers):
            total = left.value + right.value if operator.value == "+" else left.value * right.value
            if total == result.value:
                group = [left, operator, right, result]
                label = f"{left.value} {operator.value} {right.value} = {result.value}"
                break
    return {"label": label, "tiles": [{"value": tile.value, "winning": bool(tile.winning)} for tile in group]}


def explain_wait(values, winning_value):
    """Return every arithmetic decomposition and witnesses for other families."""
    validate_hand(values)
    tiles = [create_tile_from_value(value, winning=False) for value in values]
    tiles.append(create_tile_from_value(winning_value, winning=True))
    hand = Hand(hand_tiles=tiles, winning_tile=tiles[-1])
    solutions = []
    for groups in _ARITHMETIC.all_win_groups(hand):
        solutions.append({"family": "算术麻将", "groups": [_group_view(group, "算术麻将") for group in groups]})
    for family, check in (
        ("传统麻将", _TRADITIONAL.can_win_traditional),
        ("连七对", _SPECIAL.can_win_lian_qi_dui),
        ("七小对", _SPECIAL.can_win_qi_xiao_dui),
        ("清龙", _SPECIAL.can_win_qing_long),
    ):
        won, groups = check(tiles)
        if won:
            solutions.append({"family": family, "groups": [_group_view(group, family) for group in groups]})
    if not solutions:
        raise ValueError("待牌分析与分组结果不一致")
    remaining = max(0, TILE_COUNTS[winning_value] - values.count(winning_value))
    return {"tile": winning_value, "remaining_count": remaining, "empty": remaining == 0, "solutions": solutions}


def generate_question(rng=None, *, max_seconds=5):
    rng = rng or random.Random()
    deadline = time.monotonic() + max_seconds
    attempts = 0
    while time.monotonic() < deadline:
        # sample() selects positions in the complete 108-tile population;
        # repeated values remain distinct physical copies, without replacement.
        values = tuple(rng.sample(PHYSICAL_TILES, 13))
        attempts += 1
        waits = find_waits(values)
        probability = acceptance_probability(len(waits))
        if probability < 1 and rng.random() >= probability:
            continue
        answers = tuple(explain_wait(values, value) for value in waits)
        return {"hand": values, "waits": waits, "answers": answers, "attempts": attempts}
    raise ValueError("题目生成较慢，请重新生成一题")


def _worker_initializer():
    from singleplayer.bot_worker import lower_own_priority
    lower_own_priority()


def parse_seconds(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600):
        raise ValueError("限时须为 1 到 600 秒，或选择不限时")
    return value


@dataclass
class PracticeQuestion:
    generated: dict
    seconds: float | None
    question_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "READY"
    started_at: float | None = None
    deadline: float | None = None
    selected: tuple = ()
    not_ready: bool = False
    answer_revision: int = 0


class PracticeBusy(ValueError):
    pass


class PracticeService:
    """Generation never holds the game lock or runs in its CPU policy worker."""

    def __init__(self, *, executor=None, clock=time.monotonic):
        self.executor = executor
        self.clock = clock
        self.lock = RLock()
        self.pending = None
        self.questions = OrderedDict()
        self.closed = False

    async def new_question(self, seconds=30):
        seconds = parse_seconds(seconds)
        with self.lock:
            if self.closed:
                raise ValueError("练习服务已关闭")
            if self.pending is not None and not self.pending.done():
                raise PracticeBusy("正在准备题目，请稍候再试")
            if self.executor is None:
                self.executor = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"),
                                                    initializer=_worker_initializer)
            self.pending = future = self.executor.submit(generate_question)
        generated = await asyncio.shield(asyncio.wrap_future(future))
        question = PracticeQuestion(generated, seconds)
        with self.lock:
            self.questions[question.question_id] = question
            while len(self.questions) > 128:
                self.questions.popitem(last=False)
            return self._view(question, self.clock())

    def _question(self, question_id, now):
        question = self.questions.get(question_id)
        if question is None:
            raise ValueError("练习题已失效，请生成下一题")
        if question.phase == "ANSWERING" and question.deadline is not None and now >= question.deadline:
            question.phase = "TIMED_OUT"
        return question

    def get(self, question_id):
        with self.lock:
            now = self.clock()
            return self._view(self._question(question_id, now), now)

    def start(self, question_id):
        with self.lock:
            now = self.clock()
            question = self._question(question_id, now)
            if question.phase == "READY":
                question.phase, question.started_at = "ANSWERING", now
                question.deadline = None if question.seconds is None else now + question.seconds
            return self._view(question, now)

    def answer(self, question_id, values, not_ready, revision, *, submit=False):
        if (type(values) is not list or any(type(v) not in (int, str) or v not in TILE_COUNTS for v in values)
                or len(values) != len(set(values)) or type(not_ready) is not bool
                or not_ready and values or type(revision) is not int or not 1 <= revision <= 1_000_000_000):
            raise ValueError("答案须为不重复的合法牌种，或选择不能听牌")
        if submit and not values and not not_ready:
            raise ValueError("请先选择待牌，或选择不能听牌")
        with self.lock:
            now = self.clock()
            question = self._question(question_id, now)
            if question.phase == "READY":
                raise ValueError("请先开始本题")
            if question.phase == "ANSWERING" and revision > question.answer_revision:
                question.selected = tuple(sorted(values, key=TILE_INDEX.__getitem__))
                question.not_ready, question.answer_revision = not_ready, revision
                if submit:
                    right = set(question.selected) == set(question.generated["waits"])
                    right = right and (question.not_ready == (not question.generated["waits"]))
                    question.phase = "CORRECT" if right else "INCORRECT"
            return self._view(question, now)

    def reveal(self, question_id):
        with self.lock:
            now = self.clock()
            question = self._question(question_id, now)
            if question.phase == "READY":
                raise ValueError("请先开始本题")
            if question.phase == "ANSWERING":
                question.phase = "REVEALED"
            return self._view(question, now)

    @staticmethod
    def _view(question, now):
        result = {"question_id": question.question_id, "phase": question.phase,
                  "hand": [{"tile_id": f"{question.question_id}:{index}", "value": value}
                           for index, value in enumerate(question.generated["hand"])],
                  "time_limit_seconds": question.seconds, "server_now": now,
                  "deadline": question.deadline, "selected": question.selected,
                  "not_ready": question.not_ready, "answer_revision": question.answer_revision,
                  "consider_min_fan": False}
        if question.phase in FINISHED:
            expected, selected = set(question.generated["waits"]), set(question.selected)
            result.update(waits=question.generated["waits"], answers=question.generated["answers"],
                          missed=sorted(expected - selected, key=TILE_INDEX.__getitem__),
                          extra=sorted(selected - expected, key=TILE_INDEX.__getitem__))
        return result

    def close(self):
        with self.lock:
            self.closed = True
            executor, self.executor = self.executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
