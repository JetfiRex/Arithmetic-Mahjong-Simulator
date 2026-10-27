"""
Core game-state primitives for the single-player Arithmetic Mahjong simulator.

This module intentionally does not know how to score a hand. It only owns the
round flow: wall creation, dealing, drawing, discarding, response windows, and
turn advancement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import operator
import random
from typing import Any, Optional, Union
from uuid import uuid4

from calculator_base.constants import TILE_COUNTS
from simulator.actions import (
    ActionDetailState,
    ActionOption,
    ActionRequest,
    ActionType,
    ResolvedAction,
    RobKongWindow,
    ResponseWindow,
    ResponseWindowStatus,
    TimeoutConfig,
    TimerKind,
    TimerState,
)
from simulator.action_options import (
    build_chi_detail_payload,
    build_discard_response_options,
    build_hu_response_option,
    build_turn_action_options,
    is_valid_chi_selection,
)
from simulator.action_resolver import resolve_response_requests
from simulator.config import GameConfig, GameMode
from simulator.hand_state import HandGroup, HandTile
from simulator.melds import (
    make_an_gang_meld,
    make_chi_meld,
    make_ming_gang_meld,
    make_peng_meld,
    upgrade_peng_to_jia_gang,
)
from simulator.rule_adapter import (
    ArithmeticMahjongRuleAdapter,
    HandSnapshot,
    RuleAdapter,
    WinContext,
    WinMethod,
)
from simulator.settlement import (
    BloodBattleScoringAdapter,
    FinalPlayerState,
    NormalScoringAdapter,
    PlayerScore,
    RoundEndReason,
    RoundResult,
    ScoreDelta,
    ScoringAdapter,
    SettlementContext,
    SettlementEvent,
)
from simulator.tile_selection import (
    TileSelectionError,
    select_tiles,
    select_tiles_by_region_order,
)


TileValue = Union[int, str]


class GameStateError(ValueError):
    """Raised when an action is invalid for the current game state."""


class GamePhase(str, Enum):
    INIT = "INIT"
    DEALING = "DEALING"
    WAITING_DRAW = "WAITING_DRAW"
    WAITING_TURN_ACTION = "WAITING_TURN_ACTION"
    WAITING_RESPONSE = "WAITING_RESPONSE"
    WAITING_ACTION_DETAIL = "WAITING_ACTION_DETAIL"
    WAITING_ROB_KONG = "WAITING_ROB_KONG"
    RESOLVING_ACTION = "RESOLVING_ACTION"
    SETTLING = "SETTLING"
    ROUND_ENDED = "ROUND_ENDED"


@dataclass(frozen=True)
class DiscardRecord:
    player_id: str
    seat: int
    tile: TileValue
    sequence: int
    tile_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "seat": self.seat,
            "tile": self.tile,
            "sequence": self.sequence,
            "tile_id": self.tile_id,
        }


@dataclass
class TileWall:
    tiles: list[TileValue]

    @classmethod
    def from_rule_tiles(
        cls,
        *,
        rng: Optional[random.Random] = None,
        shuffle: bool = True,
    ) -> "TileWall":
        tiles: list[TileValue] = []
        for tile, count in TILE_COUNTS.items():
            tiles.extend([tile] * count)

        if shuffle:
            random_source = rng if rng is not None else random
            random_source.shuffle(tiles)

        return cls(tiles)

    def draw(self) -> TileValue:
        if not self.tiles:
            raise GameStateError("牌山已空，无法摸牌")
        return self.tiles.pop()

    def remaining_count(self) -> int:
        return len(self.tiles)

    def can_draw(self) -> bool:
        return bool(self.tiles)

    def to_dict(self) -> dict[str, Any]:
        return {"remaining_count": self.remaining_count()}


@dataclass
class PlayerState:
    player_id: str
    seat: int
    name: str
    is_human: bool = False
    is_dealer: bool = False
    hand_tiles: list[TileValue] = field(default_factory=list)
    melded_groups: list[dict[str, Any]] = field(default_factory=list)
    discarded_tiles: list[TileValue] = field(default_factory=list)
    score: int = 0
    is_ready: bool = False
    is_winner: bool = False
    is_out: bool = False
    win_order: int | None = None
    win_settlement_id: str | None = None
    hand_tile_ids: list[str] = field(default_factory=list, init=False, repr=False)
    hand_groups: list[HandGroup] = field(default_factory=list)
    _identity_values: list[TileValue] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._reset_hand_identity()

    def add_hand_tile(self, value: TileValue) -> HandTile:
        """Append a new ungrouped tile at the visual right edge."""
        self._ensure_hand_identity()
        tile = HandTile.create(value)
        self.hand_tiles.append(value)
        self.hand_tile_ids.append(tile.tile_id)
        self._identity_values.append(value)
        return tile

    def hand_tile_records(self) -> list[HandTile]:
        self._ensure_hand_identity()
        return [
            HandTile(tile_id=tile_id, value=value)
            for tile_id, value in zip(self.hand_tile_ids, self.hand_tiles)
        ]

    def tile_id_at(self, index: int) -> str:
        self._ensure_hand_identity()
        if index < 0 or index >= len(self.hand_tile_ids):
            raise GameStateError(f"手牌位置越界: {index}")
        return self.hand_tile_ids[index]

    def tile_value(self, tile_id: str) -> TileValue:
        return self.hand_tiles[self._tile_index(tile_id)]

    def loose_tile_ids(self) -> list[str]:
        self._ensure_hand_identity()
        grouped_ids = {
            tile_id for group in self.hand_groups for tile_id in group.tile_ids
        }
        return [
            tile_id for tile_id in self.hand_tile_ids if tile_id not in grouped_ids
        ]

    def create_hand_group(self, tile_ids: list[str]) -> HandGroup:
        """Create the rightmost private group using current visual tile order."""
        self._ensure_hand_identity()
        if not tile_ids:
            raise GameStateError("分组至少需要一张牌")
        if len(tile_ids) != len(set(tile_ids)):
            raise GameStateError("同一张牌不能在分组中重复出现")

        known_ids = set(self.hand_tile_ids)
        unknown_ids = [tile_id for tile_id in tile_ids if tile_id not in known_ids]
        if unknown_ids:
            raise GameStateError(f"分组包含不属于该玩家的牌: {unknown_ids[0]}")

        selected_ids = [
            tile_id for tile_id in self.hand_tile_ids if tile_id in set(tile_ids)
        ]
        for group in self.hand_groups:
            group.tile_ids = [
                tile_id for tile_id in group.tile_ids if tile_id not in tile_ids
            ]
        self._remove_empty_groups()

        created_order = max(
            (group.created_order for group in self.hand_groups),
            default=-1,
        ) + 1
        new_group = HandGroup(tile_ids=selected_ids, created_order=created_order)
        self.hand_groups.append(new_group)
        self._rebuild_visual_order()
        return new_group

    def disband_hand_group(self, group_id: str) -> HandGroup:
        """Move a group's tiles to the left edge of the loose-tile region."""
        self._ensure_hand_identity()
        for index, group in enumerate(self.hand_groups):
            if group.group_id == group_id:
                removed = self.hand_groups.pop(index)
                self._rebuild_visual_order()
                return removed
        raise GameStateError(f"未知手牌分组: {group_id}")

    def move_tile_by_id(
        self,
        tile_id: str,
        to_index: int,
        *,
        group_id: Optional[str] = None,
    ) -> None:
        """Move a tile within/to a group, or to the loose region when group_id is None."""
        self._ensure_hand_identity()
        self._tile_index(tile_id)
        target_group = None
        if group_id is not None:
            target_group = next(
                (group for group in self.hand_groups if group.group_id == group_id),
                None,
            )
            if target_group is None:
                raise GameStateError(f"未知手牌分组: {group_id}")

        # Validate the destination after a hypothetical removal, before changing
        # any group. Inserting into the copy also validates the index type.
        if target_group is not None:
            target_ids = [member for member in target_group.tile_ids if member != tile_id]
            if to_index < 0 or to_index > len(target_ids):
                raise GameStateError(f"组内目标位置越界: {to_index}")
            target_ids.insert(to_index, tile_id)
            loose_ids = None
        else:
            loose_ids = [member for member in self.loose_tile_ids() if member != tile_id]
            if to_index < 0 or to_index > len(loose_ids):
                raise GameStateError(f"散牌目标位置越界: {to_index}")
            loose_ids.insert(to_index, tile_id)

        for group in self.hand_groups:
            if group is target_group:
                group.tile_ids = target_ids
            else:
                group.tile_ids = [member for member in group.tile_ids if member != tile_id]
        self._remove_empty_groups()
        self._rebuild_visual_order(loose_ids=loose_ids)

    def remove_tile_value(self, tile: TileValue) -> TileValue:
        """Remove the first matching value.

        Prefer remove_tile_at() for UI actions, because duplicate tile values can
        appear in different hand positions.
        """
        self._ensure_hand_identity()
        try:
            index = self.hand_tiles.index(tile)
        except ValueError as exc:
            raise GameStateError(f"玩家 {self.player_id} 没有这张牌: {tile}") from exc
        return self.remove_tile_at(index)

    def remove_tile_at(self, index: int) -> TileValue:
        self._ensure_hand_identity()
        if index < 0 or index >= len(self.hand_tiles):
            raise GameStateError(f"手牌位置越界: {index}")
        tile_id = self.hand_tile_ids.pop(index)
        self._identity_values.pop(index)
        tile = self.hand_tiles.pop(index)
        self._remove_tile_from_groups(tile_id)
        return tile

    def remove_tile_by_id(self, tile_id: str) -> TileValue:
        """Remove the exact physical tile selected by the UI."""
        return self.remove_tile_at(self._tile_index(tile_id))

    def remove_tiles_by_ids(self, tile_ids: list[str]) -> list[TileValue]:
        """Validate first, then remove a set of exact physical tiles."""
        self._ensure_hand_identity()
        if len(tile_ids) != len(set(tile_ids)):
            raise GameStateError("不能重复移除同一张实体牌")
        missing = [tile_id for tile_id in tile_ids if tile_id not in self.hand_tile_ids]
        if missing:
            raise GameStateError(f"玩家 {self.player_id} 没有这张实体牌: {missing[0]}")
        values_by_id = dict(zip(self.hand_tile_ids, self.hand_tiles))
        removed = [values_by_id[tile_id] for tile_id in tile_ids]
        for tile_id in tile_ids:
            self.remove_tile_by_id(tile_id)
        return removed

    def remove_leftmost_values(self, values: list[TileValue]) -> list[TileValue]:
        """Compatibility helper for meld execution until actions submit tile IDs."""
        removed = []
        for value in values:
            self._ensure_hand_identity()
            try:
                index = self.hand_tiles.index(value)
            except ValueError as exc:
                raise GameStateError(f"手牌中没有可移除的牌: {value}") from exc
            removed.append(self.remove_tile_at(index))
        return removed

    def move_tile(self, from_index: int, to_index: int) -> None:
        """Move one hand tile to another position for UI drag-and-drop ordering."""
        self._ensure_hand_identity()
        if from_index < 0 or from_index >= len(self.hand_tiles):
            raise GameStateError(f"起始手牌位置越界: {from_index}")
        if to_index < 0 or to_index >= len(self.hand_tiles):
            raise GameStateError(f"目标手牌位置越界: {to_index}")
        if self.hand_groups:
            raise GameStateError("存在手牌分组时请使用 tile_id 和目标区域移动牌")

        to_index = operator.index(to_index)
        tile = self.hand_tiles.pop(from_index)
        tile_id = self.hand_tile_ids.pop(from_index)
        identity_value = self._identity_values.pop(from_index)
        self.hand_tiles.insert(to_index, tile)
        self.hand_tile_ids.insert(to_index, tile_id)
        self._identity_values.insert(to_index, identity_value)

    def to_public_dict(self, *, reveal_hand: bool = False) -> dict[str, Any]:
        self._ensure_hand_identity()
        data = {
            "player_id": self.player_id,
            "seat": self.seat,
            "name": self.name,
            "is_human": self.is_human,
            "is_dealer": self.is_dealer,
            "hand_count": len(self.hand_tiles),
            "melded_groups": self.melded_groups,
            "discarded_tiles": list(self.discarded_tiles),
            "score": self.score,
            "is_ready": self.is_ready,
            "is_winner": self.is_winner,
            "is_out": self.is_out,
            "win_order": self.win_order,
            "win_settlement_id": self.win_settlement_id,
        }
        if reveal_hand:
            data["hand_tiles"] = list(self.hand_tiles)
            data["hand_tile_records"] = [
                tile.to_dict() for tile in self.hand_tile_records()
            ]
            tiles_by_id = dict(zip(self.hand_tile_ids, self.hand_tiles))
            data["hand_groups"] = [
                group.to_dict(tiles_by_id) for group in self.hand_groups
            ]
            data["loose_tile_ids"] = self.loose_tile_ids()
        return data

    def _tile_index(self, tile_id: str) -> int:
        self._ensure_hand_identity()
        try:
            return self.hand_tile_ids.index(tile_id)
        except ValueError as exc:
            raise GameStateError(f"玩家 {self.player_id} 没有这张实体牌: {tile_id}") from exc

    def _ensure_hand_identity(self) -> None:
        if (
            len(self.hand_tile_ids) != len(self.hand_tiles)
            or self._identity_values != self.hand_tiles
        ):
            # Direct hand_tiles assignment remains supported for tests and setup.
            # It starts a new physical hand, so previous IDs/groups cannot survive.
            self._reset_hand_identity()

    def _reset_hand_identity(self) -> None:
        self.hand_tile_ids = [HandTile.create(value).tile_id for value in self.hand_tiles]
        self._identity_values = list(self.hand_tiles)
        self.hand_groups.clear()

    def _remove_tile_from_groups(self, tile_id: str) -> None:
        for group in self.hand_groups:
            group.tile_ids = [member for member in group.tile_ids if member != tile_id]
        self._remove_empty_groups()

    def _remove_empty_groups(self) -> None:
        self.hand_groups = [group for group in self.hand_groups if group.tile_ids]

    def _rebuild_visual_order(self, *, loose_ids: Optional[list[str]] = None) -> None:
        values_by_id = dict(zip(self.hand_tile_ids, self.hand_tiles))
        grouped_ids = [
            tile_id for group in self.hand_groups for tile_id in group.tile_ids
        ]
        if len(grouped_ids) != len(set(grouped_ids)):
            raise GameStateError("同一张牌不能属于多个手牌分组")

        if loose_ids is None:
            grouped_set = set(grouped_ids)
            loose_ids = [
                tile_id for tile_id in self.hand_tile_ids if tile_id not in grouped_set
            ]
        desired_ids = grouped_ids + loose_ids
        if len(desired_ids) != len(values_by_id) or set(desired_ids) != set(values_by_id):
            raise GameStateError("手牌分组与实体牌状态不一致")

        self.hand_tile_ids = desired_ids
        self.hand_tiles = [values_by_id[tile_id] for tile_id in desired_ids]
        self._identity_values = list(self.hand_tiles)


