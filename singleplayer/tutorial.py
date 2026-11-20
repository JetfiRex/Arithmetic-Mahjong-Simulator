"""Six authored tutorial scripts, followed by their isolated engine lessons."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import random
from threading import RLock
import time
from uuid import uuid4

from calculator_base.constants import TILE_COUNTS
from calculator_base.special_winning_checker import SpecialWinningChecker
from simulator import ActionType, GameConfig, GameMode, GameState, TimeoutConfig
from simulator.hand_state import HandTile
from simulator.rule_adapter import HandSnapshot, WinContext, WinMethod
from simulator.state_snapshot import capture_game_state, restore_game_state



# 第一、二课：基础知识与拖牌胡牌


BASICS_HAND = (1, "+", 2, 3, 2, "×", 2, 4, 7, 7, 7, 7, 9, 9)


CHAPTERS = ("基础知识", "胡牌", "互动：吃碰杠", "传统麻将", "七小对和清龙", "血战")


STEPS = (
    {"title": "欢迎来到算数麻将", "text": "欢迎游玩算数麻将这个开放世界冒险桌游！算数麻将就是用麻将凑齐算式胡牌！", "highlight": [], "visual": "welcome"},
    {"title": "我们一步步看", "text": "我知道你想学会，但是先不着急，我们一步步看。先认识眼前这一副牌。", "highlight": [], "visual": "welcome"},
    {"title": "用 1 演示", "text": "每张数字牌都代表一个数字。我们先拿这张 1 来看看。", "highlight": [0], "visual": "tile", "tile": 1},
    {"title": "左上角是牌值", "text": "左上角的 1 代表它是 1。简单版有数字 0 到 20，以及 24。", "highlight": [0], "visual": "value", "tile": 1},
    {"title": "还有符号牌", "text": "1 右边这张牌的 + 代表符号加号。这个版本中有 + 和 × 两种符号。", "highlight": [1], "visual": "value", "tile": "+"},
    {"title": "右下角是牌池张数", "text": "1 右下角的 4 代表这副牌里一共有 4 张 1。这里写的是固定张数，不是此刻还剩几张。", "highlight": [0], "visual": "stock", "tile": 1},
    {"title": "一共 108 张", "text": "这副牌一共有 108 张，每个数字和符号的数量不一样多。具体数量随时可以打开规则速查。", "highlight": [], "visual": "pool"},
    {"title": "第一组：加法算式", "text": "前四张牌，1+2=3，是一个算式。算式用了四张牌，等号不用占一张牌。", "highlight": [0,1,2,3], "visual": "formula", "formula": "1 + 2 = 3"},
    {"title": "第二组：乘法算式", "text": "后面的 2×2=4 也是算式。加法和乘法都是算式。", "highlight": [4,5,6,7], "visual": "formula", "formula": "2 × 2 = 4"},
    {"title": "除了算式，还有刻子", "text": "除了算式，还有刻子。和一般麻将的刻子不一样，算数麻将的普通刻子是四张相同的牌。", "highlight": [8,9,10,11], "visual": "kezi"},
    {"title": "第三组：四张 7", "text": "后面的四张7就是一个刻子。", "highlight": [8,9,10,11], "visual": "kezi"},
    {"title": "最后是一对", "text": "最后两张9是一对。这一对可以是相同的数字，也可以是相同的符号。", "highlight": [12,13], "visual": "pair"},
    {"title": "三组，加上一对", "text": "如果你能凑齐三组算式或者刻子，再加上一对，就组成了基本胡牌结构。这副牌已经凑齐啦！", "highlight": list(range(14)), "visual": "shape"},
    {"title": "试着胡牌吧", "text": "按右下角的“胡”，就可以胡啦！", "highlight": list(range(14)), "visual": "hu", "requires": "hu"},
    {"title": "你胡牌啦！", "text": "胡牌之后会弹出窗口，告诉你是怎样组成胡牌的，以及这手牌有哪些番。可以再点“查看胡牌结果”重看。", "highlight": list(range(14)), "visual": "result"},
    {"title": "认识这些番", "text": "这个牌目前有这些番。更多请查阅规则书哦！", "highlight": [], "visual": "fans"},
    {"title": "认识这些番2", "text": "你也可以直接打开“规则速查”的番数表，查询每个番是什么意思。", "highlight": [], "visual": "fans"},
)


HU_STEP = 13


SORT_HELP_SECONDS = 30


SORT_HAND = (2, 2, 3, 5, 12, 14, 14, 14, 14, 24, "+", "+", "+")


QUAD_ORDER = (14, 14, 14, 14, 2, 2, 3, 5, 12, 24, "+", "+", "+")


FORMULA_ORDER = (14, 14, 14, 14, 2, "+", 3, 5, 2, 12, 24, "+", "+")


REGROUP_ORDER = (2, "+", 3, 5, 2, "+", 12, 14, 14, 14, 14, "+", 24, 10)


SORT_STEPS = (
    {"title": "接着认识胡牌", "text": "上一步我们知道了基础知识。现在换一副固定的手牌，继续看看怎么胡牌。", "visual": "sort_intro"},
    {"title": "学习拖牌整理", "text": "下一步我们来学习拖牌整理，这是非常重要的一步。", "visual": "sort_intro"},
    {"title": "整理，才能看清分组", "text": "通过拖牌，我们就能看清手里的牌要怎么凑算式、听什么牌。我们一点一点整理。", "visual": "sort_intro"},
    {"title": "先把四张 14 放到最左边", "text": "首先看到的是四张14。使用拖牌，把它们一张一张拖到最左边吧！这一段只能拖动尚未整理的 14。", "visual": "sort_quad", "requires": "sort_quad"},
    {"title": "这么多加号，来凑加法", "text": "四张14已经整理好了。然后我们看到这么多加号，就知道要凑加法啦！", "visual": "many_plus"},
    {"title": "把 2 + 3 = 5 排到前面", "text": "注意到2+3=5，我们就可以凑出这个算式！把 2、+、3、5 排到刻子右边、剩余手牌的最左侧。", "visual": "sort_formula", "requires": "sort_formula"},
    {"title": "诶，这里应该听乘法", "text": "然后我们还有……诶？2、12、24，这里应该是听乘法。旁边还剩两张加号，正好是一对。", "visual": "wait_multiply"},
    {"title": "等一张乘号", "text": "按现在的分组，2 × 12 = 24 只差一张乘号。于是等到一张 ×，就能胡啦！", "visual": "wait_multiply"},
    {"title": "接下来来一张……", "text": "好，接下来我们来了一张……点击“摸一张牌”，看看固定来牌是什么。", "visual": "draw_prompt", "requires": "draw"},
    {"title": "为什么 10 也能胡？", "text": "来了一张 10。咦？为什么右下角也显示了“胡”？", "visual": "surprise_ten"},
    {"title": "哦！还能这么安排", "text": "哦！因为我还能这么安排！看看这一套不同的分组：2+3=5，2+12=14，两张14当对子，剩下14、+、24。", "visual": "regroup"},
    {"title": "10 + 14 = 24", "text": "这样安排就看出来了：10+14=24，原来这套分组也听一张10！", "visual": "wait_ten"},
    {"title": "亲手确认这次胡牌", "text": "这一次就用摸来的 10 胡牌吧！点击右下角的“胡”，再看看完整分组。如果刚才已经试过，可以查看胡牌结果后继续。", "visual": "wait_ten", "requires": "hu"},
    {"title": "分组，让听牌五花八门", "text": "根据不同的分组，听牌也会变得五花八门。这正是算术麻将的精髓哦～", "visual": "regroup"},
    {"title": "记住你自己的分组", "text": "在实际打牌中，这个分组得靠你自己拖牌分哦。", "visual": "remember_groups"},
    {"title": "记住你自己的分组2", "text": "组和组之间现在是因为演示才分得很开，实际对局里你得记住自己的分组哦～！", "visual": "remember_groups"},
)


# 第三课：吃碰杠互动


INTERACTION_HAND = (5, 5, 5, 4, 4, 4, 4, 6, 7, 8, 18, 19, "+")


INTERACTION_STEPS = (
    {"title":"已经打了几回合", "text":"现在我们已经打了几回合牌了。看看大家的弃牌，再看看自己的手牌。", "visual":"interaction_intro"},
    {"title":"先找凑牌方向", "text":"我们可以想到4+4=8这个方向，不过四张4已经是一个刻子。", "visual":"interaction_shape"},
    {"title":"还差第四张 5", "text":"要是这三张5能再来第四张5就好了。看看其他人会打出什么。", "visual":"peng_wish"},
    {"title":"对家打出了 5", "text":"下家出牌后，好！我们看到了对家打出的5。", "visual":"peng_offer"},
    {"title":"我们想要这张牌", "text":"我们想要这个5。手里已有三张同样的牌，这个时候，就可以“碰”。", "visual":"peng_offer"},
    {"title":"试着碰牌", "text":"按右下角的“碰”，把手里的三张5和对家打出的5组成四张的刻子。", "visual":"peng_offer", "requires":"peng"},
    {"title":"碰后，直接出牌", "text":"好！我们碰到了。碰牌后，这一回合不再摸牌，直接从手里打出一张。", "visual":"after_peng"},
    {"title":"先打出 18", "text":"然后我们看这个18和19有点大了。先点击手里的18，把它打出去。", "visual":"discard_eighteen", "requires":"discard", "discard":18},
    {"title":"上家打出了 2", "text":"下家、对家依次出牌，上家又打出了一个2。我们想要这张2，可以用它和手里的6、+、8组成2+6=8。", "visual":"chi_offer"},
    {"title":"试着吃牌", "text":"这次我们可以点右下角的“吃”，吃下上家打出的2。", "visual":"chi_offer", "requires":"chi_blocked"},
    {"title":"诶？怎么回事？", "text":"诶？怎么这张2就被对家拿走了？哦，对家也想要它，并且碰了四张 2。", "visual":"chi_blocked"},
    {"title":"碰，比吃优先", "text":"如果吃和碰同时发生，就不能吃，碰优先。", "visual":"chi_blocked"},
    {"title":"碰，比吃优先2", "text":"所以刚才不是bug，而是对家的碰优先拿到了这张牌。", "visual":"chi_blocked"},
    {"title":"又来了一张 2", "text":"对家碰完出牌，上家又打出了一张2。好，我们再吃一次！", "visual":"chi_again"},
    {"title":"再点一次吃", "text":"点击右下角的“吃”，再试一次吧。", "visual":"chi_again", "requires":"chi"},
    {"title":"选哪一种吃法？", "text":"接下来选择吃法：4+2=6，还是6+2=8？点一种试试看。", "visual":"chi_choice", "requires":"chi_detail"},
    {"title":"保住四张 4", "text":"如果选4+2=6，四张4就被拆成三张，剩下的7、8也不好处理。选6+2=8后，四张4仍是一个刻子。", "visual":"after_chi"},
    {"title":"再打出 19", "text":"好！接下来点击手里的 19，把它打出去。", "visual":"discard_nineteen", "requires":"discard", "discard":19},
    {"title":"现在听 7", "text":"两组已经明了出来，手上只剩四张4和一张7。再来一张7，就能凑成对子胡牌啦！", "visual":"wait_seven"},
    {"title":"怎么摸来第五张 4？", "text":"其他三家各出了一张牌，我们摸到了4。现在手里有五张4，但是我想要的是7！", "visual":"gang_offer"},
    {"title":"还可以杠", "text":"我们可以“杠”！算数麻将用五张相同的牌组成一个杠。", "visual":"gang_offer"},
    {"title":"杠后，再摸一张", "text":"杠了以后，就会多摸一张牌。算数麻将没有单独的杠补牌堆，补牌也从同一个牌山里摸。", "visual":"gang_offer"},
    {"title":"暗杠和明杠", "text":"现在五张4都在自己手里，这样的杠叫“暗杠”。", "visual":"gang_types"},
    {"title":"暗杠和明杠2", "text":"如果自己有四张4、别人再打出一张4，拿那张牌来杠，就叫“明杠”。", "visual":"gang_types"},
    {"title":"亲手杠一次", "text":"点击右下角的“杠”，把手里的五张4暗杠，再多摸一张。", "visual":"gang_offer", "requires":"gang"},
    {"title":"摸到了 7！", "text":"摸到了7！好，我们可以胡了！点击右下角的“胡”，看看这次有吃、有碰、也有杠的完整分组。", "visual":"interaction_hu", "requires":"hu"},
    {"title":"吃、碰、杠，学会啦", "text":"更多互动规则也可以随时查询规则速查。", "visual":"interaction_done"},
)


# 第四课：传统麻将


TRADITIONAL_HAND = (1, "+", 2, 3, 2, "+", 3, 5, 4, 4, 1, 3, 3)


TRADITIONAL_ORDER = (1, 2, 3, 1, 2, 3, 3, 4, 5, 3, 4, "+", "+")


TRADITIONAL_STEPS = (
    {"title":"这次学习传统麻将", "text":"这次我们来学习传统麻将。我们已经打了几回合，现在看看这副理好的手牌。", "visual":"traditional_intro"},
    {"title":"为什么保留麻将牌面？", "text":"我们保留了麻将的牌面，这样你才知道你打的是麻将。牌面除了好看，也藏着另一种胡牌方法。", "visual":"traditional_faces"},
    {"title":"有一个番，叫传统麻将", "text":"有一个“传统麻将”。按麻将牌面凑成四组三张的顺子或刻子，再加上一对，也是一种胡法。", "visual":"traditional_shape"},
    {"title":"先看算式分组", "text":"现在手牌理好了。按眼前这套算式分组，最后的1、3、3可以补成1×3=3，所以你应该看出是在等一张×。", "visual":"arithmetic_wait"},
    {"title":"乘号点胡，是 2 番", "text":"如果别人打出或者自己摸到一张乘号，我们就能按算术麻将胡。", "visual":"arithmetic_fan"},
    {"title":"换一种理法看看", "text":"但是，我们可以换一种理法。要不要按照一般的麻将牌面理牌？", "visual":"traditional_question"},
    {"title":"换一种理法看看2", "text":"点击“按传统麻将理牌”，看看还是这十三张牌，会有什么不同。", "visual":"traditional_question", "requires":"regroup"},
    {"title":"同一批牌，新的分组", "text":"现在自动整理成：一二三条、一二三条、三四五条、三四条，最后两张红中是一对。", "visual":"traditional_regroup"},
    {"title":"熟悉麻将的朋友看出来了吗？", "text":"熟悉麻将的朋友都知道，现在按这套传统麻将分组，胡二、五条。", "visual":"traditional_wait"},
    {"title":"来一张 2 或 5，就能胡", "text":"也就是说，来一张2或者5，就可以把三四条补成一组顺子。", "visual":"traditional_wait"},
    {"title":"上家打出了 2", "text":"好，上家打出一个2。点击右下角的“胡”，就可以胡了！", "visual":"traditional_ron", "requires":"hu"},
    {"title":"提示能胡，不一定已经看出胡法", "text":"记住：当系统提示可以胡的时候，有可能你自己还不知道怎么胡。别着急，结果里会告诉你完整的分组。", "visual":"traditional_result"},
    {"title":"算式，不止你排的这一种", "text":"一方面，能胡的算式不止你眼前排列的这一种。换一种分组，可能就会发现新的胡法。", "visual":"traditional_result"},
    {"title":"另一方面，也可能是传统麻将", "text":"另一方面，也可能是传统麻将。刚才上家的2，就和三四条凑成了二三四条。", "visual":"traditional_result"},
    {"title":"看到能胡，先点胡看看", "text":"总之，看到系统提示可以胡，先点击“胡”！然后你就能看到究竟是怎么组成的。也可以再点“查看胡牌结果”重看。", "visual":"traditional_result"},
    {"title":"传统麻将，这一项是 1 番", "text":"然后你可以看到“传统麻将”这一项有1番。这副牌另有“全一位”2番，合计3番", "visual":"traditional_fans"},
    {"title":"算式不好凑时，换个角度", "text":"当你手里的算式不好凑时，也试试传统麻将吧！保留麻将牌面，正是为了让你还能从另一个角度看牌。", "visual":"traditional_finish"},
)


# 第五课：七小对和清龙


SPECIAL_HANDS = (
    (2,2,3,3,5,5,6,6,7,7,8,8,9,9),
    (3,3,4,4,5,5,6,6,7,7,8,8,9,9),
    (1,2,3,4,5,6,7,8,9,10,11,12,16,16),
)


SPECIAL_NAMES = ("七小对", "连七对", "清龙")


SPECIAL_STEPS = (
    {"title":"两个特殊胡法", "text":"最后我们来看两个特殊胡法。", "visual":"special_intro", "example":0},
    {"title":"七小对", "text":"一个是七小对，你的手牌只需要是七个不同的对子即可。", "visual":"special_pairs", "example":0},
    {"title":"连七对", "text":"如果七个对子是连续的，那就是连七对。", "visual":"special_linked", "example":1},
    {"title":"清龙", "text":"最后是清龙，你需要12个连续的数字加上一对。", "visual":"special_dragon", "example":2},
    {"title":"仅供参考", "text":"这种特殊胡法不作为主要的胡法，仅供参考。", "visual":"special_reference", "example":2},
    {"title":"梦想胡法", "text":"因为胡法比较梦想，于是番数也不小。", "visual":"special_finish", "example":2},
)


# 第六课：血战


BLOOD_HAND = (1,"+",2,3, 2,"×",2,4, 7,7,7,7, 9,9)


BLOOD_OPPOSITE_HAND = (10,"+",10,20, 12,"+",12,24, 10,"+",14,24, 16,16)


BLOOD_STEPS = (
    {"title":"最后，学习血战模式", "text":"最后我们来学习血战模式。", "visual":"blood_intro"},
    {"title":"三个人胡，或者流局", "text":"和一般的模式不一样，血战要三个人胡牌，或者流局，才能结束。", "visual":"blood_end_rule"},
    {"title":"现在你自摸了", "text":"现在你自摸了，点击右下角的“胡”。", "visual":"blood_self_hu", "requires":"hu"},
    {"title":"三家都要赔付", "text":"胡牌了以后，因为你是自摸，于是你会拿到另外三个人的赔付。", "visual":"blood_first_payment"},
    {"title":"但是，游戏没有结束", "text":"但是游戏没有结束。你已经胡牌退出，剩下三家继续打。", "visual":"blood_continue"},
    {"title":"对家也自摸了", "text":"又打了几个回合。现在，你的对家自摸了。", "visual":"blood_opposite_hu"},
    {"title":"你不用再赔了", "text":"因为你自己先胡，后面其他人的自摸都不会让你赔付。看看这一次的赔付里，已经没有你了。", "visual":"blood_exempt"},
    {"title":"最后，就剩两个人", "text":"最后就是两个人。这个时候，如果有人自摸，就是另一个仍在打牌的人赔付。", "visual":"blood_two_left"},
    {"title":"现在流局了", "text":"剩下两家又打了几个回合。现在牌山摸完，流局了。", "visual":"blood_draw"},
    {"title":"看看整局赔付", "text":"看看这一局的赔付：你的自摸由三家赔，对家的自摸只由当时还没胡的两家赔。流局后，前面两次胡牌的结算仍然保留。", "visual":"blood_settlement"},
    {"title":"先胡，还是等高番？", "text":"血战如果先胡，就不会赔后面的；而后胡，则可能有机会胡高番的牌。", "visual":"blood_finish"},
)


# 共用剧情与快照辅助


def _step_index(steps, visual):
    """Locate a story event independently of how its narration is split."""
    return next(index for index, step in enumerate(steps) if step["visual"] == visual)


def arrange_wall(values, hands, draws):
    """Reserve all authored tiles before filling the remaining dealt hands."""
    remaining = list(values)
    for value in [v for hand in hands for v in hand] + list(draws):
        remaining.remove(value)
    for hand in hands:
        while len(hand) < 13:
            hand.append(remaining.pop(0))
    deal = [hands[seat][index] for index in range(13) for seat in range(4)]
    values[:] = reversed([*deal, *draws, *remaining])


class ScriptedLesson:
    def __init__(self, wall, *, warmup_turns, config=None):
        self.identifier, self.revision, self.step = str(uuid4()), 0, 0
        self.completed, self.result, self.notice = False, None, ""
        self.game = GameState.new_game(player_names=["我", "示范下家", "示范对家", "示范上家"],
                                       rng=wall, config=config or GameConfig(hand_preparation_seconds=0),
                                       timeout_config=TimeoutConfig(), now=0)
        for _ in range(warmup_turns):
            self._opponent_turn()
        self.checkpoints = {0:self._checkpoint()}

    def _checkpoint(self):
        return {"game":capture_game_state(self.game, now=0), "result":deepcopy(self.result), "notice":self.notice}

    def _previous(self):
        if self.step == 0:
            raise ValueError("已经是第一段讲解")
        target = self.step - 1
        saved = self.checkpoints[target]
        self.game = restore_game_state(saved["game"], now=0)
        self.result, self.notice, self.step = deepcopy(saved["result"]), saved["notice"], target
        self.checkpoints = {i:s for i,s in self.checkpoints.items() if i <= target}

    def _responses(self, requests=None):
        requests = requests or {}
        window = self.game.response_window
        if window is None:
            raise ValueError("示范响应窗口未开启")
        for player_id in list(window.players()):
            if player_id not in window.requests_by_player and player_id not in window.auto_passed_players:
                self.game.submit_response(player_id, requests.get(player_id, ActionType.PASS), now=0, source_window_id=window.window_id)

    def _discard(self, player_id, *, value=None, tile_id=None):
        game = self.game
        if any(o.action_type in (ActionType.AN_GANG, ActionType.JIA_GANG) for o in game.turn_options_by_player.get(player_id, [])):
            game.submit_turn_action(player_id, ActionType.PASS, now=0, source_turn_window_id=game.turn_window_id)
        if tile_id is not None:
            game.discard_tile_by_id(player_id, tile_id, now=0, source_turn_window_id=game.turn_window_id)
        else:
            game.discard_tile(player_id, value, now=0, source_turn_window_id=game.turn_window_id)

    def _opponent_turn(self, discard=None, *, respond=True):
        game = self.game
        player_id = game.current_player().player_id
        game.draw_tile(player_id, now=0)
        if discard is None:
            self._discard(player_id, tile_id=game.drawn_tile_id)
        else:
            self._discard(player_id, value=discard)
        if respond:
            self._responses()


# 第三课：吃碰杠互动运行逻辑


class _InteractionWall(random.Random):
    def shuffle(self, values):
        # Reserve the two 2 discards and the opponent's three matching 2s.
        # There are exactly six 2s in the real pool; no demonstration copies.
        hands = [list(INTERACTION_HAND), [], [2,2,2,5], [2,2]]
        draws = [0,1,3,6,8,9,10,11,12, 13,14, 15,16,17, 18, 19,20,24,4,7]
        arrange_wall(values, hands, draws)


class InteractionLesson(ScriptedLesson):
    def __init__(self):
        # Two full rounds and our next discard have actually happened. The
        # authored thirteen are preserved by these fixed tsumogiri turns.
        super().__init__(_InteractionWall(0), warmup_turns=9)

    def _enter(self, step):
        self.notice = ""
        if step == _step_index(INTERACTION_STEPS, "peng_offer"):
            self._opponent_turn()
            self._opponent_turn(5, respond=False)
        elif step == _step_index(INTERACTION_STEPS, "chi_offer"):
            self._opponent_turn(); self._opponent_turn(); self._opponent_turn(2, respond=False)
        elif step == _step_index(INTERACTION_STEPS, "chi_again"):
            # P2 owns the turn after its PENG: discard without drawing.
            self._discard("p2", value=self.game.get_player("p2").hand_tiles[0]); self._responses()
            self._opponent_turn(2, respond=False)
        elif step == _step_index(INTERACTION_STEPS, "gang_offer"):
            for _ in range(3): self._opponent_turn()
            assert self.game.draw_tile("p0", now=0) == 4
        self.step = step
        self.checkpoints[step] = self._checkpoint()

    def dispatch(self, action, command):
        step = INTERACTION_STEPS[self.step]
        requirement = step.get("requires")
        if action == "previous":
            self._previous()
        elif action == "next":
            if requirement:
                raise ValueError("请先按提示完成这一步的吃、碰、杠、出牌或胡牌")
            if self.step == len(INTERACTION_STEPS)-1:
                raise ValueError("已经是最后一段讲解")
            self._enter(self.step+1)
        elif action == "peng" and requirement == "peng":
            self._responses({"p0":ActionType.PENG})
            self._enter(self.step+1)
        elif action == "chi" and requirement in ("chi_blocked", "chi"):
            requests = {"p0":ActionType.CHI}
            if requirement == "chi_blocked": requests["p2"] = ActionType.PENG
            self._responses(requests)
            self._enter(self.step+1)
        elif action == "chi_detail" and requirement == "chi_detail":
            detail = self.game.pending_action_detail
            option = next((o for o in detail.payload_options if o.get("option_id") == command.get("option_id")), None)
            if option is None:
                raise ValueError("请选择当前列出的吃法")
            if Counter(option["required_values"]) != Counter((6,"+",8)):
                self.notice = "这个吃法会拆开四张 4，留下三张 4 和 7、8。先不急着吃，再试试能保留刻子的 6 + 2 = 8 吧！"
            else:
                if not self.game.submit_chi_detail("p0", option_id=option["option_id"], detail_id=detail.detail_id, now=0):
                    raise ValueError("示范吃法未能完成")
                self._enter(self.step+1)
        elif action == "discard" and requirement == "discard":
            identifier = command.get("tile_id")
            player = self.game.get_player("p0")
            if type(identifier) is not str or identifier not in player.hand_tile_ids or player.tile_value(identifier) != step["discard"]:
                raise ValueError(f"这一段请点击手里的 {step['discard']} 出牌")
            self._discard("p0", tile_id=identifier); self._responses()
            self._enter(self.step+1)
        elif action == "gang" and requirement == "gang":
            self.game.submit_turn_action("p0", ActionType.AN_GANG, now=0, source_turn_window_id=self.game.turn_window_id)
            assert self.game.get_player("p0").tile_value(self.game.drawn_tile_id) == 7
            self._enter(self.step+1)
        elif action == "hu" and requirement == "hu":
            self.game.submit_turn_action("p0", ActionType.HU, now=0, source_turn_window_id=self.game.turn_window_id)
            self.result = self.game.settlement_events[0].to_dict()
            self._enter(self.step+1)
        elif action == "complete" and self.step == len(INTERACTION_STEPS)-1 and self.result:
            self.completed = True
        else:
            raise ValueError("请跟随当前提示操作，这一步暂时不能执行该动作")

    def view(self):
        game, step = self.game, INTERACTION_STEPS[self.step]
        player = game.get_player("p0")
        requirement = step.get("requires")
        visual = step["visual"]
        highlight = [i for i,v in enumerate(player.hand_tiles) if
                     visual == "interaction_shape" and v == 4 or
                     visual in ("peng_wish", "peng_offer") and v == 5 or
                     requirement == "discard" and v == step["discard"] or
                     visual in ("gang_offer", "gang_types") and v == 4 or
                     visual in ("wait_seven", "interaction_hu") and v == 7]
        detail = game.pending_action_detail
        drawn_id = game.drawn_tile_id if game.current_turn_seat == 0 else None
        return {"steps":INTERACTION_STEPS, "step":self.step, "completed":self.completed,
                "won":self.result is not None, "result":self.result,
                "hand":[t.to_dict() for t in player.hand_tile_records()],
                "groups":[{"start":0,"end":len(player.hand_tiles),"label":""}],
                "highlight":highlight, "drag":None, "notice":self.notice, "assisted_steps":[],
                "next_action":"complete" if self.step == len(INTERACTION_STEPS)-1 else "next",
                "next_enabled":not self.completed if self.step == len(INTERACTION_STEPS)-1 else not requirement,
                "hu_enabled":self.result is not None or requirement == "hu",
                "drawn_tile_id":drawn_id, "drawn":drawn_id is not None,
                "interaction":{"requirement":requirement, "discard":step.get("discard"),
                    "wall_remaining":game.wall.remaining_count(), "phase":game.phase.value,
                    "last_discard":game.last_discard.to_dict() if game.last_discard else None,
                    "melds":deepcopy(player.melded_groups),
                    "chi_options":deepcopy(detail.payload_options) if requirement == "chi_detail" and detail else [],
                    "history_count":len(game.discard_history)},
                "opponents":[{"name":p.name,"hand_count":len(p.hand_tiles),"discarded_tiles":p.discarded_tiles[:],
                              "melded_groups":deepcopy(p.melded_groups)} for p in game.players[1:]],
                "my_discards":player.discarded_tiles[:]}


# 第四课：传统麻将运行逻辑


class _TraditionalWall(random.Random):
    def shuffle(self, values):
        hands = [list(TRADITIONAL_HAND), [], [], [2]]
        # Two full rounds preserve the authored thirteen. Four later turns
        # end in p3 discarding its reserved 2; no replacement tile is invented.
        draws = [0,6,8,10,11,12,14,15, 16,17,18,19]
        arrange_wall(values, hands, draws)


class TraditionalLesson(ScriptedLesson):
    def __init__(self):
        super().__init__(_TraditionalWall(0), warmup_turns=8)
        # A hypothetical discard comparison, evaluated without changing the
        # game, stock, scores or pending responses.
        self.arithmetic_preview = self.game.rule_adapter.evaluate_win(
            HandSnapshot.from_player(self.game.get_player("p0")),
            WinContext("p0", "×", WinMethod.DISCARD, source_player_id="p3"),
        ).to_dict()

    def _enter(self, step):
        self.notice = ""
        if step == _step_index(TRADITIONAL_STEPS, "traditional_regroup"):
            player = self.game.get_player("p0")
            if Counter(player.hand_tiles) != Counter(TRADITIONAL_ORDER):
                raise ValueError("示范理牌不能改变原来的十三张牌")
            for target, value in enumerate(TRADITIONAL_ORDER):
                source = next(i for i in range(target, len(player.hand_tiles)) if player.hand_tiles[i] == value)
                player.move_tile_by_id(player.hand_tile_ids[source], target)
        elif step == _step_index(TRADITIONAL_STEPS, "traditional_ron"):
            for _ in range(3):
                self._opponent_turn()
            self._opponent_turn(2, respond=False)
        self.step = step
        self.checkpoints[step] = self._checkpoint()

    def dispatch(self, action, command):
        requirement = TRADITIONAL_STEPS[self.step].get("requires")
        if action == "previous":
            self._previous()
        elif action == "next":
            if requirement:
                raise ValueError("请先按提示理牌或点击右下角的“胡”")
            if self.step == len(TRADITIONAL_STEPS)-1:
                raise ValueError("已经是最后一段讲解")
            self._enter(self.step+1)
        elif action == "regroup" and requirement == "regroup":
            self._enter(self.step+1)
        elif action == "hu" and requirement == "hu":
            self._responses({"p0":ActionType.HU})
            self.result = self.game.settlement_events[0].to_dict()
            self._enter(self.step+1)
        elif action == "complete" and self.step == len(TRADITIONAL_STEPS)-1 and self.result:
            self.completed = True
        else:
            raise ValueError("请跟随当前讲解操作，这一步暂时不能执行该动作")

    def view(self):
        game, step = self.game, TRADITIONAL_STEPS[self.step]
        player = game.get_player("p0")
        arranged = self.step >= _step_index(TRADITIONAL_STEPS, "traditional_regroup")
        groups = ((0,3,"一二三条 · 顺子"),(3,6,"一二三条 · 顺子"),(6,9,"三四五条 · 顺子"),
                  (9,11,"等二、五条"),(11,13,"两张红中 · 对子")) if arranged else (
                  (0,4,"1 + 2 = 3"),(4,8,"2 + 3 = 5"),(8,10,"两张 4 · 对子"),(10,13,"1 × 3 = 3 · 差 ×"))
        highlight = list(range(10,13)) if step["visual"] in ("arithmetic_wait", "arithmetic_fan") else list(range(9,11)) if step["visual"] in ("traditional_wait", "traditional_ron") else []
        requirement = step.get("requires")
        return {"steps":TRADITIONAL_STEPS, "step":self.step, "completed":self.completed,
                "won":self.result is not None, "result":self.result,
                "hand":[t.to_dict() for t in player.hand_tile_records()],
                "groups":[{"start":a,"end":b,"label":label} for a,b,label in groups],
                "highlight":highlight, "drag":None, "notice":self.notice, "assisted_steps":[],
                "next_action":"regroup" if requirement == "regroup" else "complete" if self.step == len(TRADITIONAL_STEPS)-1 else "next",
                "next_enabled":not self.completed if self.step == len(TRADITIONAL_STEPS)-1 else requirement != "hu",
                "hu_enabled":self.result is not None or requirement == "hu",
                "drawn_tile_id":None, "drawn":False,
                "traditional":{"arranged":arranged, "waits":[2,5], "arithmetic_preview":deepcopy(self.arithmetic_preview)},
                "interaction":{"requirement":"hu" if requirement == "hu" else None, "discard":None,
                    "wall_remaining":game.wall.remaining_count(), "phase":game.phase.value,
                    "last_discard":game.last_discard.to_dict() if game.last_discard else None,
                    "melds":[], "chi_options":[], "history_count":len(game.discard_history)},
                "opponents":[{"name":p.name,"hand_count":len(p.hand_tiles),"discarded_tiles":p.discarded_tiles[:],
                              "melded_groups":deepcopy(p.melded_groups)} for p in game.players[1:]],
                "my_discards":player.discarded_tiles[:]}


# 第五课：七小对和清龙运行逻辑


class SpecialLesson:
    def __init__(self):
        self.identifier, self.revision, self.step = str(uuid4()), 0, 0
        self.completed = False
        self.game = None  # Complete examples only; this lesson has no live game.
        self.hands = [[HandTile.create(v).to_dict() for v in values] for values in SPECIAL_HANDS]
        checker = SpecialWinningChecker()
        checks = (checker.can_win_qi_xiao_dui, checker.can_win_lian_qi_dui, checker.can_win_qing_long)
        if not all(check(list(values))[0] for check,values in zip(checks,SPECIAL_HANDS)):
            raise ValueError("特殊胡法示例牌与当前规则不一致")

    def dispatch(self, action, command):
        if action == "previous" and self.step > 0:
            self.step -= 1
        elif action == "next" and self.step < len(SPECIAL_STEPS)-1:
            self.step += 1
        elif action == "complete" and self.step == len(SPECIAL_STEPS)-1:
            self.completed = True
        else:
            raise ValueError("这一课只需查看示例牌，使用上一步、下一步或完成即可")

    def view(self):
        example = SPECIAL_STEPS[self.step]["example"]
        groups = [{"start":i,"end":i+2,"label":"对子"} for i in range(0,14,2)] if example < 2 else [
            {"start":0,"end":12,"label":"十二个连续数字"}, {"start":12,"end":14,"label":"对子"}]
        return {"steps":SPECIAL_STEPS, "step":self.step, "completed":self.completed,
                "won":False, "result":None, "display_only":True,
                "example":{"name":SPECIAL_NAMES[example],"index":example}, "hand":self.hands[example],
                "groups":groups, "highlight":[], "drag":None, "notice":"", "assisted_steps":[],
                "next_action":"complete" if self.step == len(SPECIAL_STEPS)-1 else "next",
                "next_enabled":not self.completed if self.step == len(SPECIAL_STEPS)-1 else True,
                "hu_enabled":False, "drawn_tile_id":None, "drawn":False, "opponents":[]}


# 第六课：血战运行逻辑


class _BloodWall(random.Random):
    def shuffle(self, values):
        hands = [list(BLOOD_HAND[:-1]), [], list(BLOOD_OPPOSITE_HAND[:-1]), []]
        # Two warmup rounds, our 9, four active-seat turns, then p2's 16.
        # The rest of this same full wall is later played all the way out.
        draws = [0,3,4,6,8,11,13,15, 9, 17,18,19,0,16]
        arrange_wall(values, hands, draws)


class BloodLesson(ScriptedLesson):
    def __init__(self):
        super().__init__(_BloodWall(0), warmup_turns=8,
                         config=GameConfig(mode=GameMode.BLOOD_BATTLE, hand_preparation_seconds=0))
        if self.game.draw_tile("p0", now=0) != 9:
            raise ValueError("血战示范的固定自摸牌不一致")
        self.own_drawn_id = self.game.drawn_tile_id
        self.checkpoints[0] = self._checkpoint()

    def _enter(self, step):
        self.notice = ""
        if step == _step_index(BLOOD_STEPS, "blood_opposite_hu"):
            for _ in range(4):
                self._opponent_turn()
            if self.game.current_turn_seat != 2 or self.game.draw_tile("p2", now=0) != 16:
                raise ValueError("对家固定自摸的来牌不一致")
            self.game.submit_turn_action("p2", ActionType.HU, now=0, source_turn_window_id=self.game.turn_window_id)
        elif step == _step_index(BLOOD_STEPS, "blood_draw"):
            # The ending is a fixed illustration, not another live game.
            # Leave unused tiles in the snapshot; the existing settlements
            # supply the final ledger without another round of draw analysis.
            self.game.end_round("wall_empty")
        self.step = step
        self.checkpoints[step] = self._checkpoint()

    def dispatch(self, action, command):
        requirement = BLOOD_STEPS[self.step].get("requires")
        if action == "previous":
            self._previous()
        elif action == "next":
            if requirement:
                raise ValueError("请先亲手点击右下角的“胡”，再继续")
            if self.step == len(BLOOD_STEPS)-1:
                raise ValueError("已经是最后一段讲解")
            self._enter(self.step+1)
        elif action == "hu" and requirement == "hu":
            self.game.submit_turn_action("p0", ActionType.HU, now=0, source_turn_window_id=self.game.turn_window_id)
            self.result = self.game.settlement_events[0].to_dict()
            self._enter(self.step+1)
        elif action == "complete" and self.step == len(BLOOD_STEPS)-1 and self.game.round_result is not None:
            self.completed = True
        else:
            raise ValueError("请按当前血战教程的提示操作")

    def view(self):
        game, step = self.game, BLOOD_STEPS[self.step]
        player = game.get_player("p0")
        players = [{"player_id":p.player_id, "name":p.name, "seat":p.seat, "score":p.score,
                    "is_out":p.is_out, "is_winner":p.is_winner, "win_order":p.win_order} for p in game.players]
        return {"steps":BLOOD_STEPS, "step":self.step, "completed":self.completed,
                "won":self.result is not None, "result":self.result,
                "hand":[t.to_dict() for t in player.hand_tile_records()],
                "groups":[{"start":a,"end":b,"label":label} for a,b,label in
                          ((0,4,"1 + 2 = 3"),(4,8,"2 × 2 = 4"),(8,12,"四张 7 · 刻子"),(12,14,"两张 9 · 对子"))],
                "highlight":list(range(14)) if step.get("requires") == "hu" else [], "drag":None, "notice":"", "assisted_steps":[],
                "next_action":"complete" if self.step == len(BLOOD_STEPS)-1 else "next",
                "next_enabled":not self.completed if self.step == len(BLOOD_STEPS)-1 else not step.get("requires"),
                "hu_enabled":self.result is not None or step.get("requires") == "hu",
                "drawn_tile_id":self.own_drawn_id, "drawn":True,
                "blood_battle":{"players":players, "active_player_ids":[p.player_id for p in game.players if not p.is_out],
                    "settlement_events":[event.to_dict() for event in game.settlement_events],
                    "round_result":game.round_result.to_dict() if game.round_result else None,
                    "opposite_hand":list(game.get_player("p2").hand_tiles) if game.get_player("p2").is_winner else None},
                "interaction":{"requirement":step.get("requires"), "discard":None,
                    "wall_remaining":0 if game.round_result else game.wall.remaining_count(), "phase":game.phase.value,
                    "last_discard":game.last_discard.to_dict() if game.last_discard else None,
                    "melds":[], "chi_options":[], "history_count":len(game.discard_history)},
                "opponents":[{"name":p.name,"player_id":p.player_id,"hand_count":len(p.hand_tiles),
                    "is_out":p.is_out,"win_order":p.win_order,"score":p.score,
                    "discarded_tiles":p.discarded_tiles[:], "melded_groups":deepcopy(p.melded_groups)} for p in game.players[1:]],
                "my_discards":player.discarded_tiles[:]}


# 教程服务与前两课运行逻辑


@dataclass
class _Lesson:
    game: GameState
    identifier: str = field(default_factory=lambda: str(uuid4()))
    revision: int = 0
    step: int = 0
    completed: bool = False
    result: dict | None = None
    notice: str = ""
    failures: int = 0
    sort_started: float | None = None
    assisted_steps: set = field(default_factory=set)
    drawn_id: str | None = None
    before_draw: dict | None = None
    before_regroup: list | None = None


class _LessonWall(random.Random):
    def __init__(self, hand, draw):
        super().__init__(0)
        self.hand, self.draw = hand, draw

    def shuffle(self, values):
        # Deal the authored thirteen to p0, then the fixed draw. All 108 physical
        # copies remain accounted for, including the other three hands.
        remaining = list(values)
        for value in (*self.hand, self.draw):
            remaining.remove(value)
        others, tail = remaining[:39], remaining[39:]
        order = []
        for index, value in enumerate(self.hand):
            order.extend((value, *others[index * 3:index * 3 + 3]))
        order.extend((self.draw, *tail))
        values[:] = reversed(order)


class TutorialService:
    def __init__(self, *, clock=time.monotonic):
        self.lock = RLock()
        self.clock, self.lesson, self.sessions = clock, 0, {}

    @property
    def game(self):
        return self.sessions[self.lesson].game if self.lesson in self.sessions else None

    @property
    def current(self):
        if self.lesson not in self.sessions:
            self._start()
        return self.sessions[self.lesson]

    def _start(self):
        if self.lesson in (2,3,4,5):
            self.sessions[self.lesson] = {2:InteractionLesson, 3:TraditionalLesson, 4:SpecialLesson, 5:BloodLesson}[self.lesson]()
            return
        hand, draw = (BASICS_HAND[:-1], BASICS_HAND[-1]) if self.lesson == 0 else (SORT_HAND, 10)
        game = GameState.new_game(player_names=["我", "示范下家", "示范对家", "示范上家"],
                                  rng=_LessonWall(hand, draw), config=GameConfig(hand_preparation_seconds=0),
                                  timeout_config=TimeoutConfig(), now=0)
        if self.lesson == 0:
            game.draw_tile("p0", now=0)
        self.sessions[self.lesson] = _Lesson(game, drawn_id=game.drawn_tile_id)

    def _goal_done(self):
        s = self.current
        values = s.game.get_player("p0").hand_tiles
        requirement = (STEPS if self.lesson == 0 else SORT_STEPS)[s.step].get("requires")
        if requirement == "sort_quad":
            return values[:4] == [14] * 4
        if requirement == "sort_formula":
            return values[:4] == [14] * 4 and values[4:8] in ([2, "+", 3, 5], [3, "+", 2, 5])
        if requirement == "hu":
            return s.result is not None
        if requirement == "draw":
            return s.drawn_id is not None
        return True

    def _reorder(self, values):
        player = self.current.game.get_player("p0")
        if Counter(values) != Counter(player.hand_tiles):
            raise ValueError("示范排法不能改变任何一张牌")
        for position, value in enumerate(values):
            index = next(i for i in range(position, len(values)) if player.hand_tiles[i] == value)
            player.move_tile_by_id(player.hand_tile_ids[index], position)

    def _enter_step(self, step):
        s = self.current
        if self.lesson == 1:
            if step == 8 and s.drawn_id is not None:
                s.game = restore_game_state(s.before_draw, now=0)
                s.result, s.drawn_id, s.before_regroup = None, None, None
            if step == 9 and s.before_regroup is not None:
                player = s.game.get_player("p0")
                for index, identifier in enumerate(s.before_regroup):
                    player.move_tile_by_id(identifier, index)
                s.before_regroup = None
            if step == 10 and s.before_regroup is None:
                s.before_regroup = list(s.game.get_player("p0").hand_tile_ids)
                self._reorder(REGROUP_ORDER)
        s.step, s.failures, s.notice = step, 0, ""
        s.sort_started = self.clock() if self.lesson == 1 and SORT_STEPS[step].get("requires", "").startswith("sort_") else None

    def _assist(self):
        s = self.current
        self._reorder(QUAD_ORDER if s.step == 3 else FORMULA_ORDER)
        s.assisted_steps.add(s.step)
        s.notice = "咳咳，我们对牌面稍作修改。"

    def _move(self, command):
        s = self.current
        if self.lesson != 1 or s.step not in (3, 5) or self._goal_done():
            raise ValueError("这一段暂时不需要拖牌")
        player = s.game.get_player("p0")
        identifier, target = command.get("tile_id"), command.get("index")
        if type(identifier) is not str or identifier not in player.hand_tile_ids or type(target) is not int or not 0 <= target < len(player.hand_tiles):
            raise ValueError("请选择当前手牌和合法落点")
        source = player.hand_tile_ids.index(identifier)
        value = player.hand_tiles[source]
        leading = next((i for i, v in enumerate(player.hand_tiles) if v != 14), len(player.hand_tiles))
        allowed = value == 14 and source >= leading and 0 <= target <= leading if s.step == 3 else source >= 4 and target >= 4 and value in (2, 3, 5, "+")
        if not allowed:
            s.failures += 1
            s.notice = "这一段只能把尚未整理的 14 拖入左侧的 14 组，组内或组尾都可以。" if s.step == 3 else "先保留左边四张 14，只移动剩余的 2、3、5 和加号。"
            if s.failures >= 3 and self.clock() - s.sort_started >= SORT_HELP_SECONDS:
                self._assist()
            return
        # The tutorial owns pre-draw layout. Move identities through PlayerState
        # without fabricating a live turn or changing any physical inventory.
        player.move_tile_by_id(identifier, target)
        s.notice, s.failures = "", 0
        if s.step == 5 and Counter(player.hand_tiles[4:8]) == Counter((2, "+", 3, 5)) and not self._goal_done():
            if self.clock() - s.sort_started >= SORT_HELP_SECONDS:
                self._assist()
            else:
                s.notice = "这四张牌已经选对了，试着按 2、+、3、5 的顺序排列。"

    def _groups(self):
        s = self.current
        values = s.game.get_player("p0").hand_tiles
        if self.lesson == 0:
            return [{"start": a, "end": b, "label": label if s.step >= reveal else ""}
                    for a, b, label, reveal in ((0,4,"1 + 2 = 3",7),(4,8,"2 × 2 = 4",8),(8,12,"四张 7 · 刻子",9),(12,14,"两张 9 · 对子",11))]
        if s.step == len(SORT_STEPS) - 1:
            return [{"start":0,"end":len(values),"label":""}]
        if s.step >= 10:
            return [{"start":a,"end":b,"label":label} for a,b,label in
                    ((0,4,"2 + 3 = 5"),(4,8,"2 + 12 = 14"),(8,10,"14、14 · 对子"),(10,13,"14 + ? = 24"),(13,14,"摸入的 10"))]
        groups, start = [], 0
        if values[:4] == [14]*4:
            groups.append({"start":0,"end":4,"label":"四张 14 · 刻子"}); start = 4
        if s.step >= 5 and values[4:8] in ([2,"+",3,5], [3,"+",2,5]):
            groups.append({"start":4,"end":8,"label":f"{values[4]} + {values[6]} = 5"}); start = 8
        end = len(values) - (1 if s.drawn_id is not None else 0)
        if start < end:
            groups.append({"start":start,"end":end,"label":""})
        if end < len(values):
            groups.append({"start":end,"end":len(values),"label":"摸入的 10"})
        return groups

    def get(self):
        with self.lock:
            s = self.current
            if self.lesson in (2,3,4,5):
                return deepcopy({"tutorial_id":s.identifier, "revision":s.revision, "lesson":self.lesson,
                                 "available_lessons":[0,1,2,3,4,5], "chapters":CHAPTERS,
                                 "completed_lessons":[i for i,item in self.sessions.items() if item.completed],
                                 "pool_total":sum(TILE_COUNTS.values()), "tile_kinds":len(TILE_COUNTS),
                                 "one_copies":TILE_COUNTS[1], **s.view()})
            player = s.game.get_player("p0")
            steps = STEPS if self.lesson == 0 else SORT_STEPS
            done = self._goal_done()
            drag = None
            if self.lesson == 1 and s.step in (3,5) and not done:
                elapsed = max(0, self.clock() - s.sort_started)
                locked = next((i for i,v in enumerate(player.hand_tiles) if v != 14), len(player.hand_tiles)) if s.step == 3 else 4
                drag = {"kind":"quad" if s.step == 3 else "formula", "locked":locked,
                        "allowed_values":[14] if s.step == 3 else [2,3,5,"+"], "target":0 if s.step == 3 else 4,
                        "help_after":max(0,SORT_HELP_SECONDS-elapsed), "auto_after":max(0,SORT_HELP_SECONDS-elapsed)}
            highlight = steps[s.step].get("highlight", [])
            if self.lesson == 1:
                highlight = [i for i,v in enumerate(player.hand_tiles) if
                             s.step == 3 and v == 14 or s.step == 4 and v == "+" or s.step == 5 and i >= 4 and v in (2,3,5,"+") or
                             s.step in (6,7) and i >= 8 or s.step in (8,9) and player.hand_tile_ids[i] == s.drawn_id or
                             s.step == 10 or s.step in (11,12) and i >= 10 or s.step == 13]
            return deepcopy({"tutorial_id": s.identifier, "revision": s.revision,
                             "lesson": self.lesson, "available_lessons":[0,1,2,3,4,5], "chapters": CHAPTERS, "steps": steps, "step": s.step,
                             "completed_lessons":[i for i, item in self.sessions.items() if item.completed],
                             "completed": s.completed, "won": s.result is not None,
                             "hand": [t.to_dict() for t in player.hand_tile_records()],
                             "groups":self._groups(), "highlight":highlight, "drag":drag, "notice":s.notice,
                             "assisted_steps":sorted(s.assisted_steps), "next_action":"draw" if steps[s.step].get("requires") == "draw" else "complete" if s.step == len(steps)-1 else "next",
                             "next_enabled":not s.completed if s.step == len(steps)-1 else done or steps[s.step].get("requires") == "draw",
                             "hu_enabled":s.result is not None or (self.lesson == 0 and s.step == HU_STEP or self.lesson == 1 and s.drawn_id is not None and s.step >= 9),
                             "drawn_tile_id": s.drawn_id, "drawn":s.drawn_id is not None,
                             "result": s.result, "pool_total": sum(TILE_COUNTS.values()),
                             "tile_kinds": len(TILE_COUNTS), "one_copies": TILE_COUNTS[1],
                             "opponents": [{"name": p.name, "hand_count": len(p.hand_tiles)} for p in s.game.players[1:]]})

    def dispatch(self, command):
        with self.lock:
            s = self.current
            if command.get("tutorial_id") != s.identifier:
                raise ValueError("示范局已重开，请刷新当前教程")
            revision = command.get("revision")
            if type(revision) is not int or revision != s.revision:
                raise ValueError("讲解步骤已变化，请重新操作")
            action = command.get("action")
            if type(action) is not str or action not in {"next", "previous", "restart", "hu", "complete", "select_lesson", "move", "assist", "draw", "peng", "chi", "chi_detail", "discard", "gang", "regroup"}:
                raise ValueError("未知的教程操作")
            if action == "restart":
                self._start()
                return self.get()
            if action == "select_lesson":
                lesson = command.get("lesson")
                if type(lesson) is not int or lesson not in (0,1,2,3,4,5):
                    raise ValueError("这一步的课程内容还在准备中")
                if lesson != self.lesson:
                    s.revision += 1
                    self.lesson = lesson
                    self.current.revision += 1
                return self.get()
            if self.lesson in (2,3,4,5):
                s.dispatch(action, command)
                s.revision += 1
                return self.get()
            steps = STEPS if self.lesson == 0 else SORT_STEPS
            if action == "next":
                if not self._goal_done():
                    raise ValueError("请先完成拖牌整理、摸牌或点击右下角的“胡”，再继续")
                if s.step == len(steps) - 1:
                    raise ValueError("已经是最后一段讲解")
                self._enter_step(s.step + 1)
            elif action == "previous":
                if s.step == 0:
                    raise ValueError("已经是第一段讲解")
                self._enter_step(s.step - 1)
            elif action == "move":
                self._move(command)
            elif action == "assist":
                if self.lesson != 1 or s.step not in (3,5) or self._goal_done() or s.sort_started is None:
                    raise ValueError("当前不需要代为整理")
                automatic = command.get("automatic", False)
                if type(automatic) is not bool or self.clock() - s.sort_started < SORT_HELP_SECONDS:
                    raise ValueError("先试着拖动一下；稍后就能帮你整理")
                self._assist()
            elif action == "draw":
                if self.lesson != 1 or s.step != 8 or s.drawn_id is not None:
                    raise ValueError("请在中央讲解提示摸牌时点击“摸一张牌”")
                s.before_draw = capture_game_state(s.game, now=0)
                if s.game.draw_tile("p0", now=0) != 10:
                    raise ValueError("示范来牌与固定牌山不一致")
                s.drawn_id = s.game.drawn_tile_id
                self._enter_step(9)
            elif action == "hu":
                allowed = s.step == HU_STEP if self.lesson == 0 else s.drawn_id is not None and s.step >= 9
                if not allowed or s.result is not None:
                    raise ValueError("请跟随中央讲解，在可以胡牌时点击右下角的“胡”")
                s.game.submit_turn_action("p0", ActionType.HU, now=0, source_turn_window_id=s.game.turn_window_id)
                s.result = s.game.settlement_events[0].to_dict()
                if self.lesson == 0:
                    self._enter_step(s.step + 1)
            elif action == "complete":
                if s.step != len(steps) - 1 or s.result is None:
                    raise ValueError("请先完成示范胡牌和最后一段讲解")
                s.completed = True
            s.revision += 1
            return self.get()
