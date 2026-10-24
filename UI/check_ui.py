"""Optional browser smoke test: install playwright, then python -m UI.check_ui."""

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
from threading import Thread
import time

from playwright.sync_api import sync_playwright, expect

from UI.practice import PracticeSession
from UI.server import PracticeHandler


class QuietHandler(PracticeHandler):
    def log_message(self, format, *args):
        pass


def main():
    output = Path(__file__).parent / ".runtime"
    output.mkdir(exist_ok=True)
    session = PracticeSession()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, session=session))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.set_default_timeout(8000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            def choose_scene(scene):
                expect(page.locator("body")).not_to_have_class(re.compile("working"))
                with page.expect_response(lambda r: r.request.method == "POST" and r.request.post_data_json.get("type") == "scene"):
                    page.get_by_label("试牌场景", exact=True).select_option(scene)
                expect(page.locator("body")).not_to_have_class(re.compile("working"))
            expect(page.get_by_role("button", name="翻牌", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "desktop.png"), full_page=True)
            assert page.locator("img").evaluate_all("nodes => nodes.every(n => n.complete && n.naturalWidth > 0)")
            # Stable identity survives sorting; group creation is a real UI command.
            tiles = page.locator(".hand-region [data-tile-id]")
            original = tiles.first.get_attribute("data-tile-id")
            tiles.first.drag_to(tiles.nth(3), target_position={"x": 40, "y": 35})
            expect(tiles.first).not_to_have_attribute("data-tile-id", original)
            tiles.nth(0).click(button="right")
            tiles.nth(1).click(button="right")
            tiles.nth(2).click(button="right")
            page.get_by_role("button", name="分为一组", exact=True).click()
            expect(page.locator(".hand-region[data-group-id]")).to_have_count(1)
            page.get_by_role("button", name="翻牌", exact=True).click()
            page.get_by_role("button", name="吃", exact=True).click()
            expect(page.locator(".chi-choice")).to_have_count(3)
            page.get_by_role("button", name="吃 3 + 6 9", exact=True).click()
            expect(page.locator(".meld")).to_have_count(1)
            expect(page.locator(".meld .sideways")).to_have_count(1)
            page.locator(".hand-region [data-tile-id]").last.click()
            page.get_by_role("button", name="出牌", exact=True).click()
            expect(page.get_by_role("button", name="翻牌", exact=True)).to_be_visible()
            # Editing the wall affects the very next incoming tile.
            page.get_by_role("button", name="编辑牌堆", exact=True).click()
            page.get_by_role("textbox", name="牌值 JSON 数组").fill("[24, 7, 9]")
            page.get_by_role("button", name="应用修改", exact=False).click()
            expect(page.locator("#deck-remaining")).to_have_text("3")
            page.get_by_role("button", name="翻牌", exact=True).click()
            expect(page.locator("#incoming-tile .tile")).to_have_attribute("data-value", "24")
            page.get_by_role("button", name="过", exact=True).click()
            page.get_by_role("button", name="摸牌", exact=True).click()
            expect(page.locator(".hand-region [data-tile-id]").last).to_have_attribute("data-value", "7")
            choose_scene("manual")
            page.get_by_role("button", name="翻牌", exact=True).click()
            page.get_by_role("button", name="吃", exact=True).click()
            expect(page.locator(".chi-choice")).to_have_count(0)
            for value in (3, "+", 6):
                page.locator(f'.hand-region .tile[data-value="{value}"]').first.click()
            page.get_by_role("button", name="确认吃牌", exact=True).click()
            expect(page.locator(".meld")).to_have_count(1)
            # Each kong preset executes, then the self-HU preset settles normally.
            for scene, button in [("peng", "碰"), ("ming", "杠"), ("an", "暗杠 3"), ("jia", "加杠 6")]:
                choose_scene(scene)
                expect(page.locator("#scene-select")).to_have_value(scene)
                if scene in ("peng", "ming"):
                    page.get_by_role("button", name="翻牌", exact=True).click()
                page.get_by_role("button", name=button, exact=True).click()
                expect(page.locator(".meld")).to_have_count(1)
                expect(page.locator("#stage-label")).to_have_text("我的出牌回合")
            choose_scene("discard_win")
            page.get_by_role("button", name="翻牌", exact=True).click()
            page.get_by_role("button", name="胡", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            page.get_by_role("button", name="下一局", exact=False).click()
            choose_scene("win")
            page.get_by_role("button", name="胡", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            expect(page.locator(".fan-row")).to_have_count(3)
            page.screenshot(path=str(output / "settlement.png"), full_page=True)
            page.get_by_role("button", name="下一局", exact=False).click()
            expect(page.get_by_role("dialog")).not_to_be_visible()
            # Mobile framing and developer drawer.
            choose_scene("basic")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="打开牌堆", exact=True).click()
            expect(page.get_by_role("heading", name="牌堆工作台")).to_be_visible()
            page.screenshot(path=str(output / "mobile-deck.png"), full_page=True)
            page.get_by_role("button", name="关闭牌堆", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 900})
            choose_scene("timer")
            expect(page.locator("#timer")).to_be_visible()
            with session.lock:
                session.game.active_timer.deadline_at = time.monotonic() + 0.5
            expect(page.locator("#stage-label")).to_have_text("来牌响应", timeout=5000)
            assert not errors, errors
            browser.close()
            print("Browser smoke passed: drag, grouping, chi/peng/kongs, wall editor, draw, HU, next round, mobile, timer.")
            print(f"Screenshots: {output.resolve()}")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
