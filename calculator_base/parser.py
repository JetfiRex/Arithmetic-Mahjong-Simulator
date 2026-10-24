"""
Parser for simplified arithmetic mahjong hands.
"""

from calculator_base.constants import *
from calculator_base.hand_structure import Hand, MeldedGroup, create_tile_from_value


def tile_sort_key(x):
    """Stable tile ordering: symbols first, then numbers."""
    if x in SYMBOLS:
        return (0, str(x))
    return (1, int(x))


def parse_hand(hand_str):
    """
    Parse a flat tile string into simple tile values.

    Supported tiles:
    - numbers: 0-20 and 24
    - symbols: +, *, x, X, ×
    """
    hand_str = hand_str.replace(",", " ").replace("，", " ")
    result = []

    for token in hand_str.split():
        value = _parse_simple_tile_value(token)
        result.append(value)

    return result


def format_hand(hand):
    return " ".join(str(tile) for tile in hand)


def validate_tile(tile):
    return tile in ALL_TILES


def validate_hand(hand, expected_len=None):
    if expected_len is not None and len(hand) != expected_len:
        return False, f"手牌数量错误：期望 {expected_len} 张，实际 {len(hand)} 张"

    for i, tile in enumerate(hand):
        if not validate_tile(tile):
            return False, f"第 {i + 1} 张牌不合法: {tile}"

    return True, ""


def _normalize_brackets(text: str) -> str:
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("【", "[").replace("】", "]")
    text = text.replace("｛", "{").replace("｝", "}")
    return text


def _tokenize_complex(text: str):
    text = _normalize_brackets(text)
    text = text.replace(",", " ").replace("，", " ")

    tokens = []
    current = ""

    for char in text:
        if char in "()[]{}|/":
            if current.strip():
                tokens.append(current.strip())
                current = ""
            tokens.append(char)
        elif char.isspace():
            if current.strip():
                tokens.append(current.strip())
                current = ""
        else:
            current += char

    if current.strip():
        tokens.append(current.strip())

    return tokens


def _parse_simple_tile_value(token: str):
    if not token:
        raise ValueError("空token")

    if token.endswith(("d", "w")):
        raise ValueError(f"简单版不支持宝牌或万用牌标记: '{token}'")

    if token in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[token]

    if token in JOKER_ALIASES:
        raise ValueError(f"简单版没有万用牌: '{token}'")

    try:
        value = int(token)
    except ValueError:
        raise ValueError(f"无法识别的牌token: '{token}'") from None

    if value not in VALID_NUMBER_TILES:
        raise ValueError(f"非法数字牌: {value}")

    return value


def _parse_tile_token(token: str):
    """
    Compatibility shape for older parser callers.

    Returns (value, is_dora, is_joker_used); the last two are always False in
    simplified rules.
    """
    return _parse_simple_tile_value(token), False, False


def _parse_melded_group(tokens: list, start_idx: int):
    if tokens[start_idx] != "(":
        raise ValueError(f"期望'('，实际'{tokens[start_idx]}'")

    end_idx = start_idx + 1
    depth = 1
    while end_idx < len(tokens) and depth > 0:
        if tokens[end_idx] == "(":
            depth += 1
        elif tokens[end_idx] == ")":
            depth -= 1
        end_idx += 1

    if depth != 0:
        raise ValueError("括号不匹配")

    inner_tokens = tokens[start_idx + 1 : end_idx - 1]
    if not inner_tokens:
        raise ValueError("空的鸣牌面子")

    group_type = None
    for marker, marker_type in (("明", "明杠"), ("暗", "暗杠"), ("加", "加杠")):
        if marker in inner_tokens:
            inner_tokens.remove(marker)
            group_type = marker_type
            break

    tiles = []
    for token in inner_tokens:
        if token in {"(", ")", "[", "]", "{", "}", "|", "/"}:
            raise ValueError(f"鸣牌面子中出现非法字符: '{token}'")
        value, is_dora, is_joker_used = _parse_tile_token(token)
        tiles.append(create_tile_from_value(value, is_dora, is_joker_used))

    if group_type is None:
        if len(tiles) == 4:
            group_type = "碰" if len({tile.value for tile in tiles}) == 1 else "吃"
        elif len(tiles) == 5:
            group_type = "暗杠"
        else:
            raise ValueError(f"无效的鸣牌面子张数: {len(tiles)}")

    return MeldedGroup(tiles, group_type), end_idx


def _infer_win_type(hand_groups, has_melded_groups=False):
    if not hand_groups:
        return None

    lengths = [len(group) for group in hand_groups]

    if lengths.count(2) == 1 and all(length in {2, 4} for length in lengths):
        return "算术麻将"
    if len(lengths) == 7 and all(length == 2 for length in lengths):
        return "七小对"
    if sorted(lengths) == [2, 12]:
        return "清龙"
    if (
        not has_melded_groups
        and lengths.count(2) == 1
        and all(length in {2, 3} for length in lengths)
    ):
        return "传统麻将"

    return None


