"""Deterministic disconnect fallback, deliberately independent of strategy bots."""

from simulator import ActionType, GamePhase


def pass_response(game, player_id, *, now):
    """Pass t1/t4 or abandon t2 without inventing a meld or a winning choice."""
    if game.phase == GamePhase.WAITING_RESPONSE:
        window = game.response_window
        if player_id in window.options_by_player and player_id not in window.requests_by_player:
            game.submit_response(player_id, ActionType.PASS, now=now, source_window_id=window.window_id)
    elif game.phase == GamePhase.WAITING_ROB_KONG:
        window = game.rob_kong_window
        if player_id in window.options_by_player and player_id not in window.requests_by_player:
            game.submit_rob_kong_response(player_id, ActionType.PASS, now=now, source_window_id=window.window_id)
    elif game.phase == GamePhase.WAITING_ACTION_DETAIL and game.pending_action_detail.player_id == player_id:
        game.pass_action_detail(player_id)


def discard_drawn_tile(game, player_id, *, now):
    """Discard the physical drawn tile even after regrouping; otherwise use visual rightmost."""
    player = game.get_player(player_id)
    options = game.turn_options_by_player.get(player_id, [])
    has_prompt = any(
        (o.action_type == ActionType.HU and not game.turn_hu_prompt_passed)
        or (o.action_type in {ActionType.AN_GANG, ActionType.JIA_GANG} and not game.turn_gang_prompt_passed)
        for o in options
    )
    if has_prompt:
        game.submit_turn_action(player_id, ActionType.PASS, now=now, source_turn_window_id=game.turn_window_id)
    tile_id = game.drawn_tile_id if game.drawn_tile_id in player.hand_tile_ids else player.hand_tile_ids[-1]
    game.discard_tile_by_id(player_id, tile_id, now=now, source_turn_window_id=game.turn_window_id)
