"""Optional browser regression for four independent discard rivers."""

from dataclasses import asdict
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright

from simulator import GamePhase, GameState, PlayerState, TileWall, TimeoutConfig
from UI.check_number_mode import assert_number_layout
from UI.check_ui import QuietHandler
from UI.game_session import DeveloperSession
from UI.server import PracticeServer


def values(page, player_id):
    return page.locator(f'.discard-river[data-player-id="{player_id}"] > .tile').evaluate_all("nodes => nodes.map(n => n.dataset.value)")


def assert_river_framing(page):
    assert page.locator(".opponent-seats").evaluate("n => n.getBoundingClientRect().bottom <= document.querySelector('.table-center').getBoundingClientRect().top + 1")
    assert page.locator(".discard-zone").evaluate("n => n.getBoundingClientRect().bottom <= document.querySelector('.table-surface').getBoundingClientRect().bottom + 1")
    assert page.locator("#discard-river").evaluate("n => n.getBoundingClientRect().bottom <= n.parentElement.getBoundingClientRect().bottom + 1")
    assert page.locator(".discard-river").evaluate_all("nodes => nodes.every(n => n.scrollWidth <= n.clientWidth + 1)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def main():
    output = Path(__file__).parent / ".runtime"
    output.mkdir(exist_ok=True)
    session = DeveloperSession()
    session.dispatch({"type": "start", "revision": session.revision, "timeouts": asdict(TimeoutConfig())})
    initial = [[3, 9], [3, 6], [3, 11, 24], [3, "+", 17]]
    players = [PlayerState(f"p{i}", i, ["我", "下家", "对家", "上家"][i], hand_tiles=[3, 3, 3, 20] if i == 0 else [3, 5] if i == 1 else [], discarded_tiles=list(initial[i])) for i in range(4)]
    game = GameState(players, TileWall([7, 8]), phase=GamePhase.WAITING_TURN_ACTION, current_turn_seat=1)
    game.discard_tile("p1", 3)
    session.game = game
    session.revision += 1
    server = PracticeServer(("127.0.0.1", 0), QuietHandler, session)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 2560, "height": 1440})
                page.set_default_timeout(8000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                expect(page.get_by_role("button", name="碰", exact=True)).to_be_visible()
                expected = [list(map(str, tiles)) for tiles in initial]
                expected[1].append("3")
                for seat in range(4):
                    assert values(page, f"p{seat}") == expected[seat]
                page.screenshot(path=str(output / "discard-rivers-before-claim.png"), full_page=True)
                # Consuming p1's last 3 must not remove another seat's identical 3.
                page.get_by_role("button", name="碰", exact=True).click()
                expect(page.locator('.opponent-discards[data-player-id="p1"] > .tile')).to_have_count(2)
                for seat in range(4):
                    assert values(page, f"p{seat}") == list(map(str, initial[seat]))
                expect(page.locator("#discard-count")).to_have_text("2 张")
                page.screenshot(path=str(output / "discard-rivers-2560.png"), full_page=True)
                # Reserve a stable scroll area for long rivers without growing the seats.
                with session.lock:
                    for player in game.players:
                        player.discarded_tiles = list(range(21)) + [24, "+", "×"]
                    session.revision += 1
                expect(page.locator('#discard-river > .tile')).to_have_count(24)
                for width, height in [(2560, 1440), (1280, 720), (390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert_river_framing(page)
                    page.screenshot(path=str(output / f"discard-rivers-long-{width}.png"), full_page=True)
                page.get_by_role("button", name="数字", exact=True).click()
                assert_number_layout(page)
                assert not errors, errors
                print("Discard river regression passed: per-seat order, duplicate values, claimed-tile removal, long rivers, 2560/1280/mobile and number mode.")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