def parse_mode1_already_won(hand_str: str):
    """
    Mode 1: already-won grouped hand.

    Format:
        (meld) hand_group / hand_group / pair [winning] {method}
    """
    tokens = _tokenize_complex(hand_str)

    melded_groups = []
    hand_groups = []
    current_group = []
    winning_tile = None
    winning_method = None

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]

        if token == "(":
            melded_group, next_idx = _parse_melded_group(tokens, idx)
            melded_groups.append(melded_group)
            idx = next_idx
        elif token == "[":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("'['后缺少牌")
            value, is_dora, is_joker_used = _parse_tile_token(tokens[idx])
            winning_tile = create_tile_from_value(value, is_dora, is_joker_used, winning=True)
            current_group.append(winning_tile)
            idx += 1
            if idx >= len(tokens) or tokens[idx] != "]":
                raise ValueError("缺少']'")
            idx += 1
        elif token == "{":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("'{'后缺少胡牌方式")
            winning_method = WINNING_METHOD_ALIASES.get(tokens[idx], tokens[idx])
            idx += 1
            if idx >= len(tokens) or tokens[idx] != "}":
                raise ValueError("缺少'}'")
            idx += 1
        elif token in {"|", "/"}:
            if current_group:
                hand_groups.append(current_group)
                current_group = []
            idx += 1
        elif token in {")", "]", "}"}:
            raise ValueError(f"不匹配的符号: '{token}'")
        else:
            value, is_dora, is_joker_used = _parse_tile_token(token)
            current_group.append(create_tile_from_value(value, is_dora, is_joker_used))
            idx += 1

    if current_group:
        hand_groups.append(current_group)

    return Hand(
        melded_groups=melded_groups,
        hand_tiles=[],
        hand_groups=hand_groups,
        winning_tile=winning_tile,
        winning_method=winning_method,
        win_type=_infer_win_type(hand_groups, has_melded_groups=bool(melded_groups)),
        should_win_in_mode=True,
    )


def parse_mode2_check_win(hand_str: str):
    """Mode 2: ungrouped already-won hand."""
    tokens = _tokenize_complex(hand_str)

    melded_groups = []
    hand_tiles = []
    winning_tile = None
    idx = 0

    while idx < len(tokens):
        token = tokens[idx]

        if token == "(":
            melded_group, next_idx = _parse_melded_group(tokens, idx)
            melded_groups.append(melded_group)
            idx = next_idx
        elif token == "[":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("'['后缺少牌")
            value, is_dora, is_joker_used = _parse_tile_token(tokens[idx])
            winning_tile = create_tile_from_value(value, is_dora, is_joker_used, winning=True)
            hand_tiles.append(winning_tile)
            idx += 1
            if idx >= len(tokens) or tokens[idx] != "]":
                raise ValueError("缺少']'")
            idx += 1
        elif token in {"|", "/", "{", "}", ")", "]"}:
            raise ValueError(f"模式2中出现非法符号: '{token}'")
        else:
            value, is_dora, is_joker_used = _parse_tile_token(token)
            hand_tiles.append(create_tile_from_value(value, is_dora, is_joker_used))
            idx += 1

    return Hand(
        melded_groups=melded_groups,
        hand_tiles=hand_tiles,
        winning_tile=winning_tile,
        should_win_in_mode=True,
    )


def parse_mode3_ready_with_meld(hand_str: str):
    """Mode 3: ready hand with melds."""
    tokens = _tokenize_complex(hand_str)

    melded_groups = []
    hand_tiles = []
    idx = 0

    while idx < len(tokens):
        token = tokens[idx]

        if token == "(":
            melded_group, next_idx = _parse_melded_group(tokens, idx)
            melded_groups.append(melded_group)
            idx = next_idx
        elif token in {"[", "]", "{", "}", "|", "/", ")",}:
            raise ValueError(f"模式3中出现非法符号: '{token}'")
        else:
            value, is_dora, is_joker_used = _parse_tile_token(token)
            hand_tiles.append(create_tile_from_value(value, is_dora, is_joker_used))
            idx += 1

    return Hand(
        melded_groups=melded_groups,
        hand_tiles=hand_tiles,
        should_win_in_mode=False,
    )


def parse_mode4_ready_no_meld(hand_str: str):
    """Mode 4: ready hand without melds."""
    tokens = _tokenize_complex(hand_str)
    hand_tiles = []

    for token in tokens:
        if token in {"(", ")", "[", "]", "{", "}", "|", "/"}:
            raise ValueError(f"模式4中出现非法符号: '{token}'")
        value, is_dora, is_joker_used = _parse_tile_token(token)
        hand_tiles.append(create_tile_from_value(value, is_dora, is_joker_used))

    return Hand(
        melded_groups=[],
        hand_tiles=hand_tiles,
        should_win_in_mode=False,
    )