@dataclass
class GameState:
    players: list[PlayerState]
    wall: TileWall
    timeout_config: TimeoutConfig = field(default_factory=TimeoutConfig)
    rule_adapter: RuleAdapter = field(
        default_factory=ArithmeticMahjongRuleAdapter,
        repr=False,
    )
    scoring_adapter: ScoringAdapter = field(default_factory=NormalScoringAdapter, repr=False)
    config: GameConfig = field(default_factory=GameConfig)
    game_id: str = field(default_factory=lambda: str(uuid4()))
    round_id: str = field(default_factory=lambda: str(uuid4()))
    dealer_seat: int = 0
    current_turn_seat: int = 0
    phase: GamePhase = GamePhase.INIT
    last_discard: Optional[DiscardRecord] = None
    response_window: Optional[ResponseWindow] = None
    pending_action_detail: Optional[ActionDetailState] = None
    rob_kong_window: Optional[RobKongWindow] = None
    resolved_actions: list[ResolvedAction] = field(default_factory=list)
    turn_options_by_player: dict[str, list[ActionOption]] = field(default_factory=dict)
    turn_window_id: Optional[str] = None
    turn_gang_prompt_passed: bool = False
    turn_hu_prompt_passed: bool = False
    drawn_tile_id: Optional[str] = None
    settlement_events: list[SettlementEvent] = field(default_factory=list)
    round_result: Optional[RoundResult] = None
    active_timer: Optional[TimerState] = None
    discard_history: list[DiscardRecord] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.config.mode == GameMode.BLOOD_BATTLE and type(self.scoring_adapter) is NormalScoringAdapter:
            self.scoring_adapter = BloodBattleScoringAdapter()

    @classmethod
    def new_game(
        cls,
        *,
        player_names: Optional[list[str]] = None,
        human_seat: int = 0,
        dealer_seat: int = 0,
        rng: Optional[random.Random] = None,
        shuffle: bool = True,
        timeout_config: Optional[TimeoutConfig] = None,
        rule_adapter: Optional[RuleAdapter] = None,
        scoring_adapter: Optional[ScoringAdapter] = None,
        config: Optional[GameConfig] = None,
    ) -> "GameState":
        if player_names is None:
            player_names = ["玩家", "下家", "对家", "上家"]
        if len(player_names) != 4:
            raise ValueError("必须提供四个玩家名称")
        if human_seat not in range(4):
            raise ValueError("human_seat 必须在 0 到 3 之间")
        if dealer_seat not in range(4):
            raise ValueError("dealer_seat 必须在 0 到 3 之间")

        players = [
            PlayerState(
                player_id=f"p{seat}",
                seat=seat,
                name=player_names[seat],
                is_human=seat == human_seat,
                is_dealer=seat == dealer_seat,
            )
            for seat in range(4)
        ]
        game = cls(
            players=players,
            wall=TileWall.from_rule_tiles(rng=rng, shuffle=shuffle),
            timeout_config=timeout_config or TimeoutConfig(),
            rule_adapter=rule_adapter or ArithmeticMahjongRuleAdapter(),
            scoring_adapter=scoring_adapter or NormalScoringAdapter(),
            config=config or GameConfig(),
            dealer_seat=dealer_seat,
            current_turn_seat=dealer_seat,
            phase=GamePhase.INIT,
        )
        game.deal_initial_hands()
        return game

    def deal_initial_hands(self, tiles_per_player: int = 13) -> None:
        self._require_phase(GamePhase.INIT)
        self.phase = GamePhase.DEALING
        self._log_event("round_started", dealer_seat=self.dealer_seat)

        for _ in range(tiles_per_player):
            for seat in range(4):
                self.players[seat].add_hand_tile(self.wall.draw())

        self.phase = GamePhase.WAITING_DRAW
        self._log_event("initial_hands_dealt", tiles_per_player=tiles_per_player)

    def draw_tile(self, player_id: str, *, now: float = 0.0) -> TileValue:
        self._require_phase(GamePhase.WAITING_DRAW)
        player = self._require_current_player(player_id)

        if not self.wall.can_draw():
            self.end_round("wall_empty")
            raise GameStateError("牌山已空，本局流局")

        tile = self.wall.draw()
        drawn = player.add_hand_tile(tile)
        self._open_turn_action_window(player, now=now, drawn_tile_id=drawn.tile_id)
        self._log_event("tile_drawn", player_id=player_id, seat=player.seat)
        return tile

    def discard_tile(
        self,
        player_id: str,
        tile: TileValue,
        *,
        now: float = 0.0,
        source_turn_window_id: Optional[str] = None,
    ) -> DiscardRecord:
        """Discard the first matching tile value.

        This is useful for scripts and tests. UI callers should use
        discard_tile_at() so duplicate values keep their positional identity.
        """
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_current_player(player_id)
        self._require_gang_prompt_resolved(player_id)
        player._ensure_hand_identity()
        try:
            hand_index = player.hand_tiles.index(tile)
        except ValueError as exc:
            raise GameStateError(f"玩家 {player_id} 没有这张牌: {tile}") from exc
        tile_id = player.tile_id_at(hand_index)
        discarded_tile = player.remove_tile_at(hand_index)
        return self._record_discard(
            player,
            discarded_tile,
            tile_id=tile_id,
            hand_index=hand_index,
            now=now,
        )

    def discard_tile_at(
        self,
        player_id: str,
        hand_index: int,
        *,
        now: float = 0.0,
        source_turn_window_id: Optional[str] = None,
    ) -> DiscardRecord:
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_current_player(player_id)
        self._require_gang_prompt_resolved(player_id)
        tile_id = player.tile_id_at(hand_index)
        discarded_tile = player.remove_tile_at(hand_index)
        return self._record_discard(
            player,
            discarded_tile,
            tile_id=tile_id,
            hand_index=hand_index,
            now=now,
        )

    def discard_tile_by_id(
        self,
        player_id: str,
        tile_id: str,
        *,
        now: float = 0.0,
        source_turn_window_id: Optional[str] = None,
    ) -> DiscardRecord:
        """Discard the exact physical tile selected by the player."""
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_current_player(player_id)
        self._require_gang_prompt_resolved(player_id)
        hand_index = player._tile_index(tile_id)
        discarded_tile = player.remove_tile_by_id(tile_id)
        return self._record_discard(
            player,
            discarded_tile,
            tile_id=tile_id,
            hand_index=hand_index,
            now=now,
        )

    def reorder_hand_tile(
        self,
        player_id: str,
        from_index: int,
        to_index: int,
        *,
        source_turn_window_id: Optional[str] = None,
    ) -> None:
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_active_player(player_id)
        player.move_tile(from_index, to_index)
        self._log_event(
            "hand_tile_reordered",
            _private_to_player_id=player_id,
            player_id=player_id,
            from_index=from_index,
            to_index=to_index,
        )

    def move_hand_tile_by_id(
        self,
        player_id: str,
        tile_id: str,
        to_index: int,
        *,
        group_id: Optional[str] = None,
        source_turn_window_id: Optional[str] = None,
    ) -> None:
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_active_player(player_id)
        player.move_tile_by_id(tile_id, to_index, group_id=group_id)
        self._log_event(
            "hand_tile_moved",
            _private_to_player_id=player_id,
            player_id=player_id,
            tile_id=tile_id,
            group_id=group_id,
            to_index=to_index,
        )

    def create_hand_group(
        self,
        player_id: str,
        tile_ids: list[str],
        *,
        source_turn_window_id: Optional[str] = None,
    ) -> HandGroup:
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_active_player(player_id)
        group = player.create_hand_group(tile_ids)
        self._log_event(
            "hand_group_created",
            _private_to_player_id=player_id,
            player_id=player_id,
            group_id=group.group_id,
            tile_ids=list(group.tile_ids),
        )
        return group

    def disband_hand_group(
        self,
        player_id: str,
        group_id: str,
        *,
        source_turn_window_id: Optional[str] = None,
    ) -> HandGroup:
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        self._require_turn_window(source_turn_window_id)
        player = self._require_active_player(player_id)
        group = player.disband_hand_group(group_id)
        self._log_event(
            "hand_group_disbanded",
            _private_to_player_id=player_id,
            player_id=player_id,
            group_id=group_id,
            tile_ids=list(group.tile_ids),
        )
        return group

    def apply_chi(self, player_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute chi using canonical display data from ActionOption payload."""
        player = self._require_active_player(player_id)
        hand_values = list(payload.get("hand_tiles", []))
        display_tiles = list(payload["display_tiles"])
        claimed_display_index = payload["claimed_display_index"]

        selected_tile_ids = payload.get("selected_tile_ids")
        if selected_tile_ids is None:
            player.remove_leftmost_values(hand_values)
        else:
            player.remove_tiles_by_ids(list(selected_tile_ids))
        meld = make_chi_meld(display_tiles, claimed_display_index)
        player.melded_groups.append(meld)
        self._log_event("meld_created", player_id=player_id, group_type="吃", meld=meld)
        return meld

    def apply_peng(
        self,
        player_id: str,
        tile: TileValue,
        *,
        selected_tile_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Execute peng; remove the leftmost three matching hand tiles."""
        player = self._require_active_player(player_id)
        if selected_tile_ids is None:
            player.remove_leftmost_values([tile] * 3)
        else:
            player.remove_tiles_by_ids(selected_tile_ids)
        meld = make_peng_meld(tile)
        player.melded_groups.append(meld)
        self._log_event("meld_created", player_id=player_id, group_type="碰", meld=meld)
        return meld

    def apply_ming_gang(
        self,
        player_id: str,
        tile: TileValue,
        *,
        selected_tile_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Execute ming-gang; remove the leftmost four matching hand tiles."""
        player = self._require_active_player(player_id)
        if selected_tile_ids is None:
            player.remove_leftmost_values([tile] * 4)
        else:
            player.remove_tiles_by_ids(selected_tile_ids)
        meld = make_ming_gang_meld(tile)
        player.melded_groups.append(meld)
        self._log_event("meld_created", player_id=player_id, group_type="明杠", meld=meld)
        return meld

    def apply_an_gang(
        self,
        player_id: str,
        tile: TileValue,
        *,
        selected_tile_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Execute an-gang using preselected physical tiles when provided."""
        player = self._require_active_player(player_id)
        if selected_tile_ids is None:
            player.remove_leftmost_values([tile] * 5)
        else:
            player.remove_tiles_by_ids(selected_tile_ids)
        meld = make_an_gang_meld(tile)
        player.melded_groups.append(meld)
        self._log_event("meld_created", player_id=player_id, group_type="暗杠", meld=meld)
        return meld

    def apply_jia_gang(
        self,
        player_id: str,
        *,
        meld_index: int,
        tile: TileValue,
        selected_tile_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Execute jia-gang using a preselected physical tile when provided."""
        player = self._require_active_player(player_id)
        if meld_index < 0 or meld_index >= len(player.melded_groups):
            raise GameStateError(f"明牌组位置越界: {meld_index}")
        target_meld = player.melded_groups[meld_index]
        if target_meld.get("group_type") != "碰":
            raise GameStateError("只有碰牌组可以加杠")
        if tile not in target_meld.get("tiles", []):
            raise GameStateError("加杠牌值与碰牌组不一致")

        if selected_tile_ids is None:
            player.remove_leftmost_values([tile])
        else:
            player.remove_tiles_by_ids(selected_tile_ids)
        meld = upgrade_peng_to_jia_gang(target_meld, tile)
        self._log_event(
            "meld_upgraded",
            player_id=player_id,
            group_type="加杠",
            meld_index=meld_index,
            meld=meld,
        )
        return meld

    def submit_turn_action(
        self,
        player_id: str,
        action_type: ActionType,
        *,
        option_id: Optional[str] = None,
        now: float = 0.0,
        source_turn_window_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Submit self draw, PASS, an-gang, or jia-gang in the current t3."""
        self._require_phase(GamePhase.WAITING_TURN_ACTION)
        player = self._require_current_player(player_id)
        self._require_turn_window(source_turn_window_id)
        if self.active_timer is not None and self.active_timer.is_expired(now):
            self._expire_turn_decision(now)
            raise GameStateError("t3 已超时，系统已执行默认弃牌")

        if action_type == ActionType.PASS:
            if option_id is not None:
                raise GameStateError("t3 PASS 不接受 option_id")
            if not self._has_unpassed_gang_prompt(player_id) and not (
                not self.turn_hu_prompt_passed
                and any(
                    option.action_type == ActionType.HU
                    for option in self.turn_options_by_player.get(player_id, [])
                )
            ):
                raise GameStateError("当前没有需要跳过的胡牌或杠牌提示")
            self.turn_gang_prompt_passed = True
            self.turn_hu_prompt_passed = True
            self._log_event(
                "turn_gang_prompt_passed",
                player_id=player_id,
                turn_window_id=self.turn_window_id,
            )
            return None

        if action_type == ActionType.HU:
            if option_id is not None:
                raise GameStateError("胡牌自动选择最高番，不接受 option_id")
            if self.turn_hu_prompt_passed or self.drawn_tile_id is None:
                raise GameStateError("当前不能自摸胡牌")
            if not any(
                option.action_type == ActionType.HU
                for option in self.turn_options_by_player.get(player_id, [])
            ):
                raise GameStateError("当前没有自摸选项")
            self._settle_wins(
                [WinContext(
                    player_id, player.tile_value(self.drawn_tile_id), WinMethod.SELF_DRAW,
                    is_heavenly=self._is_heavenly_candidate(player_id),
                )],
                reason="self_draw",
            )
            return self.round_result.to_dict() if self.round_result else self.settlement_events[-1].to_dict()

        if action_type not in (ActionType.AN_GANG, ActionType.JIA_GANG):
            raise GameStateError(f"t3 动作 {action_type.value} 尚未接入执行流程")
        if self.turn_gang_prompt_passed:
            raise GameStateError("当前 t3 已跳过杠牌提示")
        if not self.wall.can_draw():
            raise GameStateError("牌山已空，不能杠牌")

        option = next(
            (
                candidate
                for candidate in self.turn_options_by_player.get(player_id, [])
                if candidate.action_type == action_type
            ),
            None,
        )
        if option is None:
            raise GameStateError(f"玩家 {player_id} 当前不能提交 {action_type.value}")

        payload = self._select_turn_payload(option, option_id)
        try:
            if action_type == ActionType.AN_GANG:
                required_values = list(payload["required_values"])
                selection = select_tiles(player, required_values)
            else:
                selection = select_tiles_by_region_order(
                    player,
                    list(payload["required_values"]),
                )
        except (KeyError, TileSelectionError, GameStateError) as exc:
            raise GameStateError(f"杠牌选项已经失效: {exc}") from exc

        resolved_payload = dict(payload)
        resolved_payload["selected_tile_ids"] = list(selection.tile_ids)
        resolved_payload["tile_selection"] = selection.to_dict()
        if action_type == ActionType.JIA_GANG and self._open_rob_kong_window(
            player, resolved_payload, now=now,
        ):
            return None
        return self._complete_turn_gang(player_id, action_type, resolved_payload, now=now)

    def _complete_turn_gang(
        self,
        player_id: str,
        action_type: ActionType,
        payload: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        """Commit the meld and draw only after any rob-kong window has passed."""
        if not self.wall.can_draw():
            raise GameStateError("牌山已空，不能杠牌")
        player = self.get_player(player_id)
        if action_type == ActionType.AN_GANG:
            meld = self.apply_an_gang(
                player_id, payload["tile"], selected_tile_ids=payload["selected_tile_ids"],
            )
        else:
            meld = self.apply_jia_gang(
                player_id,
                meld_index=payload["meld_index"],
                tile=payload["tile"],
                selected_tile_ids=payload["selected_tile_ids"],
            )
        self.resolved_actions.append(
            ResolvedAction(
                action_type=action_type,
                player_id=player_id,
                payload=payload,
                reason="turn_gang_selected",
            )
        )

        replacement_tile = self.wall.draw()
        drawn = player.add_hand_tile(replacement_tile)
        self._open_turn_action_window(player, now=now, drawn_tile_id=drawn.tile_id)
        self._log_event(
            "gang_replacement_drawn",
            player_id=player_id,
            action_type=action_type.value,
            wall_remaining=self.wall.remaining_count(),
        )
        return meld

    def _record_discard(
        self,
        player: PlayerState,
        tile: TileValue,
        *,
        tile_id: str,
        hand_index: Optional[int],
        now: float,
    ) -> DiscardRecord:
        player.discarded_tiles.append(tile)
        record = DiscardRecord(
            player_id=player.player_id,
            seat=player.seat,
            tile=tile,
            sequence=len(self.discard_history),
            tile_id=tile_id,
        )
        self.last_discard = record
        self.discard_history.append(record)
        self.active_timer = None
        self.turn_options_by_player.clear()
        self.turn_window_id = None
        self.turn_gang_prompt_passed = False
        self.turn_hu_prompt_passed = False
        self.drawn_tile_id = None
        self.response_window = self._create_response_window(record, now=now)
        self.phase = GamePhase.WAITING_RESPONSE
        self._log_event("tile_discarded", hand_index=hand_index, **record.to_dict())
        return record

    def _open_rob_kong_window(
        self,
        player: PlayerState,
        payload: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        """Pause t3 only when a proposed jia-gang has eligible robbers."""
        meld_index = payload["meld_index"]
        if meld_index < 0 or meld_index >= len(player.melded_groups):
            raise GameStateError("待加杠的碰组已经失效")
        meld = player.melded_groups[meld_index]
        if meld.get("group_type") != "碰" or meld.get("tiles") != [payload["tile"]] * 4:
            raise GameStateError("待加杠的碰组已经失效")

        options_by_player = {}
        for other in self.players:
            if other.player_id == player.player_id or other.is_out:
                continue
            option = build_hu_response_option(
                player=other,
                discarded_tile=payload["tile"],
                source_player_id=player.player_id,
                rule_adapter=self.rule_adapter,
                win_method=WinMethod.ROB_KONG,
                is_heavenly=self._is_heavenly_candidate(other.player_id),
            )
            if option is not None:
                options_by_player[other.player_id] = [option, ActionOption(ActionType.PASS)]
        if not options_by_player:
            return False

        timeout = self.timeout_config.rob_kong_seconds
        self.rob_kong_window = RobKongWindow(
            source_player_id=player.player_id,
            tile=payload["tile"],
            payload=payload,
            selected_tile_ids=list(payload["selected_tile_ids"]),
            opened_at=now,
            deadline_at=None if timeout is None else now + timeout,
            options_by_player=options_by_player,
            paused_turn_timer=self.active_timer,
            source_turn_window_id=self.turn_window_id,
        )
        self.phase = GamePhase.WAITING_ROB_KONG
        self._start_timer_if_configured(TimerKind.ROB_KONG_RESPONSE, now=now)
        self._log_event("jia_gang_declared", player_id=player.player_id, tile=payload["tile"])
        return True

    def submit_rob_kong_response(
        self,
        player_id: str,
        action_type: ActionType,
        *,
        now: Optional[float] = None,
        source_window_id: Optional[str] = None,
    ) -> None:
        """Accept one final HU/PASS from each eligible player during t4."""
        self._require_phase(GamePhase.WAITING_ROB_KONG)
        window = self.rob_kong_window
        if window is None:
            raise GameStateError("当前没有抢杠窗口")
        if source_window_id is not None and source_window_id != window.window_id:
            raise GameStateError("抢杠窗口已经过期")
        if player_id not in window.options_by_player:
            raise GameStateError("该玩家没有抢杠资格")
        timestamp = window.opened_at if now is None else now
        if timestamp < window.opened_at:
            raise GameStateError("抢杠响应时间早于窗口开始时间")
        if window.deadline_at is not None and timestamp >= window.deadline_at:
            self._expire_rob_kong_window(timestamp)
            return
        if action_type not in (ActionType.HU, ActionType.PASS):
            raise GameStateError("抢杠只能选择胡或过")
        if player_id in window.requests_by_player:
            raise GameStateError("该玩家已经提交抢杠响应")
        window.requests_by_player[player_id] = ActionRequest(
            player_id=player_id,
            action_type=action_type,
            submitted_at=timestamp,
            source_window_id=window.window_id,
        )
        if window.is_complete():
            try:
                self._resolve_rob_kong_window(now=timestamp)
            except GameStateError:
                if self.rob_kong_window is window:
                    window.requests_by_player.pop(player_id)
                raise

    def _resolve_rob_kong_window(self, *, now: float) -> None:
        window = self.rob_kong_window
        if window is None:
            raise GameStateError("当前没有抢杠窗口")
        source = self.get_player(window.source_player_id)
        winners = sorted(
            (
                request for request in window.requests_by_player.values()
                if request.action_type == ActionType.HU
            ),
            key=lambda request: (self.get_player(request.player_id).seat - source.seat) % 4,
        )
        if winners:
            if not self.config.allow_multiple_hu:
                winners = winners[:1]
            self._settle_wins(
                [
                    WinContext(
                        request.player_id, window.tile, WinMethod.ROB_KONG, source.player_id,
                        is_heavenly=self._is_heavenly_candidate(request.player_id),
                    )
                    for request in winners
                ],
                reason="rob_kong",
                robbed_kong=window,
            )
            return

        self._complete_turn_gang(source.player_id, ActionType.JIA_GANG, window.payload, now=now)
        self.rob_kong_window = None
        # t4 is excluded from the original t3 budget, including after replacement draw.
        if window.paused_turn_timer is not None:
            pause_seconds = now - window.opened_at
            self.active_timer = replace(
                window.paused_turn_timer,
                started_at=window.paused_turn_timer.started_at + pause_seconds,
                deadline_at=window.paused_turn_timer.deadline_at + pause_seconds,
            )
        else:
            self.active_timer = None
        self._log_event("rob_kong_passed", player_id=source.player_id)

    def _expire_rob_kong_window(self, now: float) -> None:
        window = self.rob_kong_window
        if window is None:
            return
        for player_id in window.options_by_player:
            if player_id not in window.requests_by_player:
                window.requests_by_player[player_id] = ActionRequest(
                    player_id=player_id,
                    action_type=ActionType.PASS,
                    submitted_at=now,
                    source_window_id=window.window_id,
                )
        self._resolve_rob_kong_window(now=now)

    def pass_response(self, player_id: str, *, now: Optional[float] = None) -> None:
        self.submit_response(player_id, ActionType.PASS, now=now)

    def submit_response(
        self,
        player_id: str,
        action_type: ActionType,
        *,
        now: Optional[float] = None,
        source_window_id: Optional[str] = None,
    ) -> None:
        """Submit one final t1 response after validating the offered options."""
        self._require_phase(GamePhase.WAITING_RESPONSE)
        if self.response_window is None:
            raise GameStateError("当前没有响应窗口")
        window = self.response_window
        if source_window_id is not None and source_window_id != window.window_id:
            raise GameStateError("响应窗口已经过期")
        if (
            now is not None
            and window.deadline_at is not None
            and now >= window.deadline_at
        ):
            self._expire_response_window(now)
            return
        if player_id not in window.players():
            raise GameStateError(f"玩家 {player_id} 当前没有可响应动作")
        if player_id in window.auto_passed_players:
            raise GameStateError(f"玩家 {player_id} 已被静默过牌")
        if player_id in window.requests_by_player:
            raise GameStateError(f"玩家 {player_id} 已经提交过响应")

        offered_types = {
            option.action_type for option in window.options_by_player[player_id]
        }
        if action_type not in offered_types:
            raise GameStateError(f"玩家 {player_id} 当前不能提交 {action_type.value}")

        window.submit(
            ActionRequest(
                player_id=player_id,
                action_type=action_type,
                submitted_at=now,
                source_window_id=window.window_id,
            )
        )
        self._log_event(
            "response_submitted",
            player_id=player_id,
            action_type=action_type.value,
        )

        # Timed t1 is fixed-duration, even when everyone already passed.
        # Unlike t4, only an untimed t1 may resolve after the last submission.
        if window.deadline_at is None and window.is_complete():
            try:
                self._resolve_response_window(
                    "all_responses_submitted",
                    now=window.opened_at if now is None else now,
                )
            except GameStateError:
                if self.response_window is window and window.status == ResponseWindowStatus.OPEN:
                    window.requests_by_player.pop(player_id)
                raise

    def submit_chi_detail(
        self,
        player_id: str,
        *,
        option_id: Optional[str] = None,
        selected_tile_ids: Optional[list[str]] = None,
        now: Optional[float] = None,
        detail_id: Optional[str] = None,
    ) -> bool:
        """Resolve t2 chi selection; any missing or invalid selection becomes PASS."""
        self._require_phase(GamePhase.WAITING_ACTION_DETAIL)
        detail = self.pending_action_detail
        if detail is None or detail.action_type != ActionType.CHI:
            raise GameStateError("当前没有待选择的吃牌动作")
        if detail.player_id != player_id:
            raise GameStateError(f"当前不是玩家 {player_id} 选择吃牌细节")
        if detail_id is not None and detail_id != detail.detail_id:
            raise GameStateError("动作细节窗口已经过期")
        if (
            now is not None
            and detail.deadline_at is not None
            and now >= detail.deadline_at
        ):
            return self._pass_action_detail("timeout")

        player = self.get_player(player_id)
        try:
            if detail.selection_mode == "listed":
                if option_id is None or selected_tile_ids is not None:
                    return self._pass_action_detail("missing_or_invalid_selection")
                option = next(
                    (
                        payload
                        for payload in detail.payload_options
                        if payload.get("option_id") == option_id
                    ),
                    None,
                )
                if option is None:
                    return self._pass_action_detail("unknown_chi_option")
                required_values = list(option["required_values"])
                selection = select_tiles(player, required_values)
                payload = dict(option)
            else:
                if selected_tile_ids is None or len(selected_tile_ids) != 3:
                    return self._pass_action_detail("invalid_manual_tile_count")
                hand_values = [player.tile_value(tile_id) for tile_id in selected_tile_ids]
                if not is_valid_chi_selection(hand_values, detail.claimed_tile):
                    return self._pass_action_detail("invalid_manual_formula")
                selection = select_tiles(
                    player,
                    hand_values,
                    selected_tile_ids=selected_tile_ids,
                    allow_explicit_selection=True,
                )
                payload = build_chi_detail_payload(hand_values, detail.claimed_tile)
        except (GameStateError, TileSelectionError, ValueError):
            return self._pass_action_detail("invalid_chi_selection")

        payload["selected_tile_ids"] = list(selection.tile_ids)
        payload["tile_selection"] = selection.to_dict()
        self.apply_chi(player_id, payload)
        self._claim_last_discard(detail.source_player_id, detail.claimed_tile)
        self.resolved_actions.append(
            ResolvedAction(
                action_type=ActionType.CHI,
                player_id=player_id,
                source_player_id=detail.source_player_id,
                claimed_tile=detail.claimed_tile,
                payload=payload,
                reason="chi_detail_selected",
            )
        )
        self.pending_action_detail = None
        self.active_timer = None
        self._begin_turn_without_draw(player, now=detail.opened_at if now is None else now)
        return True

    def pass_action_detail(self, player_id: str) -> None:
        self._require_phase(GamePhase.WAITING_ACTION_DETAIL)
        detail = self.pending_action_detail
        if detail is None or detail.player_id != player_id:
            raise GameStateError(f"玩家 {player_id} 当前没有可放弃的动作细节")
        self._pass_action_detail("player_passed")

    def expire_timers(self, now: float) -> None:
        if self.active_timer is None or not self.active_timer.is_expired(now):
            return

        timer_kind = self.active_timer.kind
        self._log_event("timer_expired", timer_kind=timer_kind.value, now=now)

        if timer_kind == TimerKind.RESPONSE_WINDOW:
            self._expire_response_window(now)
        elif timer_kind == TimerKind.TURN_DECISION:
            self._expire_turn_decision(now)
        elif timer_kind == TimerKind.DETAIL_SELECTION:
            self._log_event("detail_selection_expired", now=now)
            self._pass_action_detail("timeout")
        elif timer_kind == TimerKind.ROB_KONG_RESPONSE:
            self._expire_rob_kong_window(now)

    def _is_heavenly_candidate(self, player_id: str) -> bool:
        # Claimed discards leave the player's pile but remain in round history.
        return not any(record.player_id == player_id for record in self.discard_history)

    def _settle_wins(
        self,
        contexts: list[WinContext],
        *,
        reason: str,
        robbed_kong: Optional[RobKongWindow] = None,
    ) -> None:
        """Prepare every winner's result before applying any score changes."""
        events = []
        winners = [context.winner_id for context in contexts]
        if not winners or len(set(winners)) != len(winners):
            raise GameStateError("胡牌玩家不能为空或重复")
        blood = self.config.mode == GameMode.BLOOD_BATTLE
        if blood and len(self.settlement_events) + len(winners) > 3:
            raise GameStateError("血战最多三位赢家")
        try:
            for context in contexts:
                player = self._require_active_player(context.winner_id)
                result = self.rule_adapter.evaluate_win(HandSnapshot.from_player(player), context)
                if not result.can_win:
                    raise GameStateError(result.error or "胡牌条件已不满足")
                event = self.scoring_adapter.settle_win(
                    result,
                    SettlementContext(
                        round_id=self.round_id,
                        winner_id=player.player_id,
                        source_player_id=context.source_player_id,
                        eligible_payer_ids=tuple(
                            other.player_id for other in self.players
                            if other.player_id != player.player_id and not other.is_out
                        ),
                    ),
                )
                delta_ids = [delta.player_id for delta in event.score_deltas]
                if (
                    event.round_id != self.round_id
                    or event.winner_id != player.player_id
                    or event.source_player_id != context.source_player_id
                    or event.win_result != result
                    or len(delta_ids) != len(set(delta_ids))
                    or not set(delta_ids).issubset({p.player_id for p in self.players if not p.is_out})
                    or not delta_ids
                    or any(type(delta.delta) is not int for delta in event.score_deltas)
                    or sum(delta.delta for delta in event.score_deltas) != 0
                ):
                    raise GameStateError("结算器返回了无效的结算记录")
                if blood:
                    event = replace(event, win_order=len(self.settlement_events) + len(events) + 1)
                events.append(event)
        except ValueError as exc:
            raise GameStateError(f"无法结算: {exc}") from exc

        if robbed_kong is not None:
            source = self.get_player(robbed_kong.source_player_id)
            if (
                len(robbed_kong.selected_tile_ids) != 1
                or source.tile_value(robbed_kong.selected_tile_ids[0]) != robbed_kong.tile
            ):
                raise GameStateError("待抢杠牌已经失效")
            source.remove_tiles_by_ids(robbed_kong.selected_tile_ids)
        self.phase = GamePhase.SETTLING
        self.active_timer = None
        self._log_event("settlement_started", winner_ids=[event.winner_id for event in events])
        for event in events:
            winner = self.get_player(event.winner_id)
            winner.is_winner = True
            if blood:
                winner.is_out = True
                winner.win_order = event.win_order
                winner.win_settlement_id = event.settlement_id
            for delta in event.score_deltas:
                self.get_player(delta.player_id).score += delta.delta
            self.settlement_events.append(event)
            self.resolved_actions.append(
                ResolvedAction(
                    action_type=ActionType.HU,
                    player_id=event.winner_id,
                    source_player_id=event.source_player_id,
                    claimed_tile=event.win_result.winning_tile,
                    payload=event.win_result.to_action_payload(),
                    reason=reason,
                )
            )
            self._log_event("win_settled", settlement=event.to_dict())
        if not blood:
            self.end_round("hu")
        elif len(self.settlement_events) == 3:
            self.end_round("blood_battle_complete")
        else:
            self._clear_action_windows()
            self._advance_after_unclaimed_discard(self.get_player(events[-1].winner_id).seat, "blood_win")

    def end_round(self, reason: str) -> None:
        """Finish once and expose the immutable result until manual restart."""
        if self.phase == GamePhase.ROUND_ENDED:
            raise GameStateError("本局已经结束")
        end_reason = RoundEndReason(reason)
        if end_reason == RoundEndReason.BLOOD_BATTLE_COMPLETE and (
            self.config.mode != GameMode.BLOOD_BATTLE or len(self.settlement_events) != 3
        ):
            raise GameStateError("只有血战已有三位赢家时才能结束")
        if self.config.mode == GameMode.BLOOD_BATTLE and end_reason == RoundEndReason.HU:
            raise GameStateError("血战只能三家胡牌或牌山摸空后结束")
        if end_reason == RoundEndReason.HU and not self.settlement_events:
            raise GameStateError("胡牌必须先生成结算记录")
        if self.phase != GamePhase.SETTLING:
            self.phase = GamePhase.SETTLING
            self._log_event("settlement_started", winner_ids=[])
        deltas = {player.player_id: 0 for player in self.players}
        for event in self.settlement_events:
            for delta in event.score_deltas:
                deltas[delta.player_id] += delta.delta
        self._log_event("round_ended", reason=reason)
        public_kinds = {"round_started", "initial_hands_dealt", "tile_drawn", "tile_discarded",
                        "meld_created", "meld_upgraded", "jia_gang_declared", "gang_replacement_drawn",
                        "win_settled", "round_ended"}
        public_log = tuple({key: value for key, value in event.items()
                            if key in {"sequence", "type", "player_id", "seat", "reason"}
                            or key == "tile" and event["type"] in {"tile_discarded", "jia_gang_declared"}}
                           for event in self.event_log if event["type"] in public_kinds
                           and "_private_to_player_id" not in event)
        self.round_result = RoundResult(
            round_id=self.round_id,
            end_reason=end_reason,
            dealer_seat=self.dealer_seat,
            next_dealer_seat=self._next_seat(self.dealer_seat),
            winner_ids=tuple(event.winner_id for event in self.settlement_events),
            settlement_events=tuple(self.settlement_events),
            final_scores=tuple(PlayerScore(player.player_id, player.score) for player in self.players),
            score_deltas=tuple(ScoreDelta(player_id, delta) for player_id, delta in deltas.items()),
            mode=self.config.mode,
            remaining_player_ids=tuple(player.player_id for player in self.players if not player.is_winner),
            final_player_states=tuple(FinalPlayerState(
                player.player_id, player.seat, player.score, player.is_winner, player.is_out,
                player.win_order, player.win_settlement_id,
            ) for player in self.players),
            event_log=public_log,
        )
        self.phase = GamePhase.ROUND_ENDED
        self._clear_action_windows()

    def _clear_action_windows(self) -> None:
        self.response_window = None
        self.pending_action_detail = None
        self.rob_kong_window = None
        self.turn_options_by_player.clear()
        self.turn_window_id = None
        self.turn_gang_prompt_passed = False
        self.turn_hu_prompt_passed = False
        self.drawn_tile_id = None
        self.active_timer = None

    def start_next_round(
        self,
        *,
        rng: Optional[random.Random] = None,
        shuffle: bool = True,
        source_round_id: Optional[str] = None,
    ) -> None:
        """Keep cumulative scores; rotate the dealer and deal only on request."""
        self._require_phase(GamePhase.ROUND_ENDED)
        if source_round_id is not None and source_round_id != self.round_id:
            raise GameStateError("下一局请求对应的牌局已过期")
        wall = TileWall.from_rule_tiles(rng=rng, shuffle=shuffle)
        self.dealer_seat = self._next_seat(self.dealer_seat)
        self.current_turn_seat = self.dealer_seat
        self.wall = wall
        self.round_id = str(uuid4())
        for player in self.players:
            player.hand_tiles.clear()
            player._reset_hand_identity()
            player.melded_groups.clear()
            player.discarded_tiles.clear()
            player.is_ready = False
            player.is_winner = False
            player.is_out = False
            player.win_order = None
            player.win_settlement_id = None
            player.is_dealer = player.seat == self.dealer_seat
        self.last_discard = None
        self.response_window = None
        self.pending_action_detail = None
        self.rob_kong_window = None
        self.resolved_actions.clear()
        self.turn_options_by_player.clear()
        self.turn_window_id = None
        self.turn_gang_prompt_passed = False
        self.turn_hu_prompt_passed = False
        self.drawn_tile_id = None
        self.active_timer = None
        self.settlement_events.clear()
        self.round_result = None
        self.discard_history.clear()
        self.event_log.clear()
        self.phase = GamePhase.INIT
        self.deal_initial_hands()

    def get_player(self, player_id: str) -> PlayerState:
        for player in self.players:
            if player.player_id == player_id:
                return player
        raise GameStateError(f"未知玩家: {player_id}")

    def current_player(self) -> PlayerState:
        return self.players[self.current_turn_seat]

    def to_public_dict(
        self,
        *,
        reveal_hands: bool = False,
        viewer_player_id: Optional[str] = None,
    ) -> dict[str, Any]:
        response_view = self.response_window.to_dict() if self.response_window else None
        if response_view is not None:
            if not self._timer_visible_to(viewer_player_id):
                response_view.pop("opened_at")
                response_view.pop("deadline_at")
            if not reveal_hands:
                response_view["options_by_player"] = {
                    viewer_player_id: [
                        option.to_dict()
                        for option in self.response_window.visible_options_for_player(viewer_player_id)
                    ]
                } if viewer_player_id in self.response_window.options_by_player else {}
                response_view["requests_by_player"] = {
                    player_id: request for player_id, request in response_view["requests_by_player"].items()
                    if player_id == viewer_player_id
                }
                response_view["auto_passed_players"] = [
                    player_id for player_id in response_view["auto_passed_players"]
                    if player_id == viewer_player_id
                ]

        detail = self.pending_action_detail
        detail_view = detail.to_dict(
            include_options=reveal_hands or viewer_player_id == detail.player_id,
        ) if detail else None
        if detail_view is not None and viewer_player_id != detail.player_id:
            detail_view.pop("opened_at")
            detail_view.pop("deadline_at")

        rob_window = self.rob_kong_window
        rob_view = rob_window.to_player_dict(viewer_player_id) if (
            rob_window is not None and viewer_player_id in rob_window.options_by_player
        ) else None
        paused_remaining = None
        if (
            rob_window is not None
            and viewer_player_id == rob_window.source_player_id
            and rob_window.paused_turn_timer is not None
        ):
            paused_remaining = rob_window.paused_turn_timer.remaining_seconds(rob_window.opened_at)
        owns_turn = viewer_player_id == self.current_player().player_id
        return {
            "game_id": self.game_id,
            "round_id": self.round_id,
            "dealer_seat": self.dealer_seat,
            "current_turn_seat": self.current_turn_seat,
            "phase": self.phase.value,
            "config": {"allow_multiple_hu": self.config.allow_multiple_hu, "mode": self.config.mode.value},
            "settlement_events": [event.to_dict() for event in self.settlement_events],
            "round_result": self.round_result.to_dict() if self.round_result else None,
            "wall": self.wall.to_dict(),
            "active_timer": self.active_timer.to_dict() if (
                self.active_timer and self._timer_visible_to(viewer_player_id)
            ) else None,
            "paused_turn_remaining_seconds": paused_remaining,
            "turn_window_id": self.turn_window_id,
            "turn_gang_prompt_passed": self.turn_gang_prompt_passed if owns_turn else None,
            "turn_hu_prompt_passed": self.turn_hu_prompt_passed if owns_turn else None,
            "players": [
                player.to_public_dict(
                    reveal_hand=reveal_hands
                    or player.player_id == viewer_player_id
                )
                for player in self.players
            ],
            "last_discard": self.last_discard.to_dict() if self.last_discard else None,
            "response_window": response_view,
            "pending_action_detail": detail_view,
            "rob_kong_window": rob_view,
            "resolved_actions": [
                action.to_dict() for action in self.resolved_actions
            ],
            "turn_options_by_player": {
                player_id: [option.to_dict() for option in options]
                for player_id, options in self.turn_options_by_player.items()
                if self.phase == GamePhase.WAITING_TURN_ACTION
                and (reveal_hands or player_id == viewer_player_id)
            },
            "pending_responses": {
                player_id: submitted for player_id, submitted in self._pending_response_snapshot().items()
                if reveal_hands or player_id == viewer_player_id
            },
            "discard_history": [record.to_dict() for record in self.discard_history],
            "event_log": self._visible_event_log(
                viewer_player_id=viewer_player_id,
                reveal_private=reveal_hands,
            ),
        }

    def _timer_visible_to(self, player_id: Optional[str]) -> bool:
        """Countdown ownership is independent of debug hand visibility."""
        if player_id is None or self.active_timer is None:
            return False
        if self.active_timer.kind == TimerKind.TURN_DECISION:
            return player_id == self.current_player().player_id
        if self.active_timer.kind == TimerKind.DETAIL_SELECTION:
            return self.pending_action_detail is not None and self.pending_action_detail.player_id == player_id
        if self.active_timer.kind == TimerKind.RESPONSE_WINDOW:
            return self.response_window is not None and bool(
                self.response_window.visible_options_for_player(player_id)
            )
        if self.active_timer.kind == TimerKind.ROB_KONG_RESPONSE:
            return self.rob_kong_window is not None and player_id in self.rob_kong_window.options_by_player
        return False

    def _visible_event_log(
        self,
        *,
        viewer_player_id: Optional[str],
        reveal_private: bool,
    ) -> list[dict[str, Any]]:
        visible_events = []
        for event in self.event_log:
            private_to = event.get("_private_to_player_id")
            if (
                private_to is not None
                and not reveal_private
                and private_to != viewer_player_id
            ):
                continue
            visible_events.append(
                {
                    key: value
                    for key, value in event.items()
                    if key != "_private_to_player_id"
                }
            )
        return visible_events

    def _create_response_window(
        self,
        record: DiscardRecord,
        *,
        now: float,
    ) -> ResponseWindow:
        source_player = self.get_player(record.player_id)
        options_by_player = {}
        for other in self.players:
            if other.player_id == record.player_id or other.is_out:
                continue
            options_by_player[other.player_id] = build_discard_response_options(
                player=other,
                source_player=source_player,
                discarded_tile=record.tile,
                wall_remaining=self.wall.remaining_count(),
                rule_adapter=self.rule_adapter,
                is_heavenly=self._is_heavenly_candidate(other.player_id),
                chi_player_seat=self._next_active_seat(record.seat),
            )
        opened_at = now
        timeout = self.timeout_config.discard_response_seconds
        deadline_at = None if timeout is None else opened_at + timeout
        window = ResponseWindow(
            source_player_id=record.player_id,
            discard_record=record,
            opened_at=opened_at,
            deadline_at=deadline_at,
            options_by_player=options_by_player,
        )

        if timeout is None:
            self.active_timer = None
            return window

        for player_id, options in options_by_player.items():
            if self._only_pass_options(options):
                window.auto_passed_players.add(player_id)

        self.active_timer = TimerState.start(
            TimerKind.RESPONSE_WINDOW,
            opened_at,
            timeout,
        )
        return window

    def _resolve_response_window(self, reason: str, *, now: float) -> None:
        if self.response_window is None:
            raise GameStateError("当前没有响应窗口")

        window = self.response_window
        winners = resolve_response_requests(
            window,
            {player.player_id: player for player in self.players},
            allow_multiple_hu=self.config.allow_multiple_hu,
        )
        if winners and winners[0].action_type == ActionType.HU:
            self._settle_wins(
                [
                    WinContext(
                        winner_id=request.player_id,
                        winning_tile=window.discard_record.tile,
                        win_method=WinMethod.DISCARD,
                        source_player_id=window.source_player_id,
                        is_heavenly=self._is_heavenly_candidate(request.player_id),
                    )
                    for request in winners
                ],
                reason=reason,
            )
            window.status = ResponseWindowStatus.RESOLVED
            return

        window.status = ResponseWindowStatus.RESOLVED
        self.response_window = None
        self.active_timer = None

        if not winners:
            self._advance_after_unclaimed_discard(window.discard_record.seat, reason)
            return

        request = winners[0]
        player = self.get_player(request.player_id)
        claimed_tile = window.discard_record.tile
        if request.action_type == ActionType.CHI:
            self._begin_chi_detail(window, request, now=now)
            return

        if request.action_type == ActionType.PENG:
            selection = select_tiles(player, [claimed_tile] * 3)
            self.apply_peng(
                player.player_id,
                claimed_tile,
                selected_tile_ids=selection.tile_ids,
            )
            self._claim_last_discard(window.source_player_id, claimed_tile)
            self.resolved_actions.append(
                ResolvedAction(
                    action_type=ActionType.PENG,
                    player_id=player.player_id,
                    source_player_id=window.source_player_id,
                    claimed_tile=claimed_tile,
                    payload={"tile_selection": selection.to_dict()},
                    reason=reason,
                )
            )
            self._begin_turn_without_draw(player, now=now)
            return

        if request.action_type == ActionType.MING_GANG:
            selection = select_tiles(player, [claimed_tile] * 4)
            self.apply_ming_gang(
                player.player_id,
                claimed_tile,
                selected_tile_ids=selection.tile_ids,
            )
            self._claim_last_discard(window.source_player_id, claimed_tile)
            self.resolved_actions.append(
                ResolvedAction(
                    action_type=ActionType.MING_GANG,
                    player_id=player.player_id,
                    source_player_id=window.source_player_id,
                    claimed_tile=claimed_tile,
                    payload={"tile_selection": selection.to_dict()},
                    reason=reason,
                )
            )
            self.current_turn_seat = player.seat
            self.phase = GamePhase.WAITING_DRAW
            self.draw_tile(player.player_id, now=now)
            return

        raise GameStateError(f"尚未支持裁决动作: {request.action_type.value}")

    def _expire_response_window(self, now: float) -> None:
        if self.response_window is None:
            self.active_timer = None
            return

        self.response_window.auto_pass_unsubmitted(submitted_at=now)
        self.response_window.status = ResponseWindowStatus.EXPIRED
        self._resolve_response_window("timeout", now=now)

    def _begin_chi_detail(
        self,
        window: ResponseWindow,
        request: ActionRequest,
        *,
        now: float,
    ) -> None:
        option = self._response_option(window, request.player_id, ActionType.CHI)
        selection_mode = option.payload_options[0]["selection_mode"]
        timeout = self.timeout_config.action_detail_seconds
        self.pending_action_detail = ActionDetailState(
            player_id=request.player_id,
            action_type=ActionType.CHI,
            source_player_id=window.source_player_id,
            claimed_tile=window.discard_record.tile,
            selection_mode=selection_mode,
            payload_options=list(option.payload_options),
            opened_at=now,
            deadline_at=None if timeout is None else now + timeout,
            source_window_id=window.window_id,
        )
        self.phase = GamePhase.WAITING_ACTION_DETAIL
        self._start_timer_if_configured(TimerKind.DETAIL_SELECTION, now=now)
        self._log_event(
            "action_detail_started",
            player_id=request.player_id,
            action_type=ActionType.CHI.value,
            selection_mode=selection_mode,
        )

    def _pass_action_detail(self, reason: str) -> bool:
        detail = self.pending_action_detail
        if detail is None:
            self.active_timer = None
            return False
        source_player = self.get_player(detail.source_player_id)
        self._log_event(
            "action_detail_passed",
            player_id=detail.player_id,
            action_type=detail.action_type.value,
            reason=reason,
        )
        self.pending_action_detail = None
        self.active_timer = None
        self._advance_after_unclaimed_discard(source_player.seat, reason)
        return False

    def _advance_after_unclaimed_discard(self, source_seat: int, reason: str) -> None:
        self.current_turn_seat = self._next_active_seat(source_seat)
        self.phase = GamePhase.WAITING_DRAW
        self._log_event(
            "response_window_resolved",
            reason=reason,
            next_seat=self.current_turn_seat,
        )
        if not self.wall.can_draw():
            self.end_round("wall_empty")

    def _begin_turn_without_draw(self, player: PlayerState, *, now: float) -> None:
        self.current_turn_seat = player.seat
        self._open_turn_action_window(player, now=now)

    def _open_turn_action_window(
        self,
        player: PlayerState,
        *,
        now: float,
        drawn_tile_id: Optional[str] = None,
    ) -> None:
        """Open a fresh t3 window and reset acknowledgements from the prior one."""
        self.phase = GamePhase.WAITING_TURN_ACTION
        self.drawn_tile_id = drawn_tile_id
        self.turn_options_by_player = {
            player.player_id: build_turn_action_options(
                player=player,
                wall_remaining=self.wall.remaining_count(),
                rule_adapter=self.rule_adapter,
                allow_self_hu=drawn_tile_id is not None,
                winning_tile=player.tile_value(drawn_tile_id) if drawn_tile_id else None,
                is_heavenly=self._is_heavenly_candidate(player.player_id),
            )
        }
        self.turn_window_id = str(uuid4())
        self.turn_gang_prompt_passed = False
        self.turn_hu_prompt_passed = False
        self._start_timer_if_configured(TimerKind.TURN_DECISION, now=now)

    def _claim_last_discard(self, source_player_id: str, tile: TileValue) -> None:
        source_player = self.get_player(source_player_id)
        if not source_player.discarded_tiles or source_player.discarded_tiles[-1] != tile:
            raise GameStateError("最近弃牌状态与响应动作不一致")
        source_player.discarded_tiles.pop()

    @staticmethod
    def _response_option(
        window: ResponseWindow,
        player_id: str,
        action_type: ActionType,
    ) -> ActionOption:
        for option in window.options_by_player[player_id]:
            if option.action_type == action_type:
                return option
        raise GameStateError(f"玩家 {player_id} 没有动作选项 {action_type.value}")

    @staticmethod
    def _select_turn_payload(
        option: ActionOption,
        option_id: Optional[str],
    ) -> dict[str, Any]:
        """Resolve one concrete t3 choice; multiple choices require an ID."""
        if not option.payload_options:
            raise GameStateError(f"动作 {option.action_type.value} 没有可执行选项")
        if option_id is None:
            if len(option.payload_options) != 1:
                raise GameStateError("存在多个杠牌选项，必须指定 option_id")
            return dict(option.payload_options[0])

        payload = next(
            (
                candidate
                for candidate in option.payload_options
                if candidate.get("option_id") == option_id
            ),
            None,
        )
        if payload is None:
            raise GameStateError(f"未知或过期的杠牌选项: {option_id}")
        return dict(payload)

    def _has_unpassed_gang_prompt(self, player_id: str) -> bool:
        if self.turn_gang_prompt_passed:
            return False
        return any(
            option.action_type in (ActionType.AN_GANG, ActionType.JIA_GANG)
            for option in self.turn_options_by_player.get(player_id, [])
        )

    def _require_gang_prompt_resolved(self, player_id: str) -> None:
        if self._has_unpassed_gang_prompt(player_id):
            raise GameStateError("当前存在可执行的杠牌，请先选择杠或 PASS")

    def _expire_turn_decision(self, now: float) -> None:
        self.active_timer = None
        if self.phase != GamePhase.WAITING_TURN_ACTION:
            return

        player = self.current_player()
        if not player.hand_tiles:
            raise GameStateError("当前玩家没有可打出的牌")

        self._log_event(
            "turn_decision_default_discard",
            player_id=player.player_id,
            tile_id=player.tile_id_at(len(player.hand_tiles) - 1),
            now=now,
        )
        self.turn_gang_prompt_passed = True
        self.discard_tile_by_id(
            player.player_id,
            player.tile_id_at(len(player.hand_tiles) - 1),
            now=now,
        )

    def _start_timer_if_configured(
        self,
        kind: TimerKind,
        *,
        now: Optional[float],
    ) -> None:
        timeout = None
        if kind == TimerKind.TURN_DECISION:
            timeout = self.timeout_config.turn_action_seconds
        elif kind == TimerKind.RESPONSE_WINDOW:
            timeout = self.timeout_config.discard_response_seconds
        elif kind == TimerKind.DETAIL_SELECTION:
            timeout = self.timeout_config.action_detail_seconds
        elif kind == TimerKind.ROB_KONG_RESPONSE:
            timeout = self.timeout_config.rob_kong_seconds

        if timeout is None:
            self.active_timer = None
            return

        self.active_timer = TimerState.start(kind, now or 0.0, timeout)

    def _pending_response_snapshot(self) -> dict[str, bool]:
        if self.response_window is None:
            return {}
        return {
            player_id: player_id in self.response_window.requests_by_player
            or player_id in self.response_window.auto_passed_players
            for player_id in self.response_window.players()
        }

    def _require_turn_window(self, source_turn_window_id: Optional[str]) -> None:
        """Reject stale UI commands before touching hand identities or groups."""
        if (
            source_turn_window_id is not None
            and source_turn_window_id != self.turn_window_id
        ):
            raise GameStateError("t3 窗口已经过期")

    def _require_phase(self, phase: GamePhase) -> None:
        if self.phase != phase:
            raise GameStateError(f"当前阶段是 {self.phase.value}，不能执行该动作")

    def _require_current_player(self, player_id: str) -> PlayerState:
        player = self._require_active_player(player_id)
        if player.seat != self.current_turn_seat:
            raise GameStateError(f"当前不是玩家 {player_id} 的回合")
        return player

    def _require_active_player(self, player_id: str) -> PlayerState:
        player = self.get_player(player_id)
        if player.is_out:
            raise GameStateError("已胡牌的玩家本局不能再操作")
        return player

    def _next_active_seat(self, source_seat: int) -> int:
        for distance in range(1, 5):
            seat = (source_seat + distance) % 4
            if not self.players[seat].is_out:
                return seat
        raise GameStateError("没有未胡牌的玩家")

    @staticmethod
    def _next_seat(seat: int) -> int:
        return (seat + 1) % 4

    @staticmethod
    def _only_pass_options(options: list[ActionOption]) -> bool:
        return len(options) == 1 and options[0].action_type == ActionType.PASS

    def _log_event(self, event_type: str, **payload: Any) -> None:
        self.event_log.append(
            {
                "sequence": len(self.event_log),
                "type": event_type,
                **payload,
            }
        )
