"""Four-seat flow, pre-game timer locking, and pass-only developer opponents."""

from dataclasses import asdict
from threading import Thread
import time

import pytest

from simulator import ActionType, GamePhase, GameState, PlayerState, TileWall, TimeoutConfig
from simulator.game_state import GameStateError
from simulator.melds import make_peng_meld
from UI.game_session import DEFAULT_TIMEOUTS, DeveloperSession, parse_timeouts
from UI.practice import BASIC_HAND, READY_HAND
from UI.server import PracticeHandler, PracticeServer


def act(session, kind, *, now=0, **kwargs):
    return session.dispatch({"type": kind, "revision": session.revision, **kwargs}, now=now)


def start(session, *, now=0, **kwargs):
    return act(session, "start", now=now, timeouts=asdict(DEFAULT_TIMEOUTS), **kwargs)


def test_lobby_has_new_defaults_and_no_running_timer():
    session = DeveloperSession()
    state = session.snapshot(now=100)
    assert state["practice"]["stage"] == "LOBBY"
    assert state["practice"]["timeout_settings"] == asdict(TimeoutConfig(5, 5, 30, 5))
    assert state["active_timer"] is None
    assert not state["practice"]["settings_locked"]
    with pytest.raises(GameStateError):
        act(session, "draw")


@pytest.mark.parametrize("value", [0, -1, True, "5", 601, float("nan"), float("inf")])
def test_invalid_settings_are_atomic(value):
    session = DeveloperSession()
    original = session.game
    settings = asdict(DEFAULT_TIMEOUTS)
    settings["turn_action_seconds"] = value
    with pytest.raises(GameStateError):
        act(session, "start", timeouts=settings)
    assert session.game is original
    assert not session.started


def test_incomplete_settings_are_rejected():
    with pytest.raises(GameStateError):
        parse_timeouts({"turn_action_seconds": 30})


def test_four_seats_are_really_dealt_and_dealer_draws():
    session = DeveloperSession()
    state = start(session)
    assert [len(p.hand_tiles) for p in session.game.players] == [14, 13, 13, 13]
    assert len(session.game.wall.tiles) == 55
    assert state["practice"]["stage"] == "OWN_TURN"
    assert state["active_timer"]["deadline_at"] == 30
    assert all("hand_tiles" not in p for p in state["players"][1:])


def test_settings_cannot_be_changed_through_other_commands_after_start():
    session = DeveloperSession()
    start(session)
    original = session.game.timeout_config
    for kind, data in [("start", {"timeouts": asdict(TimeoutConfig())}), ("configure", {}), ("scene", {"scene": "timer"}), ("edit_wall", {"tiles": [1], "timeouts": asdict(TimeoutConfig())})]:
        with pytest.raises(GameStateError):
            act(session, kind, **data)
    assert session.game.timeout_config is original
    with pytest.raises(GameStateError):
        act(session, "reset", round_id="old-round")
    state = act(session, "reset", round_id=session.game.round_id)
    assert state["practice"]["stage"] == "LOBBY"
    assert not state["practice"]["settings_locked"]
    act(session, "start", timeouts=asdict(TimeoutConfig()))
    assert session.game.active_timer is None


def test_each_timer_can_be_disabled_independently():
    session = DeveloperSession()
    settings = asdict(DEFAULT_TIMEOUTS)
    settings["discard_response_seconds"] = None
    state = act(session, "start", timeouts=settings)
    assert state["active_timer"]["kind"] == "TURN_DECISION"
    assert session.game.timeout_config.discard_response_seconds is None


def test_bot_draw_discard_and_turn_rotation_without_claims_or_other_clocks():
    session = DeveloperSession()
    start(session)
    game = session.game
    # Use a tile no opponent can claim to exercise the compulsory silent t1 wait.
    for player in game.players[1:]:
        player.hand_tiles = [24] * 13
        player._reset_hand_identity()
    game.get_player("p0").hand_tiles = [1] * 14
    game.get_player("p0")._reset_hand_identity()
    game.turn_gang_prompt_passed = True
    state = act(session, "discard", tile_id=game.get_player("p0").hand_tile_ids[-1], now=1)
    assert state["phase"] == "WAITING_RESPONSE"
    assert state["active_timer"] is None  # The discarder does not see t1.
    session.tick(now=5.99)
    assert game.phase == GamePhase.WAITING_RESPONSE
    state = session.snapshot(now=6)
    assert state["practice"]["stage"] == "BOT_TURN"
    assert game.current_turn_seat == 1 and len(game.players[1].hand_tiles) == 14
    assert state["active_timer"] is None  # Bot t3 is also private.
    session.tick(now=6.9)
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    state = session.snapshot(now=7)
    assert len(game.players[1].hand_tiles) == 13
    assert game.last_discard.player_id == "p1"
    assert all(not p.melded_groups for p in game.players[1:])


