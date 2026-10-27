"""Stable rule contracts and the adapter for the current calculator package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from calculator_base.hand_structure import Hand, MeldedGroup, create_tile_from_value
from calculator_base.mahjong_checker import ArithmeticMahjong


TileValue = int | str


class WinMethod(str, Enum):
    DISCARD = "点胡"
    SELF_DRAW = "自摸"
    ROB_KONG = "抢杠"
    HEAVENLY = "天胡"  # Legacy calculator input; live wins use is_heavenly.


@dataclass(frozen=True)
class MeldSnapshot:
    group_type: str
    tiles: tuple[TileValue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"group_type": self.group_type, "tiles": list(self.tiles)}


@dataclass(frozen=True)
class HandSnapshot:
    """Rule-facing hand data; private UI grouping is intentionally excluded."""

    hand_tiles: tuple[TileValue, ...]
    melded_groups: tuple[MeldSnapshot, ...] = ()

    @classmethod
    def from_player(cls, player: Any) -> "HandSnapshot":
        return cls(
            hand_tiles=tuple(player.hand_tiles),
            melded_groups=tuple(
                _meld_snapshot(meld) for meld in player.melded_groups
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand_tiles": list(self.hand_tiles),
            "melded_groups": [meld.to_dict() for meld in self.melded_groups],
        }


@dataclass(frozen=True)
class WinContext:
    """Keep the payment method separate from independent fan conditions."""

    winner_id: str
    winning_tile: TileValue | None
    win_method: WinMethod
    source_player_id: str | None = None
    is_heavenly: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner_id": self.winner_id,
            "winning_tile": self.winning_tile,
            "win_method": self.win_method.value,
            "source_player_id": self.source_player_id,
            "is_heavenly": self.is_heavenly,
        }


@dataclass(frozen=True)
class FanItem:
    code: str
    name: str
    fan_value: int
    count: int
    total_fan: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "fan_value": self.fan_value,
            "count": self.count,
            "total_fan": self.total_fan,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WinResult:
    is_winning_shape: bool
    can_start: bool
    win_type: str | None
    win_method: WinMethod
    winning_tile: TileValue | None
    groups: tuple[tuple[TileValue, ...], ...] = ()
    fan_items: tuple[FanItem, ...] = ()
    total_fan: int = 0
    starting_fan: int = 0
    capped_fan: int = 0
    base_payment: int = 0
    ruleset_id: str = ""
    error: str | None = None

    @property
    def can_win(self) -> bool:
        return self.is_winning_shape and self.can_start and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_win": self.can_win,
            "is_winning_shape": self.is_winning_shape,
            "can_start": self.can_start,
            "win_type": self.win_type,
            "win_method": self.win_method.value,
            "winning_tile": self.winning_tile,
            "groups": [list(group) for group in self.groups],
            "fan_items": [item.to_dict() for item in self.fan_items],
            "total_fan": self.total_fan,
            "starting_fan": self.starting_fan,
            "capped_fan": self.capped_fan,
            "base_payment": self.base_payment,
            "ruleset_id": self.ruleset_id,
            "error": self.error,
        }

    def to_action_payload(self) -> dict[str, Any]:
        """Return the public rule details attached to a HU ActionOption."""
        return {
            "win_type": self.win_type,
            "winning_method": self.win_method.value,
            "winning_tile": self.winning_tile,
            "total_fan": self.total_fan,
            "starting_fan": self.starting_fan,
            "capped_fan": self.capped_fan,
            "base_payment": self.base_payment,
            "can_start": self.can_start,
            "groups": [list(group) for group in self.groups],
            "fan_items": [item.to_dict() for item in self.fan_items],
            "ruleset_id": self.ruleset_id,
        }


@runtime_checkable
class RuleAdapter(Protocol):
    @property
    def ruleset_id(self) -> str:
        ...

    def evaluate_win(
        self,
        hand: HandSnapshot,
        context: WinContext,
    ) -> WinResult:
        ...


class ArithmeticMahjongRuleAdapter:
    """Normalize the existing calculator's best-fan result into WinResult."""

    def __init__(
        self,
        *,
        min_fan: int = 1,
        ruleset_id: str = "arithmetic-mahjong-simplified-v1",
    ) -> None:
        if min_fan < 0:
            raise ValueError("min_fan 不能小于 0")
        self.min_fan = min_fan
        self._ruleset_id = ruleset_id

    @property
    def ruleset_id(self) -> str:
        return self._ruleset_id

    def evaluate_win(
        self,
        hand: HandSnapshot,
        context: WinContext,
    ) -> WinResult:
        try:
            calculator_hand = self._to_calculator_hand(hand, context)
            can_shape, groups, win_type, fan_info = ArithmeticMahjong(
                min_fan=self.min_fan
            ).can_win(calculator_hand)
        except Exception as exc:
            return self._error_result(context, f"规则计算失败: {exc}")

        if not can_shape:
            return WinResult(
                is_winning_shape=False,
                can_start=False,
                win_type=None,
                win_method=context.win_method,
                winning_tile=context.winning_tile,
                ruleset_id=self.ruleset_id,
            )
        if fan_info is None:
            return WinResult(
                is_winning_shape=True,
                can_start=False,
                win_type=win_type,
                win_method=context.win_method,
                winning_tile=context.winning_tile,
                groups=_serialize_groups(groups),
                ruleset_id=self.ruleset_id,
                error="计番结果不可用",
            )

        return WinResult(
            is_winning_shape=True,
            can_start=bool(fan_info.get("can_start", False)),
            win_type=win_type,
            win_method=context.win_method,
            winning_tile=context.winning_tile,
            groups=_serialize_groups(groups),
            fan_items=_serialize_fan_items(fan_info.get("fan_result")),
            total_fan=int(fan_info.get("total_fan", 0)),
            starting_fan=int(fan_info.get("starting_fan", 0)),
            capped_fan=int(fan_info.get("capped_fan", 0)),
            base_payment=int(fan_info.get("payment", 0)),
            ruleset_id=self.ruleset_id,
        )

    def _to_calculator_hand(
        self,
        snapshot: HandSnapshot,
        context: WinContext,
    ) -> Hand:
        hand_tiles = [create_tile_from_value(value) for value in snapshot.hand_tiles]
        winning_tile_obj = None

        if context.win_method in (WinMethod.DISCARD, WinMethod.ROB_KONG):
            if context.winning_tile is None:
                raise ValueError("点胡或抢杠必须提供胡牌牌张")
            winning_tile_obj = create_tile_from_value(
                context.winning_tile,
                winning=True,
            )
            hand_tiles.append(winning_tile_obj)
        elif context.winning_tile is not None:
            for tile in reversed(hand_tiles):
                if tile.value == context.winning_tile:
                    tile.winning = True
                    winning_tile_obj = tile
                    break
            if winning_tile_obj is None:
                raise ValueError("自摸胡牌牌张不在手牌中")

        melded_groups = [
            MeldedGroup(
                [create_tile_from_value(value) for value in meld.tiles],
                meld.group_type,
            )
            for meld in snapshot.melded_groups
        ]
        return Hand(
            melded_groups=melded_groups,
            hand_tiles=hand_tiles,
            winning_tile=winning_tile_obj,
            winning_method=context.win_method.value,
            should_win_in_mode=True,
            is_heavenly=context.is_heavenly,
        )

    def _error_result(self, context: WinContext, error: str) -> WinResult:
        return WinResult(
            is_winning_shape=False,
            can_start=False,
            win_type=None,
            win_method=context.win_method,
            winning_tile=context.winning_tile,
            ruleset_id=self.ruleset_id,
            error=error,
        )


def _meld_snapshot(meld: Any) -> MeldSnapshot:
    if isinstance(meld, MeldedGroup):
        return MeldSnapshot(
            group_type=meld.group_type,
            tiles=tuple(tile.value for tile in meld.tiles),
        )
    group_type = meld.get("group_type") or meld.get("type")
    tiles = tuple(
        tile.value if hasattr(tile, "value") else tile
        for tile in meld.get("tiles", [])
    )
    return MeldSnapshot(group_type=group_type, tiles=tiles)


def _serialize_groups(groups: list[Any]) -> tuple[tuple[TileValue, ...], ...]:
    serialized = []
    for group in groups:
        tiles = group.tiles if isinstance(group, MeldedGroup) else group
        serialized.append(
            tuple(tile.value if hasattr(tile, "value") else tile for tile in tiles)
        )
    return tuple(serialized)


def _serialize_fan_items(fan_result: Any) -> tuple[FanItem, ...]:
    if fan_result is None:
        return ()
    return tuple(
        FanItem(
            code=result.fan_type.name,
            name=result.fan_type.fan_name,
            fan_value=result.fan_type.fan_value,
            count=result.count,
            total_fan=result.get_total_fan(),
            reason=result.reason,
        )
        for result in fan_result.results
    )
