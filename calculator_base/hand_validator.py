"""
手牌验证模块
验证手牌张数是否符合麻将规则
"""

from typing import Tuple, Optional


def validate_hand_count(hand) -> Tuple[bool, Optional[str]]:
    """
    验证手牌张数是否正确
    
    规则：
    - 未胡：(吃碰明暗杠数量)*4 + 手牌张数 = 15
    - 已胡：(吃碰明暗杠数量)*4 + 手牌张数 = 16
    - 注意：单张杠不算在鸣牌中
    
    参数：
        hand: Hand对象 或 列表
    
    返回：
        (是否有效, 错误信息)
        - (True, None): 有效
        - (False, "多了X张牌"): 牌太多
        - (False, "少了X张牌"): 牌太少
    """
    # 统计手牌张数
    hand_count = 0
    melded_count = 0  # 吃碰明暗杠数量（不包括单张杠）
    single_gang_count = 0  # 单张杠数量
    is_won = False
    
    if hasattr(hand, 'hand_groups'):
        # Hand对象
        # 如果hand_groups为空（特殊胜利），跳过验证
        if not hand.hand_groups or len(hand.hand_groups) == 0:
            # 特殊胜利不需要验证牌数
            return True, None
        
        for group in hand.hand_groups:
            hand_count += len(group)
        
        if hasattr(hand, 'melded_groups'):
            for melded in hand.melded_groups:
                if melded.group_type == '单张杠':
                    single_gang_count += 1
                else:
                    # 吃、碰、明杠、暗杠
                    melded_count += 1
        
        # 检查是否已胡
        if hasattr(hand, 'winning_tile') and hand.winning_tile is not None:
            is_won = True
        # 如果手牌张数是16且没有鸣牌，默认是已胡状态（模式2）
        elif hand_count == 16 and melded_count == 0 and single_gang_count == 0:
            is_won = True
    elif hasattr(hand, 'hand_tiles'):
        # Hand对象但没有hand_groups（模式2/3）
        hand_count = len(hand.hand_tiles)
        
        if hasattr(hand, 'melded_groups'):
            for melded in hand.melded_groups:
                if melded.group_type == '单张杠':
                    single_gang_count += 1
                else:
                    # 吃、碰、明杠、暗杠
                    melded_count += 1
        
        # 检查是否已胡
        if hasattr(hand, 'winning_tile') and hand.winning_tile is not None:
            is_won = True
        # 如果手牌张数是16且没有鸣牌，默认是已胡状态
        elif hand_count == 16 and melded_count == 0 and single_gang_count == 0:
            is_won = True
    else:
        # 列表形式（向后兼容）
        hand_count = len(hand)
        # 16张默认是已胡，15张默认是听牌
        is_won = (hand_count == 16)

    if hasattr(hand, 'should_win_in_mode') and hand.should_win_in_mode == True:
        is_won = True
    elif hand.should_win_in_mode == False:
        is_won = False
    
    # 计算实际牌数
    actual_count = melded_count * 4 + hand_count
    
    # 期望牌数
    expected_count = 16 if is_won else 15
    
    # 验证
    if actual_count == expected_count:
        return True, None
    elif actual_count > expected_count:
        diff = actual_count - expected_count
        return False, f"多了{diff}张牌（实际{actual_count}张，期望{expected_count}张）"
    else:
        diff = expected_count - actual_count
        return False, f"少了{diff}张牌（实际{actual_count}张，期望{expected_count}张）"


def validate_special_winning(hand, special_win_type: str) -> Tuple[bool, Optional[str]]:
    """
    验证特殊胜利手牌是否合法
    
    参数：
        hand: Hand对象或列表
        special_win_type: "八仙过海", "四仙过海", "天龙", "地龙", "十三幺"
    
    返回：
        (是否合法, 错误信息)
    """
    # 导入判定器
    try:
        from special_winning_checker import SpecialWinningChecker
        checker = SpecialWinningChecker()
    except ImportError:
        return True, None  # 如果模块不可用，跳过验证
    
    # 检查是否满足特殊胜利条件
    if special_win_type == "八仙过海":
        can_win, _ = checker.can_win_ba_xian_guo_hai(hand)
        if not can_win:
            return False, "诈胡：不满足八仙过海条件（需要4个宝牌单张杠+4个万用牌单张杠）"
    elif special_win_type == "四仙过海":
        can_win, _ = checker.can_win_si_xian_guo_hai(hand)
        if not can_win:
            return False, "诈胡：不满足四仙过海条件（需要4个宝牌或4个万用牌单张杠）"
    elif special_win_type == "天龙":
        can_win, _ = checker.can_win_tian_long(hand)
        if not can_win:
            return False, "诈胡：不满足天龙条件（需要16个连续数字）"
    elif special_win_type == "地龙":
        can_win, _ = checker.can_win_di_long(hand)
        if not can_win:
            return False, "诈胡：不满足地龙条件（需要12项等差数列，公差1-4）"
    elif special_win_type == "十三幺":
        can_win, _ = checker.can_win_shi_san_yao(hand)
        if not can_win:
            return False, "诈胡：不满足十三幺条件（需要1,9,10,11,19,20,21,49,30,40,+,×,^各至少1张）"
    
    return True, None


def can_form_valid_groups(hand) -> bool:
    """
    检查手牌能否组成有效的分组（算术麻将、传统麻将或八小对）
    
    参数：
        hand: 列表形式的手牌
    
    返回：
        bool: 能否组成有效分组
    """
    try:
        # 尝试算术麻将
        from mahjong_checker import ArithmeticMahjong
        mjong = ArithmeticMahjong(min_fan=0)  # min_fan=0，只检查能否胡
        
        can_win, _, _, _ = mjong.can_win(hand)
        return can_win
    except:
        return False
