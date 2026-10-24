"""The local solitaire controller must still execute real simulator actions."""

from functools import partial
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from simulator import ActionType, GamePhase
from simulator.game_state import GameStateError
from UI.practice import PracticeSession, RevisionConflict, SCENES
from UI.server import PracticeHandler


def act(session, kind, *, now=None, **data):
    return session.dispatch({"type": kind, "revision": session.revision, **data}, now=now)


@pytest.mark.parametrize("scene", SCENES)
def test_all_presets_have_a_valid_real_pool(scene):
    session = PracticeSession()
    session.new_scene(scene, now=0)
    game = session.game
    count = len(game.wall.tiles) + sum(len(p.hand_tiles) + sum(len(m["tiles"]) for m in p.melded_groups) for p in game.players)
    assert count == 108
    assert session.snapshot(now=0)["practice"]["wall_tiles"] == list(reversed(game.wall.tiles))


def test_listed_chi_consumes_exact_group_and_enters_own_turn():
    session = PracticeSession()
    player = session.game.get_player("p0")
    ids = player.hand_tile_ids[:3]
    act(session, "group", tile_ids=ids)
    act(session, "reveal")
    act(session, "response", action="CHI")
    detail = session.game.pending_action_detail
    act(session, "chi", option_id="chi-0", detail_id=detail.detail_id)
    assert not player.hand_groups
    assert not set(ids) & set(player.hand_tile_ids)
    assert player.melded_groups[0]["tiles"] == [3, "+", 6, 9]
    assert session.snapshot()["practice"]["stage"] == "OWN_TURN"
    act(session, "discard", tile_id=player.hand_tile_ids[-1])
    assert session.snapshot()["practice"]["stage"] == "READY_SOURCE"


@pytest.mark.parametrize("scene,action,kind", [("peng", "PENG", "碰"), ("ming", "MING_GANG", "明杠")])
def test_source_claims_apply_real_melds(scene, action, kind):
    session = PracticeSession()
    session.new_scene(scene)
    act(session, "reveal")
    act(session, "response", action=action)
    player = session.game.get_player("p0")
    assert player.melded_groups[0]["group_type"] == kind
    assert player.melded_groups[0]["display_tiles"][-1]["orientation"] == "sideways"
    assert session.snapshot()["practice"]["stage"] == "OWN_TURN"


@pytest.mark.parametrize("scene,action,kind", [("an", "AN_GANG", "暗杠"), ("jia", "JIA_GANG", "加杠")])
def test_own_kongs_draw_replacement_and_refresh_window(scene, action, kind):
    session = PracticeSession()
    session.new_scene(scene)
    old_window, old_count = session.game.turn_window_id, len(session.game.wall.tiles)
    act(session, "turn_action", action=action, turn_window_id=old_window)
    assert session.game.turn_window_id != old_window
    assert len(session.game.wall.tiles) == old_count - 1
    assert session.game.get_player("p0").melded_groups[0]["group_type"] == kind


def test_self_win_exposes_fan_breakdown_and_manual_next_round():
    session = PracticeSession()
    session.new_scene("win")
    state = act(session, "turn_action", action="HU")
    event = state["round_result"]["settlement_events"][0]
    assert event["win_result"]["groups"] and event["win_result"]["fan_items"]
    score = session.game.get_player("p0").score
    state = act(session, "next_round", round_id=session.game.round_id)
    assert state["round_result"] is None
    assert session.game.get_player("p0").score == score
    assert state["dealer_seat"] == 1
    assert all(not p.hand_tiles for p in session.game.players[1:])


def test_wall_order_is_next_first_and_empty_wall_draw_settles():
    session = PracticeSession()
    act(session, "edit_wall", tiles=[24, 7])
    state = act(session, "reveal")
    assert state["last_discard"]["tile"] == 24
    act(session, "response", action="PASS")
    state = act(session, "draw")
    assert session.game.get_player("p0").hand_tiles[-1] == 7
    state = act(session, "discard", tile_id=session.game.get_player("p0").hand_tile_ids[-1])
    assert state["round_result"]["end_reason"] == "wall_empty"
    assert not state["round_result"]["settlement_events"]


def test_empty_wall_editor_refreshes_kong_options():
    session = PracticeSession()
    session.new_scene("an")
    act(session, "edit_wall", tiles=[])
    options = session.game.turn_options_by_player["p0"]
    assert all(o.action_type != ActionType.AN_GANG for o in options)


