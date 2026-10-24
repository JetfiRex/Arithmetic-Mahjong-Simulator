from dataclasses import replace

import pytest

from simulator.rule_adapter import WinMethod, WinResult
from simulator.settlement import NormalScoringAdapter, ScoringAdapter, SettlementContext


def _win(method=WinMethod.DISCARD):
    return WinResult(
        is_winning_shape=True,
        can_start=True,
        win_type="test-shape",
        win_method=method,
        winning_tile=6,
        total_fan=4,
        base_payment=16,
    )


def test_normal_discard_win_charges_only_the_discarder():
    scorer = NormalScoringAdapter()
    event = scorer.settle_win(_win(), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3")))

    assert isinstance(scorer, ScoringAdapter)
    assert event.round_id == "r1"
    assert event.winner_id == "p1"
    assert event.source_player_id == "p0"
    assert {delta.player_id: delta.delta for delta in event.score_deltas} == {"p1": 16, "p0": -16}


def test_normal_self_draw_charges_each_other_player():
    event = NormalScoringAdapter().settle_win(
        _win(WinMethod.SELF_DRAW),
        SettlementContext("r1", "p1", None, ("p0", "p2", "p3")),
    )

    assert {delta.player_id: delta.delta for delta in event.score_deltas} == {
        "p0": -16, "p1": 48, "p2": -16, "p3": -16,
    }
    assert sum(delta.delta for delta in event.score_deltas) == 0


def test_scorer_uses_supplied_payment_without_recalculating_fan():
    result = replace(_win(), total_fan=999, base_payment=7)
    event = NormalScoringAdapter().settle_win(
        result, SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3")),
    )
    assert event.score_deltas[0].delta == 7


@pytest.mark.parametrize(
    "result,context",
    [
        (replace(_win(), can_start=False), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3"))),
        (replace(_win(), error="failed"), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3"))),
        (replace(_win(), base_payment=-1), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3"))),
        (_win(), SettlementContext("r1", "p1", None, ("p0", "p2", "p3"))),
        (_win(), SettlementContext("r1", "p1", "unknown", ("p0", "p2", "p3"))),
        (_win(), SettlementContext("r1", "p1", "p0", ("p0", "p0", "p3"))),
        (_win(), SettlementContext("r1", "p1", "p0", ("p0", "p1", "p3"))),
        (_win(), SettlementContext("r1", "p1", "p0", ("p0", "p3"))),
        (_win(WinMethod.SELF_DRAW), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3"))),
        (_win(WinMethod.HEAVENLY), SettlementContext("r1", "p1", "p0", ("p0", "p2", "p3"))),
    ],
)
def test_invalid_settlement_contracts_are_rejected(result, context):
    with pytest.raises(ValueError):
        NormalScoringAdapter().settle_win(result, context)
