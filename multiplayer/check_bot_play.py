"""Small real-model/browser check; no training workers, output writes or GPU."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import site
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
import psutil

from bots.common_bot_helper.decision_driver import Control, DecisionDriver
from bots.common_bot_helper.observation import DecisionContext
from calculator_base.constants import MULTIPLY
from multiplayer.bot_worker import PolicyWorker
from multiplayer.bot_catalog import PROFILES, family
from multiplayer.play_model import freeze_export, recalled_rl
from simulator import GamePhase, GameState, PlayerState, TileWall


def main():
    model = recalled_rl()
    assert model is not None, "Pin an RL snapshot explicitly before this check"
    models = {"rl": model}
    for kind, path in (("nn", ".data/models/p4-v1/policy.npz"), ("cfr", ".data/models/cfr-mahjong/policy.json.gz"),
                       ("acfr", ".data/models/cfr-abstract-v1/policy.json.gz")):
        models[kind] = freeze_export(path)
    worker = PolicyWorker(models=models)
    try:
        assert all(info["available"] for info in worker.info["models"].values()), worker.info
        game = GameState([PlayerState(f"p{i}", i, f"p{i}", hand_tiles=[1, 3, 6, 9, "+", 2, 4, 8, MULTIPLY, 12, 5, 7, 24])
                          for i in range(4)], TileWall([8, 3]), phase=GamePhase.WAITING_DRAW)
        now = time.monotonic()
        game.draw_tile("p0", now=now)
        driver = DecisionDriver(game, {"p0": Control.BOT})
        statuses = {}
        for profile in PROFILES:
            driver.invalidate()
            now = time.monotonic()
            deadline = now + 1
            request = driver.prepare("p0", now=now, context=DecisionContext(1000, deadline, None))
            assert worker.submit(game.round_id, "p0", profile, 1, request, deadline, seat=0)
            result = None
            while result is None and time.monotonic() < deadline:
                result = worker.poll()
                time.sleep(0.01)
            assert result and not result.get("error"), (profile, result)
            assert result["action_id"] in {a.action_id for a in request.legal_actions}
            if family(profile) in {"nn", "rl"}:
                assert result["status"] == "model", (profile, result)
            statuses[profile] = result["status"]
        assert worker.info["cpu_only"] and not worker.info["torch_loaded"]
        print(f"Real CPU checks, unchanged RL iteration={worker.info['iteration']}: {statuses}", flush=True)
    finally:
        worker.close()

    output = Path("multiplayer/.runtime")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(site.getsitepackages() + [environment.get("PYTHONPATH", "")])
    process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-m", "multiplayer.bot_play",
                                "--port", str(port)],
                               cwd=Path(__file__).resolve().parents[1], env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def stop():
        if process.poll() is None:
            children = psutil.Process(process.pid).children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            process.wait(timeout=10)
            psutil.wait_procs(children, timeout=5)

    errors = []
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError(f"Practice server exited {process.returncode}")
            try:
                with urlopen(base + "/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("Practice server did not start")
        with sync_playwright() as playwright, ExitStack() as resources:
            browser = resources.enter_context(playwright.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"]))
            page = browser.new_page(viewport={"width": 2560, "height": 1440})
            page.set_default_timeout(15000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.wait_for_function("() => state?.bot_play?.enabled && localRoom.connectionId")
            page.locator("#local-username").fill("真人试玩")
            page.locator("#local-room-name").fill("人机验收桌")
            page.locator("#local-password").fill("private-test")
            page.locator("#entry-submit").click()
            page.wait_for_function("() => state?.room?.members.length === 4")
            page.locator("#restart-button").click()
            expect(page.locator("#bot-settings")).to_be_visible()
            assert page.locator("#bot-profile-list select").count() == 3
            assert page.locator("#bot-profile-list select").first.locator("option").count() == 25
            assert page.locator("#bot-profile-list select").first.locator("optgroup").count() == 8
            assert page.evaluate("state.bot_play.model.sha256") == model.sha256
            for index, profile in enumerate(("math", "search", "acfr-fast")):
                page.locator("#bot-profile-list select").nth(index).select_option(profile)
                page.wait_for_function(f"() => state.room.members.some(m => m.bot_profile === {json.dumps(profile)})")
            page.locator("#local-timers-enabled").uncheck()
            for width, height in [(2560, 1440), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(output / f"bot-settings-{width}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 2560, "height": 1440})
            page.locator("#local-start").click()
            page.wait_for_function("() => state.room.phase === 'PLAYING' && state.current_turn_seat === 0")
            for index in range(3):
                page.locator("#hand-regions [data-tile-id]").nth(index).click(button="right")
            page.get_by_role("button", name="分为一组", exact=True).click()
            page.wait_for_function("() => me().hand_groups.length === 1")
            # Exercise the same native drag/drop path as human-only multiplayer.
            loose = page.locator("#hand-regions [draggable=true]")
            if loose.count() >= 2:
                before = page.evaluate("me().hand_tile_records.map(t => t.tile_id)")
                loose.nth(loose.count() - 1).drag_to(loose.nth(3), target_position={"x": 4, "y": 30})
                page.wait_for_function(f"() => JSON.stringify(me().hand_tile_records.map(t => t.tile_id)) !== {json.dumps(json.dumps(before, separators=(',', ':')))}")
            for width, height in [(2560, 1440), (1707, 960), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(output / f"bot-table-{width}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.evaluate("() => [...document.querySelectorAll('#hand-regions img')].every(i => i.complete && i.naturalWidth > 0)")
            page.set_viewport_size({"width": 1707, "height": 960})
            if page.get_by_role("button", name="过", exact=True).count():
                page.get_by_role("button", name="过", exact=True).click()
            chosen = page.evaluate("me().hand_tile_records.at(-1).tile_id")
            page.locator(f'#hand-regions [data-tile-id="{chosen}"]').click()
            page.get_by_role("button", name="出牌", exact=True).click()
            page.wait_for_function("() => state.phase === 'WAITING_RESPONSE' && state.last_discard?.player_id !== 'p0'")
            for _ in range(8):
                if page.evaluate("state.current_turn_seat === 0 && state.phase === 'WAITING_TURN_ACTION' || state.room.phase === 'REVIEWING'"):
                    break
                page.evaluate("() => { const pass = [...document.querySelectorAll('#actions button')].find(b => b.textContent === '过' && !b.disabled); pass?.click(); }")
                page.wait_for_timeout(500)
            assert page.evaluate("state.current_turn_seat === 0 && state.phase === 'WAITING_TURN_ACTION' || state.room.phase === 'REVIEWING'"), page.evaluate("({phase: state.phase, seat: state.current_turn_seat, ui: ui()})")
            assert page.evaluate("state.bot_play.worker_ready"), "Policy exceeded its hard deadline"
            assert page.evaluate("state.players.slice(1).every(p => !p.hand_tile_records)")
            if page.evaluate("state.room.phase === 'PLAYING'"):
                page.locator("#restart-button").click()
                expect(page.locator("#bot-profile-list select").first).to_be_disabled()
                page.locator("#local-settings button[data-close]").first.click()
            response = page.evaluate("async () => {const r = await localRoom.send('leave'); return r.ok;}")
            assert response
            page.wait_for_function("() => state.room == null")
            assert not errors, errors
    finally:
        stop()
    print("All-policy browser check passed: 25 options/8 groups, human/math/search/abstract-CFR mix, unchanged RL, untimed turns, grouping/drag/discard/pass, hidden hands, locked settings, desktop/mobile, clean shutdown.")


if __name__ == "__main__":
    main()
