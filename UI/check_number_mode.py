"""Optional browser regression for centered, unclipped numeric tile faces."""

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
from threading import Thread

from playwright.sync_api import expect, sync_playwright

from UI.check_ui import QuietHandler
from UI.practice import PracticeSession


def assert_number_layout(page):
    """Check actual label bounds, including two-digit and operator tiles."""
    failures = page.locator(".tile.number-view:not(.back)").evaluate_all("""nodes => nodes.flatMap(tile => {
        const label = tile.querySelector('.tile-value');
        if (!label || getComputedStyle(label).visibility === 'hidden') return [];
        const box = tile.getBoundingClientRect(), text = label.getBoundingClientRect();
        const dx = Math.abs((box.left + box.right - text.left - text.right) / 2);
        const dy = Math.abs((box.top + box.bottom - text.top - text.bottom) / 2);
        const fits = text.left >= box.left - .5 && text.right <= box.right + .5
            && text.top >= box.top - .5 && text.bottom <= box.bottom + .5;
        return dx <= 1 && dy <= 1 && fits ? [] : [{value: tile.dataset.value, dx, dy, fits}];
    })""")
    assert not failures, f"Numeric tile labels are shifted or clipped: {failures[:8]}"


def main():
    output = Path(__file__).parent / ".runtime"
    output.mkdir(exist_ok=True)
    session = PracticeSession()
    session.dispatch({
        "type": "edit_hand", "revision": session.revision,
        "tiles": [0, 9, 10, 11, 20, 24, "+", "×", 3, 6, 7, 8, 9],
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, session=session))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                for width, height, scale in [(2560, 1440, 1), (1707, 960, 1.5), (1280, 720, 2), (390, 844, 1)]:
                    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.get_by_role("button", name="翻牌", exact=True)).to_be_visible()
                    tiles = page.locator(".hand-region .tile")
                    sizes = tiles.evaluate_all("nodes => nodes.map(n => [n.offsetWidth, n.offsetHeight])")
                    page.get_by_role("button", name="数字", exact=True).click()
                    expect(page.locator(".hand-region .tile.number-view")).to_have_count(13)
                    assert_number_layout(page)
                    assert tiles.evaluate_all("nodes => nodes.map(n => [n.offsetWidth, n.offsetHeight])") == sizes
                    page.locator(".hand-surface").screenshot(path=str(output / f"numbers-{width}-{scale}.png"))
                    if width == 2560:
                        page.screenshot(path=str(output / "numbers-desktop.png"), full_page=True)
                    page.get_by_role("button", name="牌面", exact=True).click()
                    expect(page.locator(".tile.number-view")).to_have_count(0)
                    assert page.locator(".hand-region .tile img").evaluate_all("nodes => nodes.every(n => getComputedStyle(n).display !== 'none' && getComputedStyle(n).visibility === 'visible')")
                    assert tiles.evaluate_all("nodes => nodes.map(n => [n.offsetWidth, n.offsetHeight])") == sizes
                    context.close()

                # Small faces must also stay centered in melds, chi choices and results.
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                expect(page.get_by_role("button", name="翻牌", exact=True)).to_be_visible()
                page.get_by_role("button", name="数字", exact=True).click()

                def scene(value):
                    expect(page.locator("body")).not_to_have_class(re.compile("working"))
                    with page.expect_response(lambda r: r.request.method == "POST" and r.request.post_data_json.get("type") == "scene"):
                        page.get_by_label("试牌场景", exact=True).select_option(value)
                    expect(page.locator("body")).not_to_have_class(re.compile("working"))

                scene("jia")
                expect(page.locator(".meld .tile.sideways")).to_have_count(1)
                assert_number_layout(page)
                scene("an")
                page.get_by_role("button", name="暗杠 3", exact=True).click()
                expect(page.locator(".meld .tile.back")).to_have_count(2)
                assert page.locator(".meld .tile.back .tile-value").evaluate_all("nodes => nodes.every(n => getComputedStyle(n).visibility === 'hidden')")
                assert_number_layout(page)
                scene("basic")
                page.get_by_role("button", name="翻牌", exact=True).click()
                page.get_by_role("button", name="吃", exact=True).click()
                expect(page.locator(".chi-choice")).to_have_count(3)
                assert_number_layout(page)
                scene("win")
                page.get_by_role("button", name="胡", exact=True).click()
                expect(page.locator("#result-dialog")).to_be_visible()
                assert_number_layout(page)
                assert not errors, errors
                print("Number-mode regression passed: 2560x1440, 150%/200% scaling, mobile, small faces, mode switching and concealed kongs.")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
