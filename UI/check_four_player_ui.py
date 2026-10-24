"""Optional Edge smoke test for settings, four-seat flow, and private clocks."""

from pathlib import Path
from threading import Thread
import time

from playwright.sync_api import expect, sync_playwright

from simulator import ActionType, GamePhase, GameState, PlayerState, TileWall
from simulator.melds import make_peng_meld
from UI.check_ui import QuietHandler
from UI.game_session import DEFAULT_TIMEOUTS, DeveloperSession
from UI.practice import READY_HAND
from UI.server import PracticeServer


def main():
    output = Path(__file__).parent / ".runtime"
    output.mkdir(exist_ok=True)
    session = DeveloperSession()
    server = PracticeServer(("127.0.0.1", 0), QuietHandler, session)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(8000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.get_by_role("heading", name="开局设置")).to_be_visible()
            for timer, value in [("t1", "5"), ("t2", "5"), ("t3", "30"), ("t4", "5")]:
                expect(page.get_by_role("spinbutton", name=f"{timer} 秒数")).to_have_value(value)
            page.screenshot(path=str(output / "four-settings.png"), full_page=True)
            page.get_by_role("spinbutton", name="t1 秒数").fill("2")
            page.get_by_role("checkbox", name="启用 t4", exact=True).uncheck()
            page.get_by_role("button", name="开始游戏", exact=False).click()
            expect(page.locator("#setup-dialog")).not_to_be_visible()
            expect(page.locator(".opponent-seat")).to_have_count(3)
            expect(page.locator("#timer")).to_contain_text("t3")
            page.screenshot(path=str(output / "four-desktop.png"), full_page=True)
            page.get_by_role("button", name="开局设置", exact=True).click()
            expect(page.get_by_role("spinbutton", name="t1 秒数")).to_be_disabled()
            expect(page.get_by_role("checkbox", name="启用计时", exact=True)).to_be_disabled()
            expect(page.locator("#setup-status")).to_have_text("本局计时与模式已锁定")
            page.get_by_role("button", name="关闭开局设置", exact=True).click()
            # Human discard -> t1, then bot t3 without exposing its countdown.
            with session.lock:
                for player in session.game.players[1:]:
                    player.hand_tiles = [24] * 13
                    player._reset_hand_identity()
            if page.get_by_role("button", name="过", exact=True).count():
                page.get_by_role("button", name="过", exact=True).click()
            page.locator('.hand-region [data-tile-id]:not([data-value="24"])').last.click()
            page.get_by_role("button", name="出牌", exact=True).click()
            expect(page.locator("#timer")).not_to_be_visible()
            expect(page.locator(".phase-step.active")).to_contain_text("t1")
            expect(page.locator(".opponent-seat.active")).to_have_attribute("data-seat", "1", timeout=5000)
            expect(page.locator("#timer")).not_to_be_visible()
            # An explicit reset unlocks settings, and global off makes all clocks None.
            page.get_by_role("button", name="开局设置", exact=True).click()
            page.get_by_role("button", name="结束本局并重新配置", exact=True).click()
            expect(page.get_by_role("checkbox", name="启用计时", exact=True)).to_be_enabled()
            page.get_by_role("checkbox", name="启用计时", exact=True).uncheck()
            page.get_by_role("button", name="开始游戏", exact=False).click()
            expect(page.locator("#setup-dialog")).not_to_be_visible()
            expect(page.locator("#timer")).not_to_be_visible()
            with session.lock:
                assert all(v is None for v in session.snapshot()["practice"]["timeout_settings"].values())
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "four-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="开局设置", exact=True).click()
            page.screenshot(path=str(output / "four-mobile-settings.png"), full_page=True)
            page.get_by_role("button", name="关闭开局设置", exact=True).click()
            page.set_viewport_size({"width": 1280, "height": 720})
            page.screenshot(path=str(output / "four-compact.png"), full_page=True)
            assert page.locator(".table-surface").evaluate("n => n.querySelector('.discard-zone').getBoundingClientRect().bottom <= n.getBoundingClientRect().bottom")
            page.set_viewport_size({"width": 1440, "height": 1000})
            # Seed a real t4 only in this test: normal pass-only bots never declare a kong.
            with session.lock:
                players = [PlayerState(f"p{i}", i, ["我", "下家", "对家", "上家"][i], hand_tiles=list(READY_HAND) if i == 0 else []) for i in range(4)]
                players[3].hand_tiles = [6, 9]
                players[3].melded_groups = [make_peng_meld(6)]
                game = GameState(players, TileWall([7, 8]), phase=GamePhase.WAITING_DRAW, current_turn_seat=3, timeout_config=DEFAULT_TIMEOUTS)
                game.draw_tile("p3", now=time.monotonic())
                game.submit_turn_action("p3", ActionType.JIA_GANG, now=time.monotonic())
                session.game = game
                session.settings = DEFAULT_TIMEOUTS
                session.revision += 1
            expect(page.get_by_role("button", name="胡", exact=True)).to_be_visible()
            expect(page.locator(".phase-step.active")).to_contain_text("t4")
            expect(page.locator("#timer")).to_contain_text("t4")
            expect(page.locator("#incoming-tile .tile")).to_have_attribute("data-value", "6")
            page.screenshot(path=str(output / "four-t4.png"), full_page=True)
            expect(page.get_by_role("button", name="组牌", exact=True)).to_be_disabled()
            page.get_by_role("button", name="胡", exact=True).click()
            expect(page.locator("#result-dialog")).to_be_visible()
            expect(page.locator("#result-title")).to_contain_text("抢杠")
            page.get_by_role("button", name="查看牌桌", exact=True).click()
            page.get_by_role("button", name="开局设置", exact=True).click()
            page.get_by_role("button", name="结束本局并重新配置", exact=True).click()
            page.get_by_role("radio", name="单人试牌台", exact=True).check()
            page.get_by_label("开局试牌场景", exact=True).select_option("basic")
            page.get_by_role("checkbox", name="启用计时", exact=True).check()
            page.get_by_role("button", name="开始游戏", exact=False).click()
            expect(page.locator("#setup-dialog")).not_to_be_visible()
            page.get_by_role("button", name="翻牌", exact=True).click()
            expect(page.locator("#timer")).to_contain_text("t1")
            page.get_by_role("button", name="吃", exact=True).click()
            expect(page.locator(".chi-choice")).to_have_count(0)
            expect(page.locator("#timer")).to_contain_text("t2", timeout=8000)
            expect(page.locator(".chi-choice")).to_have_count(3)
            page.screenshot(path=str(output / "four-configured-t2.png"), full_page=True)
            page.get_by_role("button", name="吃 3 + 6 9", exact=True).click()
            expect(page.locator("#timer")).to_contain_text("t3")
            assert not errors, errors
            browser.close()
            print("Four-player browser smoke passed: settings, lock, bot rotation, private clocks, no-timer mode, desktop/mobile/compact, seeded t4, solo t2.")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
