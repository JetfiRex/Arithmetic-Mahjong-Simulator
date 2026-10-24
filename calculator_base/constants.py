"""
Constants for the simplified arithmetic mahjong rules.

This module is the source of truth for valid tile values, tile counts, symbol
aliases, and winning method aliases. A few legacy names are kept as inert
compatibility placeholders while the rest of the complex-version code is being
refactored.
"""

# ============================================================
# Symbols
# ============================================================

PLUS = "+"
MULTIPLY = "×"

# Legacy compatibility only. POWER is no longer a legal tile in the simplified
# rules, so it is intentionally excluded from SYMBOLS and ALL_TILES.
POWER = "∧"

SYMBOLS = {PLUS, MULTIPLY}

SYMBOL_ALIASES = {
    "+": PLUS,
    "加": PLUS,
    "*": MULTIPLY,
    "x": MULTIPLY,
    "X": MULTIPLY,
    "×": MULTIPLY,
    "乘": MULTIPLY,
}

# ============================================================
# Winning Methods
# ============================================================

WINNING_METHOD_ALIASES = {
    "自摸": "自摸",
    "tsumo": "自摸",
    "zimo": "自摸",
    "zm": "自摸",
    "z": "自摸",
    "点胡": "点胡",
    "放铳": "点胡",
    "ron": "点胡",
    "r": "点胡",
    "抢杠": "抢杠",
    "抢": "抢杠",
    "qg": "抢杠",
    "q": "抢杠",
    "天胡": "天胡",
    "天": "天胡",
    "th": "天胡",
    "t": "天胡",
}

# ============================================================
# Tiles
# ============================================================

VALID_NUMBER_TILES = set(range(21)) | {24}

TILE_COUNTS = {
    0: 4,
    1: 4,
    2: 6,
    3: 6,
    4: 6,
    5: 4,
    6: 6,
    7: 4,
    8: 4,
    9: 4,
    10: 4,
    11: 2,
    12: 4,
    13: 2,
    14: 4,
    15: 4,
    16: 4,
    17: 2,
    18: 4,
    19: 2,
    20: 4,
    24: 4,
    PLUS: 8,
    MULTIPLY: 12,
}

ALL_TILES = VALID_NUMBER_TILES | SYMBOLS

# ============================================================
# Legacy Compatibility Placeholders
# ============================================================

JOKER_TIAO = "joker_tiao"
JOKER_TONG = "joker_tong"
JOKER_WAN = "joker_wan"
JOKER_SYMBOL = "joker_symbol"

# Jokers are not legal simplified-rule tiles.
JOKERS = set()
JOKER_ALIASES = {}
