import json
import random
from dataclasses import FrozenInstanceError

import pytest

from simulator.action_options import build_hu_response_option
from simulator.actions import ActionType
from simulator.game_state import GamePhase, GameState, PlayerState
from simulator.rule_adapter import (
    ArithmeticMahjongRuleAdapter,
    HandSnapshot,
    RuleAdapter,
    WinContext,
    WinMethod,
    WinResult,
)
from simulator.settlement import (
    PlayerScore,
    RoundEndReason,
    RoundResult,
    ScoreDelta,
    SettlementEvent,
)


READY_HAND = (1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 5, 5)


class NeverWinAdapter:
    ruleset_id = "never-win-test"

    def __init__(self):
        self.calls = []

    def evaluate_win(self, hand, context):
        self.calls.append((hand, context))
        return WinResult(
            is_winning_shape=False,
            can_start=False,
            win_type=None,
            win_method=context.win_method,
            winning_tile=context.winning_tile,
            ruleset_id=self.ruleset_id,
        )


def test_default_adapter_returns_highest_fan_as_plain_contract_data():
    adapter = ArithmeticMahjongRuleAdapter()
    result = adapter.evaluate_win(
        HandSnapshot(READY_HAND),
        WinContext(
            winner_id="p1",
            winning_tile=6,
            win_method=WinMethod.DISCARD,
            source_player_id="p0",
        ),
    )

    assert isinstance(adapter, RuleAdapter)
    assert result.can_win is True
    assert result.win_type == "算术麻将"
    assert result.win_method == WinMethod.DISCARD
    assert result.total_fan == 4
    assert result.capped_fan == 4
    assert result.base_payment == 16
    assert [item.name for item in result.fan_items] == ["全一位", "加一色"]
    assert sum(item.total_fan for item in result.fan_items) == result.total_fan
    assert result.ruleset_id == "arithmetic-mahjong-simplified-v1"
    json.dumps(result.to_dict(), ensure_ascii=False)


def test_min_fan_changes_legality_without_losing_shape_or_fan_details():
    result = ArithmeticMahjongRuleAdapter(min_fan=5).evaluate_win(
        HandSnapshot(READY_HAND),
        WinContext("p1", 6, WinMethod.DISCARD, "p0"),
    )

    assert result.is_winning_shape is True
    assert result.can_start is False
    assert result.can_win is False
    assert result.total_fan == 4
    assert result.groups
    assert result.fan_items


def test_invalid_win_context_becomes_contract_error_instead_of_leaking_exception():
    result = ArithmeticMahjongRuleAdapter().evaluate_win(
        HandSnapshot(READY_HAND),
        WinContext("p1", None, WinMethod.DISCARD, "p0"),
    )

    assert result.can_win is False
    assert result.error is not None
    assert "必须提供胡牌牌张" in result.error


def test_hand_snapshot_excludes_private_ui_groups():
    player = PlayerState(
        player_id="p0",
        seat=0,
        name="玩家",
        hand_tiles=list(READY_HAND),
    )
    player.create_hand_group(player.hand_tile_ids[:3])

    snapshot = HandSnapshot.from_player(player)

    assert snapshot.hand_tiles == tuple(player.hand_tiles)
    assert not hasattr(snapshot, "hand_groups")


def test_action_option_uses_injected_rule_adapter_and_stable_payload():
    player = PlayerState(
        player_id="p1",
        seat=1,
        name="玩家",
        hand_tiles=list(READY_HAND),
    )
    adapter = NeverWinAdapter()

    assert build_hu_response_option(
        player=player,
        discarded_tile=6,
        source_player_id="p0",
        rule_adapter=adapter,
    ) is None
    assert adapter.calls[0][1].source_player_id == "p0"


def test_game_state_uses_injected_rule_adapter_for_response_options():
    adapter = NeverWinAdapter()
    game = GameState.new_game(
        rng=random.Random(31),
        dealer_seat=0,
        rule_adapter=adapter,
    )
    game.phase = GamePhase.WAITING_TURN_ACTION
    game.current_turn_seat = 0
    game.get_player("p0").hand_tiles = [6, 20]
    game.get_player("p1").hand_tiles = list(READY_HAND)
    game.get_player("p2").hand_tiles = []
    game.get_player("p3").hand_tiles = []

    game.discard_tile("p0", 6)

    offered = game.response_window.options_by_player["p1"]
    assert all(option.action_type != ActionType.HU for option in offered)
    assert adapter.calls


def test_contracts_are_immutable_and_round_result_is_serializable():
    win_result = ArithmeticMahjongRuleAdapter().evaluate_win(
        HandSnapshot(READY_HAND),
        WinContext("p1", 6, WinMethod.DISCARD, "p0"),
    )
    event = SettlementEvent(
        settlement_id="s1",
        round_id="r1",
        winner_id="p1",
        source_player_id="p0",
        win_result=win_result,
        score_deltas=(ScoreDelta("p0", -16), ScoreDelta("p1", 16)),
    )
    result = RoundResult(
        round_id="r1",
        end_reason=RoundEndReason.HU,
        dealer_seat=0,
        next_dealer_seat=1,
        winner_ids=("p1",),
        settlement_events=(event,),
        final_scores=(PlayerScore("p0", -16), PlayerScore("p1", 16)),
    )

    with pytest.raises(FrozenInstanceError):
        win_result.total_fan = 99
    json.dumps(result.to_dict(), ensure_ascii=False)