@pytest.mark.parametrize("valid", [True, False])
def test_manual_chi_requires_three_valid_physical_tiles(valid):
    session = PracticeSession()
    session.new_scene("manual")
    player = session.game.get_player("p0")
    records = player.hand_tile_records()
    ids = [next(t.tile_id for t in records if t.value == value) for value in (3, "+", 6 if valid else 5)]
    act(session, "reveal")
    act(session, "response", action="CHI")
    assert session.game.pending_action_detail.selection_mode == "manual"
    act(session, "chi", tile_ids=ids, detail_id=session.game.pending_action_detail.detail_id)
    assert bool(player.melded_groups) is valid
    if not valid:
        assert session.game.phase == GamePhase.WAITING_DRAW


def test_discard_win_preset_uses_the_same_settlement_contract():
    session = PracticeSession()
    session.new_scene("discard_win")
    act(session, "reveal")
    state = act(session, "response", action="HU")
    event = state["round_result"]["settlement_events"][0]
    assert event["source_player_id"] == "p3"
    assert len(event["score_deltas"]) == 2


def test_editor_clears_groups_and_does_not_refresh_timer():
    session = PracticeSession()
    session.new_scene("timer", now=0)
    timer = session.game.active_timer
    player = session.game.get_player("p0")
    act(session, "group", now=1, tile_ids=player.hand_tile_ids[:3])
    act(session, "edit_hand", now=2, tiles=[1, 2, "+", 3])
    assert not player.hand_groups
    assert player.hand_tiles == [1, 2, "+", 3]
    assert session.game.active_timer is timer


@pytest.mark.parametrize("tiles", [[True], [21], ["x"], ["<script>"], "1,2", [1] * 513])
def test_editor_rejects_invalid_tiles_without_mutation(tiles):
    session = PracticeSession()
    before = list(session.game.wall.tiles)
    with pytest.raises(GameStateError):
        act(session, "edit_wall", tiles=tiles)
    assert session.game.wall.tiles == before


def test_old_revision_and_old_turn_window_cannot_move_tiles():
    session = PracticeSession()
    player = session.game.get_player("p0")
    before = list(player.hand_tile_ids)
    with pytest.raises(RevisionConflict):
        session.dispatch({"type": "move", "revision": -1, "tile_id": before[0], "index": 1})
    with pytest.raises(GameStateError):
        act(session, "move", tile_id=before[0], index=1, turn_window_id="expired")
    assert player.hand_tile_ids == before


def test_timed_own_turn_expires_but_source_never_times_out():
    session = PracticeSession()
    session.new_scene("timer", now=0)
    last = session.game.get_player("p0").hand_tiles[-1]
    state = session.snapshot(now=30)
    assert state["last_discard"]["tile"] == last
    state = session.snapshot(now=35)
    assert state["practice"]["stage"] == "READY_SOURCE"
    assert state["active_timer"] is None
    assert session.snapshot(now=10000)["practice"]["stage"] == "READY_SOURCE"


def test_response_stage_freezes_hand_and_wall_edits():
    session = PracticeSession()
    act(session, "reveal")
    assert session.game.phase == GamePhase.WAITING_RESPONSE
    for kind in ("edit_hand", "edit_wall"):
        with pytest.raises(GameStateError):
            act(session, kind, tiles=[])


@pytest.fixture
def server():
    session = PracticeSession()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(PracticeHandler, session=session))
    worker = Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{httpd.server_port}", session
    httpd.shutdown()
    httpd.server_close()
    worker.join(timeout=3)


def test_http_static_assets_state_and_mutation(server):
    base, session = server
    for path, content_type in [("/", "text/html"), ("/app.js", "javascript"), ("/tiles/3.png", "image/png"), ("/assets/tile-back.svg", "image/svg+xml")]:
        with urlopen(base + path) as response:
            assert response.status == 200
            assert content_type in response.headers["Content-Type"]
            assert response.read()
    body = json.dumps({"type": "edit_wall", "revision": session.revision, "tiles": [9, 3]}).encode()
    request = Request(base + "/api/action", data=body, headers={"Content-Type": "application/json", "Origin": base})
    state = json.load(urlopen(request))["state"]
    assert state["practice"]["wall_tiles"] == [9, 3]
    assert "hand_tiles" not in state["players"][1]


def test_http_rejects_cross_origin_and_unknown_tile_paths(server):
    base, session = server
    request = Request(base + "/api/action", data=b"{}", headers={"Content-Type": "application/json", "Origin": "https://example.com"})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        urlopen(base + "/tiles/../README.md")
    assert error.value.code == 404