def test_no_timeout_still_advances_bots_but_human_must_pass():
    session = DeveloperSession()
    act(session, "start", timeouts=asdict(TimeoutConfig()))
    game = session.game
    act(session, "discard", tile_id=game.get_player("p0").hand_tile_ids[-1])
    assert game.current_turn_seat == 1
    assert game.phase == GamePhase.WAITING_TURN_ACTION
    session.tick(now=1)
    assert game.phase == GamePhase.WAITING_RESPONSE
    assert "p0" not in game.response_window.requests_by_player
    session.tick(now=100)
    assert game.phase == GamePhase.WAITING_RESPONSE
    act(session, "response", action="PASS", window_id=game.response_window.window_id, now=100)
    assert game.current_turn_seat == 2
    assert game.phase == GamePhase.WAITING_TURN_ACTION


def test_solo_presets_use_pre_game_settings_instead_of_old_fixed_timers():
    session = DeveloperSession()
    state = start(session, mode="solo", scene="an")
    assert state["active_timer"]["deadline_at"] == 30
    assert session.game.timeout_config == DEFAULT_TIMEOUTS
    assert all(not p.hand_tiles for p in session.game.players[1:])


def test_t2_gets_five_seconds_and_expiry_passes_then_draws():
    session = DeveloperSession()
    start(session, mode="solo", scene="basic")
    act(session, "reveal", now=1)
    state = act(session, "response", action="CHI", now=2)
    assert state["phase"] == "WAITING_RESPONSE"
    state = session.snapshot(now=6)
    assert state["active_timer"]["kind"] == "DETAIL_SELECTION"
    assert state["active_timer"]["deadline_at"] == 11
    state = session.snapshot(now=11)
    assert state["phase"] == "WAITING_DRAW"
    assert not session.game.get_player("p0").melded_groups
    assert [x["phase"] for x in state["practice"]["phase_history"]][-2:] == ["t1", "t2"]


def test_bots_with_hu_or_peng_still_only_pass():
    session = DeveloperSession()
    act(session, "start", timeouts=asdict(TimeoutConfig()))
    game = session.game
    game.players[1].hand_tiles = list(READY_HAND)
    game.players[1]._reset_hand_identity()
    game.players[0].hand_tiles = [6] * 14
    game.players[0]._reset_hand_identity()
    act(session, "discard", tile_id=game.players[0].hand_tile_ids[-1])
    assert not game.players[1].is_winner
    assert not game.players[1].melded_groups
    assert game.phase == GamePhase.WAITING_TURN_ACTION and game.current_turn_seat == 1


@pytest.mark.parametrize("hand,draw", [([3] * 5 + [24] * 8, 7), (READY_HAND, 6)])
def test_bot_passes_own_kong_or_self_hu_before_discard(hand, draw):
    session = DeveloperSession()
    start(session)
    players = [PlayerState(f"p{i}", i, f"p{i}", hand_tiles=list(hand) if i == 1 else [24] * 13) for i in range(4)]
    session.game = GameState(players, TileWall([5, draw]), current_turn_seat=1, phase=GamePhase.WAITING_DRAW, timeout_config=DEFAULT_TIMEOUTS)
    session.snapshot(now=10)
    assert any(o.action_type in {ActionType.AN_GANG, ActionType.HU} for o in session.game.turn_options_by_player["p1"])
    session.tick(now=11)
    assert not players[1].is_winner and not players[1].melded_groups
    assert players[1].discarded_tiles == [draw]
    assert any(e["type"] == "turn_gang_prompt_passed" and e["player_id"] == "p1" for e in session.game.event_log)


