"""Real Edge windows and ephemeral ASGI process; no public tunnel or preview data."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import site
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def main():
    output = Path(__file__).parent / ".runtime"
    output.mkdir(exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = None

    def launch():
        nonlocal process
        # Own the real interpreter process, not the Windows venv launcher.
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(site.getsitepackages() + [environment.get("PYTHONPATH", "")])
        process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-m", "multiplayer.web", "--port", str(port),
                                    "--disconnect-seconds", "1", "--grace-seconds", "8"],
                                   cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=environment)
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(f"Hosting process exited: {process.returncode}")
            try:
                with urlopen(base + "/healthz", timeout=1) as response:
                    return json.load(response)["server_instance_id"]
            except (OSError, URLError):
                time.sleep(0.1)
        raise AssertionError("Hosting process did not start")

    def stop():
        nonlocal process
        if process is not None:
            process.kill()
            process.wait(timeout=10)
            process = None

    errors = []
    with sync_playwright() as playwright, ExitStack() as resources:
        resources.callback(stop)
        boot = launch()
        browser = resources.enter_context(playwright.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu"]))

        def page_for(token=None):
            page = browser.new_page(viewport={"width": 1707, "height": 960})
            page.set_default_timeout(15000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            if token:
                page.add_init_script(f"sessionStorage.setItem('mahjong-local-token', {json.dumps(token)})")
            page.goto(base)
            page.wait_for_function("() => state?.network?.enabled && localRoom.connectionId")
            return page

        pages = []
        for index in range(5):
            page = page_for()
            if index:
                page.locator("#join-tab").click()
            page.locator("#local-username").fill(["房主", "玩家二", "玩家三", "玩家四", "观众"][index])
            page.locator("#local-room-name").fill("联机验收桌")
            page.locator("#local-password").fill("browser-test-password")
            page.locator("#entry-submit").click()
            page.wait_for_function("() => state?.room != null")
            pages.append(page)
        host, second, third, fourth, spectator = pages
        host.locator("#restart-button").click()
        expect(host.locator("#local-settings")).to_be_visible()
        members = host.evaluate("state.room.members.map(m => m.member_id)")
        for index, member in enumerate(members[:4]):
            host.locator(".local-seat-select").nth(index).select_option(member)
        host.locator('.local-timer-setting[data-field="turn_action_seconds"] [type="number"]').fill("180")
        host.locator("#local-start").click()
        host.wait_for_timeout(1000)
        if host.evaluate("state.room.phase") != "PLAYING" and "房间成员已变化" in host.locator("#local-settings-error").inner_text():
            # A just-joined window may reach the host after the settings opened;
            # the returned view preserves the choices and asks for confirmation.
            host.locator("#local-start").click()
            host.wait_for_timeout(1000)
        if host.evaluate("state.room.phase") != "PLAYING":
            host.screenshot(path=str(output / "live-start-failed.png"))
            invalid = host.evaluate("() => [...document.querySelectorAll('#local-settings-form :invalid')].map(n => ({id:n.id, value:n.value, message:n.validationMessage}))")
            raise AssertionError({"settings_error": host.locator("#local-settings-error").inner_text(), "invalid": invalid, "browser_errors": errors})
        for index in range(3):
            host.locator("#hand-regions [data-tile-id]").nth(index).click(button="right")
        host.get_by_role("button", name="分为一组", exact=True).click()
        host.wait_for_function("() => me().hand_groups.length === 1")
        hand_before = host.evaluate("me().hand_tile_records.map(t => t.tile_id)")
        groups_before = host.evaluate("me().hand_groups")
        token2 = second.evaluate("localRoom.token")
        second.reload()
        second.wait_for_function("() => localRoom.connectionId && state.room?.phase === 'PLAYING'")
        assert second.evaluate("localRoom.token") == token2
        assert not host.evaluate("!!ui().connection_paused")
        token3 = third.evaluate("localRoom.token")
        third.close()
        expect(host.locator("#room-connection-dialog")).to_be_visible(timeout=7000)
        frozen = host.evaluate("ui().recovery_remaining_seconds")
        time.sleep(1.1)
        assert host.evaluate("ui().recovery_remaining_seconds") == frozen
        for page in [fourth, spectator]:
            page.wait_for_function("() => ui().connection_paused")
            assert page.evaluate("ui().recovery_remaining_seconds") is None
        for width, height, name in [(2560, 1440, "2560"), (1707, 960, "1707"), (390, 844, "390")]:
            host.set_viewport_size({"width": width, "height": height})
            host.screenshot(path=str(output / f"live-paused-{name}.png"))
            assert host.evaluate("document.documentElement.scrollWidth <= innerWidth")
        host.set_viewport_size({"width": 1707, "height": 960})
        third = page_for(token3)
        host.wait_for_function("() => !ui().connection_paused")
        assert host.evaluate("me().hand_tile_records.map(t => t.tile_id)") == hand_before
        assert host.evaluate("me().hand_groups") == groups_before
        assert abs(host.evaluate("state.active_timer.deadline_at - state.server_now") - frozen) < 3
        assert third.evaluate("state.session_id") == members[2]
        second.close()
        host.wait_for_function("() => state.room.members.some(m => m.member_id === state.room.round_members[1] && m.autopilot)", timeout=18000)
        second = page_for(token2)
        second.wait_for_function("() => ui().autopilot")
        expect(second.locator("#action-status")).to_have_text("本局由系统托管")
        assert not second.locator("#hand-regions button:not([disabled])").count()
        if host.get_by_role("button", name="过", exact=True).count():
            host.get_by_role("button", name="过", exact=True).click()
        drawn = host.evaluate("state.ui.drawn_tile_id")
        host.locator(f'[data-tile-id="{drawn}"]').click()
        host.get_by_role("button", name="出牌", exact=True).click()
        for page in [third, fourth]:
            page.wait_for_function("() => state.phase === 'WAITING_RESPONSE'")
            if page.get_by_role("button", name="过", exact=True).count():
                page.get_by_role("button", name="过", exact=True).click()
        second.wait_for_function("() => state.phase === 'WAITING_TURN_ACTION' && state.current_turn_seat === 1", timeout=9000)
        bot_drawn = second.evaluate("state.ui.drawn_tile_id")
        second.wait_for_function("() => state.last_discard?.player_id === 'p1'", timeout=5000)
        assert second.evaluate("state.last_discard.tile_id") == bot_drawn
        host.close()
        for page in [second, third, fourth, spectator]:
            page.wait_for_function("() => state.room == null", timeout=7000)
        # A hard process restart is a fresh opening, not the recovery UI.
        old_identity = second.evaluate("state.session_id")
        stop()
        assert launch() != boot
        for page in [second, third, fourth, spectator]:
            page.wait_for_function(f"() => state.server_instance_id !== {json.dumps(boot)} && state.room == null && localRoom.connectionId", timeout=15000)
            expect(page.locator("#local-entry")).to_be_visible()
            assert not page.evaluate("!!ui().recovery_pending")
        assert second.evaluate("state.session_id") != old_identity
        assert not errors, errors
    print("Live hosting browser/process check passed: five identities, refresh handoff, frozen private clocks, groups/IDs, grace return, fallback drawn tile, host disconnect, fresh restart, desktop/high-DPI/mobile.")


if __name__ == "__main__":
    main()
