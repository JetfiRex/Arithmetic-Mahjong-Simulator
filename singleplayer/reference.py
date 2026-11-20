"""Offline reference facts, tied to the shipped tile pool and fan catalogue."""

from functools import lru_cache

from calculator_base.constants import TILE_COUNTS
from calculator_base.traditional_mahjong import TraditionalMahjongChecker
from fan_calculator.fan_base import FAN_EXCLUSIONS, MAX_FAN, FanType


FAN_DESCRIPTIONS = {
    "DA_SAN_YUAN": "手牌及副露中的 +、× 合计至少 9 张。",
    "DA_SI_XI": "手牌及副露中的 0、10、20 合计至少 6 张。",
    "QI_YI_SE": "数字牌全部为奇数。符号牌不受这个数字条件限制。",
    "LIAN_QI_DUI": "七种不同的数字各成一对，七个数字组成等差数列。属于特殊胡牌结构。",
    "QUAN_ZHI_SHU": "数字牌全部是质数或 1，可用数字为 1、2、3、5、7、11、13、17、19。",
    "SAN_KE_ZI": "胡牌分组中有三个刻子。算术刻子为四张相同牌，杠组为五张相同牌。",
    "QUAN_ER_MI": "数字牌都是 2 的非负整数次幂：1、2、4、8、16。",
    "SAN_TONG_SHI": "胡牌分组中有三个相同算式。交换加数或因数顺序仍是同一个算式。",
    "QING_LONG": "十二个连续数字，再加一个对子，共十四张。属于特殊胡牌结构。",
    "QUAN_DUO_BEI": "所有数字牌的最大公约数至少为 4。符号牌不参与公约数计算。",
    "QUAN_ER_WEI": "数字牌全部大于或等于 10。",
    "TIAN_HU": "在本局自己第一次出牌之前胡牌。不要求是庄家；自摸、点胡或抢杠按实际方式付款。",
    "QUAN_SAN_BEI": "数字牌全部为 3 的倍数。",
    "QUAN_YI_WEI": "数字牌全部位于 0～9。",
    "QI_XIAO_DUI": "七种不同的牌各有两张，组成七对。四张相同牌不算两对；对子可以是数字或符号。",
    "QUAN_OU_SHU": "数字牌全部为偶数。",
    "SAN_MEN_QI": "同一胡牌分组中有一个加法算式、一个乘法算式、一个刻子，以及一个对子。",
    "CHUAN_TONG_MAJIANG": "按照中间的传统图案，组成四组三张和一对。三张可以是同花色连续数牌或三张相同牌；仍使用本游戏的起胡规则。",
    "YI_DUI_SHI": "胡牌分组中有两个相同算式。交换加数或因数顺序不影响是否相同。",
    "JIA_YI_SE": "三个四张分组全部是加法算式。",
    "CHENG_YI_SE": "三个四张分组全部是乘法算式。",
    "BU_QIU_REN": "没有吃、碰、明杠或加杠的自摸胡牌。暗杠不影响不求人。",
}