def test_four_player_chi_detail_timeout_automatically_starts_human_t3():
    session = DeveloperSession()
    start(session)
    players = [PlayerState(f"p{i}", i, f"p{i}", hand_tiles=list(BASIC_HAND) if i == 0 else [24] * 13) for i in range(4)]
    session.game = GameState(players, TileWall([7, 9]), current_turn_seat=3, phase=GamePhase.WAITING_DRAW, timeout_config=DEFAULT_TIMEOUTS)
    session.snapshot(now=0)
    session.snapshot(now=1)
    state = act(session, "response", action="CHI", now=2)
    assert state["phase"] == "WAITING_RESPONSE"
    state = session.snapshot(now=6)
    assert state["active_timer"]["deadline_at"] == 11
    state = session.snapshot(now=11)
    assert state["practice"]["stage"] == "OWN_TURN"
    assert state["active_timer"]["kind"] == "TURN_DECISION"
    assert state["active_timer"]["deadline_at"] == 41
    assert session.game.players[0].hand_tiles[-1] == 7


def test_actual_t4_bots_pass_and_resume_without_revealing_their_timer():
    session = DeveloperSession()
    start(session)
    players = [PlayerState(f"p{i}", i, f"p{i}", hand_tiles=list(READY_HAND) if i == 1 else []) for i in range(4)]
    players[0].hand_tiles = [6, 9]
    players[0].melded_groups = [make_peng_meld(6)]
    session.game = GameState(players, TileWall([7, 8]), phase=GamePhase.WAITING_DRAW, timeout_config=DEFAULT_TIMEOUTS)
    session.game.draw_tile("p0", now=0)
    session.snapshot(now=2)
    state = act(session, "turn_action", action="JIA_GANG", now=2)
    assert state["phase"] == "WAITING_TURN_ACTION"
    assert session.game.players[0].melded_groups[0]["group_type"] == "加杠"
    assert state["active_timer"]["deadline_at"] == 30
    assert any(entry["phase"] == "t4" for entry in session.phase_history)
    assert not any(entry["phase"] == "t4" for entry in state["practice"]["phase_history"])
    assert not state["rob_kong_window"]


def test_pass_only_four_player_game_runs_all_the_way_to_a_draw():
    session = DeveloperSession()
    act(session, "start", timeouts=asdict(TimeoutConfig()))
    for now in range(240):
        state = session.snapshot(now=now)
        if state["phase"] == "ROUND_ENDED":
            break
        if state["practice"]["stage"] == "OWN_TURN":
            game = session.game
            options = game.turn_options_by_player["p0"]
            if any(o.action_type in {ActionType.HU, ActionType.AN_GANG, ActionType.JIA_GANG} for o in options):
                act(session, "turn_action", action="PASS", now=now)
            act(session, "discard", tile_id=game.players[0].hand_tile_ids[-1], now=now)
        elif state["phase"] == "WAITING_RESPONSE":
            act(session, "response", action="PASS", window_id=session.game.response_window.window_id, now=now)
    else:
        pytest.fail("four-player round failed to reach settlement")
    assert state["round_result"]["end_reason"] == "wall_empty"
    assert not state["round_result"]["settlement_events"]
    assert {d.player_id for d in session.game.discard_history} == {"p0", "p1", "p2", "p3"}
    assert all(not p.melded_groups and p.score == 0 for p in session.game.players)


def test_next_round_keeps_real_hands_settings_scores_and_rotates_dealer():
    session = DeveloperSession()
    start(session)
    session.game.players[0].score = 12
    session.game.end_round("wall_empty")
    state = act(session, "next_round", round_id=session.game.round_id, now=10)
    assert state["dealer_seat"] == 1
    assert [len(p.hand_tiles) for p in session.game.players] == [13, 14, 13, 13]
    assert session.game.players[0].score == 12
    assert session.game.timeout_config == DEFAULT_TIMEOUTS
    assert state["practice"]["settings_locked"]


def test_service_advances_timeout_without_http_polling():
    session = DeveloperSession()
    start(session, now=time.monotonic())
    with session.lock:
        session.game.active_timer.deadline_at = time.monotonic() + 0.1
    server = PracticeServer(("127.0.0.1", 0), PracticeHandler, session)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    worker.start()
    try:
        time.sleep(0.25)
        with session.lock:
            assert session.game.discard_history
            assert session.game.discard_history[0].player_id == "p0"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
