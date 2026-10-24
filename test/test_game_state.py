import random
import unittest

from calculator_base.constants import TILE_COUNTS
from simulator.actions import TimeoutConfig, TimerKind
from simulator.game_state import GamePhase, GameState, GameStateError, TileWall


class TileWallTest(unittest.TestCase):
    def test_wall_uses_rule_tile_counts(self):
        wall = TileWall.from_rule_tiles(shuffle=False)

        self.assertEqual(wall.remaining_count(), sum(TILE_COUNTS.values()))
        self.assertEqual(wall.tiles.count(2), 6)
        self.assertEqual(wall.tiles.count("×"), 12)


class GameStateTest(unittest.TestCase):
    def test_new_game_deals_thirteen_tiles_to_each_player(self):
        game = GameState.new_game(rng=random.Random(1))

        self.assertEqual(game.phase, GamePhase.WAITING_DRAW)
        self.assertEqual(game.current_turn_seat, game.dealer_seat)
        self.assertEqual([len(player.hand_tiles) for player in game.players], [13] * 4)
        self.assertEqual(game.wall.remaining_count(), sum(TILE_COUNTS.values()) - 52)

    def test_current_player_can_draw_and_discard(self):
        game = GameState.new_game(rng=random.Random(2), dealer_seat=1)
        player = game.current_player()

        drawn_tile = game.draw_tile(player.player_id)
        self.assertEqual(game.phase, GamePhase.WAITING_TURN_ACTION)
        self.assertIn(drawn_tile, player.hand_tiles)
        self.assertEqual(len(player.hand_tiles), 14)

        record = game.discard_tile(player.player_id, drawn_tile)
        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)
        self.assertEqual(record.tile, drawn_tile)
        self.assertEqual(len(player.hand_tiles), 13)
        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)
        self.assertEqual(game.response_window.players(), {"p0", "p2", "p3"})

    def test_discard_by_index_preserves_duplicate_tile_positions(self):
        game = GameState.new_game(rng=random.Random(6), dealer_seat=0)
        player = game.current_player()
        player.hand_tiles = [3, 4, 3, 5]
        game.phase = GamePhase.WAITING_TURN_ACTION

        record = game.discard_tile_at(player.player_id, 2)

        self.assertEqual(record.tile, 3)
        self.assertEqual(player.hand_tiles, [3, 4, 5])
        self.assertEqual(player.discarded_tiles, [3])

    def test_player_can_reorder_hand_outside_own_turn(self):
        game = GameState.new_game(rng=random.Random(7), dealer_seat=0)
        player = game.get_player("p1")
        player.hand_tiles = [1, 2, 3, 4]
        game.phase = GamePhase.WAITING_TURN_ACTION

        game.reorder_hand_tile("p1", 0, 3)

        self.assertEqual(player.hand_tiles, [2, 3, 4, 1])
        self.assertEqual(game.phase, GamePhase.WAITING_TURN_ACTION)

    def test_all_passes_advance_turn_to_next_seat(self):
        game = GameState.new_game(rng=random.Random(3), dealer_seat=2)
        player = game.current_player()

        tile = game.draw_tile(player.player_id)
        game.discard_tile(player.player_id, tile)
        for responder_id in list(game.response_window.players()):
            game.pass_response(responder_id)

        self.assertEqual(game.phase, GamePhase.WAITING_DRAW)
        self.assertEqual(game.current_turn_seat, 3)
        self.assertIsNone(game.response_window)

    def test_response_timeout_waits_even_when_everyone_only_has_pass(self):
        game = GameState.new_game(
            rng=random.Random(8),
            dealer_seat=0,
            timeout_config=TimeoutConfig(discard_response_seconds=5),
        )
        player = game.current_player()

        game.draw_tile(player.player_id, now=10)
        player.hand_tiles = [1, 2, 4, 5, 7, 8, 10, 11, 12, 13, 14, 15, 16, 19]
        for other in game.players[1:]:
            other.hand_tiles = [1, 2, 4, 5, 7, 8, 10, 11, 12, 13, 14, 15, 16]
        game.discard_tile(player.player_id, 19, now=11)

        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)
        self.assertEqual(game.current_turn_seat, 0)
        self.assertIsNotNone(game.active_timer)
        self.assertEqual(game.active_timer.kind, TimerKind.RESPONSE_WINDOW)
        self.assertEqual(game.active_timer.deadline_at, 16)
        self.assertEqual(game.response_window.auto_passed_players, {"p1", "p2", "p3"})

        game.expire_timers(15.9)
        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)
        self.assertEqual(game.current_turn_seat, 0)

        game.expire_timers(16)
        self.assertEqual(game.phase, GamePhase.WAITING_DRAW)
        self.assertEqual(game.current_turn_seat, 1)

    def test_response_without_timeout_requires_manual_passes(self):
        game = GameState.new_game(rng=random.Random(9), dealer_seat=0)
        player = game.current_player()

        tile = game.draw_tile(player.player_id)
        game.discard_tile(player.player_id, tile)

        self.assertIsNone(game.active_timer)
        self.assertEqual(game.response_window.auto_passed_players, set())

        game.expire_timers(999)
        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)

        for responder_id in list(game.response_window.players()):
            game.pass_response(responder_id)

        self.assertEqual(game.phase, GamePhase.WAITING_DRAW)
        self.assertEqual(game.current_turn_seat, 1)

    def test_turn_decision_timeout_discards_rightmost_tile(self):
        game = GameState.new_game(
            rng=random.Random(10),
            dealer_seat=0,
            timeout_config=TimeoutConfig(turn_action_seconds=3),
        )
        player = game.current_player()

        game.draw_tile(player.player_id, now=20)
        player.hand_tiles = [1, 2, 3, 4]

        self.assertEqual(game.phase, GamePhase.WAITING_TURN_ACTION)
        self.assertEqual(game.active_timer.kind, TimerKind.TURN_DECISION)
        self.assertEqual(game.active_timer.deadline_at, 23)

        game.expire_timers(22.9)
        self.assertEqual(game.phase, GamePhase.WAITING_TURN_ACTION)
        self.assertEqual(player.hand_tiles, [1, 2, 3, 4])

        game.expire_timers(23)
        self.assertEqual(player.hand_tiles, [1, 2, 3])
        self.assertEqual(player.discarded_tiles[-1], 4)
        self.assertEqual(game.phase, GamePhase.WAITING_RESPONSE)

    def test_wrong_player_cannot_act(self):
        game = GameState.new_game(rng=random.Random(4), dealer_seat=0)

        with self.assertRaises(GameStateError):
            game.draw_tile("p1")

    def test_public_snapshot_hides_hands_by_default(self):
        game = GameState.new_game(rng=random.Random(5))

        hidden_snapshot = game.to_public_dict()
        self.assertNotIn("hand_tiles", hidden_snapshot["players"][0])

        revealed_snapshot = game.to_public_dict(reveal_hands=True)
        self.assertIn("hand_tiles", revealed_snapshot["players"][0])


if __name__ == "__main__":
    unittest.main()