RULES = (
    {"title": "牌面怎么读", "paragraphs": ["数字为 0～20 和 24；另外有 +、× 两种符号。左上角显示牌值，右下角显示这类牌在整副牌中的固定张数；中间图案用于传统麻将胡法。", "整副牌共 24 种、108 张，各牌种的数量不同。右下角的数字不会随摸打变化。"]},
    {"title": "算式、刻子、对子与胡牌", "paragraphs": ["算式用四张牌表示 a + b = c 或 a × b = c，等号不占牌。例如 1、+、2、3。", "普通刻子是四张同值牌；杠形成五张同值牌。对子是两张相同牌，数字和符号都可以。", "基本胡牌结构为三组算式或刻子，加一对。还需要达到起胡番；本机普通对局为 1 番起胡。传统麻将、七小对、连七对和清龙另有结构，见番数表。"]},
    {"title": "摸打与开局理牌", "paragraphs": ["四家先各拿 13 张，庄家先摸牌。通常摸一张、打一张，轮到下一家。吃碰后直接出牌，杠后补摸一张。", "每局默认先理牌 30 秒，可在开局设置调整或跳过。每有一组固定吃碰杠，打牌后的暗手数减少四张：13、9、5、1。", "拖动手牌用于整理顺序，不改变牌型是否成立；每局结束后庄家轮换到下家。"]},
    {"title": "吃与碰", "paragraphs": ["吃：只有上家刚打出的牌可以吃。用手中的三张与来牌组成四张算式，选择吃法后出一张。", "碰：任何一家打牌后，拿自己的三张同值牌与它组成四张刻子，再出一张。", "吃碰后不补摸，也不能立即杠牌；已经公开的副露作为固定分组。"]},
    {"title": "明杠、暗杠、加杠与抢杠", "paragraphs": ["明杠：自己的四张同值牌加上别人的弃牌，共五张。暗杠：摸牌后用手中五张同值牌组成杠。两种杠都要补摸一张。", "加杠：已有四张碰组，再用一张同值牌补为五张，成功后补摸。只有加杠可以被抢杠。", "牌山已空时不能杠。补摸后进入正常回合，可以继续合法操作，最后出一张。"]},
    {"title": "听牌、自摸、点胡与响应", "paragraphs": ["打牌后的手牌再加一张就能成牌，称为结构听牌。真正胡牌还需满足起胡番。自己摸到叫自摸，别人打出叫点胡，胡到别人加杠的牌叫抢杠。", "同时响应的优先级为胡、杠、碰、吃。多个碰杠冲突时，按行牌方向离出牌家近的优先。", "一炮多响可在开局设置开关；关闭时只保留离出牌家近的赢家。"]},
    {"title": "计番与结算", "paragraphs": ["1 番起胡，1 番每份为 2 筹码，之后每多一番翻倍。实际结算最多按 6 番计算，即每份最多 64 筹码。", "普通模式自摸由三家各付一份；点胡由出牌家付款，抢杠由加杠者付款，庄家不额外翻倍。", "同一胡牌采用一套合法分组计番，不能把不同分组的番数合在一起。已被大番包含的番种不再重复计算。", "对数字有限制的番种至少需要一张数字牌，符号牌不受该数字条件限制。番数表同时列出不重复计入的番种。"]},
    {"title": "普通模式与血战", "paragraphs": ["普通模式有人胡牌即结束本局；未胡而牌山摸空则流局。", "血战中，胡牌玩家退出后续行动和付款，其余人继续。自摸只由尚未胡牌的对手支付。", "第三家胡牌或牌山摸空时，血战整局结束；此前已经发生的胡牌结算保留。"]},
    {"title": "计算、练习与教学", "paragraphs": ["听牌练习只判断牌型，不考虑起胡番；空听也要选出，答案会标注可用副本为零。", "计算和教学中的连续进张评分用于比较推进路线，不是整局胡牌率；不会计算退向听、放铳风险或摸到无效牌后继续等待的收益。", "教学模式可以暂停思考；查阅速查本身不会暂停计时对局。"]},
)


@lru_cache(maxsize=1)
def reference_data():
    mapping = TraditionalMahjongChecker().num_to_tile
    digits = "零一二三四五六七八九"
    tiles = []
    for value, count in TILE_COUNTS.items():
        suit, face = mapping[value]
        name = digits[face] + suit if type(face) is int else face + "风" if suit == "风" else "红中" if value == "+" else "发财"
        tiles.append({"value": value, "count": count, "traditional": name})
    fans = [{"code": fan.name, "name": fan.fan_name, "fan": fan.fan_value,
             "description": FAN_DESCRIPTIONS[fan.name],
             "excludes": [other.fan_name for other in FAN_EXCLUSIONS.get(fan, [])]}
            for fan in FanType]
    return {"rules": RULES, "tiles": tiles, "fans": fans, "total": sum(TILE_COUNTS.values()),
            "number_total": sum(n for v, n in TILE_COUNTS.items() if type(v) is int),
            "symbol_total": sum(n for v, n in TILE_COUNTS.items() if type(v) is str), "max_fan": MAX_FAN}
