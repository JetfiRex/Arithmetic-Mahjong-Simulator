"""Exercise the actual embedded table in the packaged desktop window."""

import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from singleplayer.window import GameWindow, application


def check_window(destination):
    destination = Path(destination)
    report = {"ok": False}
    app = application()
    window = GameWindow()
    window.show()
    state = {"phase": "loading", "busy": False, "deadline": time.monotonic() + 90, "finished": False}

    def finish(error=None):
        if state["finished"]:
            return
        state["finished"] = True
        timer.stop()
        if error:
            report["error"] = error
        else:
            report["ok"] = True
        report["script_errors"] = list(window.page.script_errors)
        window.close()

    def evaluate(script, callback):
        state["busy"] = True

        def completed(value):
            state["busy"] = False
            if state["finished"]:
                return
            try:
                callback(json.loads(value) if value else None)
            except Exception as exc:
                finish(str(exc))

        window.page.runJavaScript(script, completed)

    def capture(name, callback):
        # DOM callbacks can precede the native compositor's first painted frame.
        state["busy"] = True

        def save():
            if state["finished"]:
                return
            try:
                pixmap = window.grab()
                image = pixmap.toImage()
                colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 30)
                          for y in range(0, image.height(), 30)}
                assert len(colors) >= 10, "Embedded window has not painted the table"
                path = str(destination.with_suffix(f".{name}.png"))
                assert pixmap.save(path)
                report[name + "_screenshot"] = path
                report[name + "_paint_colors"] = len(colors)
                state["busy"] = False
                callback()
            except Exception as exc:
                finish(str(exc))

        QTimer.singleShot(2500, save)

    def home_loaded(value):
        if not value or not value.get("ready"):
            return
        assert value["home_visible"] and value["mode_count"] == 5
        assert not value["settings_open"]
        report["mode_home"] = True
        evaluate('''JSON.stringify((() => {
          document.querySelector('[data-mode="normal"]').click();
          return true;
        })())''', lambda value: state.update(phase="settings"))

    def loaded(value):
        if not value or not value.get("ready"):
            return
        assert value["settings_open"], "Single-player setup did not open"
        assert value["profiles"] == 21 and value["opponents"] == 3
        assert value["preparation_seconds"] == "30"
        report["default_preparation_seconds"] = 30
        assert not window.page.script_errors, window.page.script_errors
        visible = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
        assert visible == [window], "Expected a single game window"
        report["one_window"] = True
        report["embedded_setup"] = True
        assert value["backdrop_filter"] == "none", "Settings must not blur the table while scrolling"
        report["settings_backdrop_filter"] = value["backdrop_filter"]
        # Real UI handlers: select the three profiles, disable timers, submit setup.
        capture("setup", lambda: evaluate('''JSON.stringify((() => {
          const selects = [...document.querySelectorAll('#bot-profile-list select')];
          selects[0].value = 'tsumogiri'; selects[0].dispatchEvent(new Event('change'));
          return true;
        })())''', lambda value: state.update(phase="second_bot")))

    def second_bot(value):
        if not value or not value["ready"]:
            return
        evaluate('''JSON.stringify((() => {
          const select = document.querySelectorAll('#bot-profile-list select')[1];
          select.value = 'tsumogiri'; select.dispatchEvent(new Event('change'));
          return true;
        })())''', lambda value: state.update(phase="third_bot"))

    def third_bot(value):
        if not value or not value["ready"]:
            return
        evaluate('''JSON.stringify((() => {
          const select = document.querySelectorAll('#bot-profile-list select')[2];
          select.value = 'tsumogiri'; select.dispatchEvent(new Event('change'));
          return true;
        })())''', lambda value: state.update(phase="start"))

    def start(value):
        if not value or not value["ready"]:
            return
        evaluate('''JSON.stringify((() => {
          document.getElementById('local-timers-enabled').checked = false;
          document.getElementById('local-hand-preparation').value = '5';
          document.getElementById('local-start').click();
          return true;
        })())''', lambda value: state.update(phase="playing"))

    def playing(value):
        if not value or not value.get("playing"):
            return
        assert value["tiles"] == 13
        assert value["preparing"] and value["timer_visible"] and not value["drawn_tile_id"]
        report["initial_preparation"] = True
        assert not value["settings_open"]
        assert not value["horizontal_overflow"]
        report["embedded_game"] = True
        assert not value["group_controls"]
        state["moved_id"] = value["last_id"]
        evaluate('''JSON.stringify((() => {
          const nodes = [...document.querySelectorAll('#hand-regions [data-tile-id]')];
          window.checkedHandNodes = new Map(nodes.map(node => [node.dataset.tileId, node]));
          window.checkedOpponentNodes = [...document.querySelectorAll('.opponent-seat')];
          const source = nodes.at(-1), target = nodes[0];
          const transfer = new DataTransfer();
          source.dispatchEvent(new DragEvent('dragstart', {bubbles:true, dataTransfer:transfer}));
          target.parentElement.dispatchEvent(new DragEvent('drop', {
            bubbles:true, cancelable:true, dataTransfer:transfer,
            clientX:target.getBoundingClientRect().left
          }));
          return true;
        })())''', lambda value: state.update(phase="reordered"))

    def reordered(value):
        if not value or not value.get("ready") or value.get("first_id") != state["moved_id"]:
            return
        report["ui_hand_ordering"] = True
        assert value["hand_nodes_reused"], "Reordering replaced physical hand tile nodes"
        assert value["opponent_nodes_reused"], "Reordering rebuilt the opponent panels"
        report["hand_nodes_reused"] = report["opponent_nodes_reused"] = True
        state["phase"] = "wait_for_turn"

    def wait_for_turn(value):
        if not value or not value["ready"]:
            return
        assert value["tiles"] == 14
        report["preparation_auto_advance"] = True
        # Synthetic click() has no pointerdown; allow drag-click suppression to expire.
        state["discard_after"] = time.monotonic() + 0.35
        capture("game", lambda: evaluate('''JSON.stringify((() => {
          const pass = [...document.querySelectorAll('#actions button')].find(b => b.textContent.trim() === '过');
          if(pass) pass.click();
          return true;
        })())''', lambda value: state.update(phase="discard")))

    def discard(value):
        if not value or not value["ready"]:
            return
        if time.monotonic() < state["discard_after"]:
            return
        evaluate('''JSON.stringify((() => {
          const node = [...document.querySelectorAll('#hand-regions [data-tile-id]')]
            .find(n => n.dataset.tileId === ui().drawn_tile_id);
          if(!node) throw new Error('Drawn tile is missing');
          node.click();
          [...document.querySelectorAll('#actions button')].find(b => b.textContent.trim() === '出牌').click();
          return true;
        })())''', lambda value: state.update(phase="bot_turn"))

    def bot_turn(value):
        if not value or not value.get("bot_discarded"):
            return
        assert not window.page.script_errors, window.page.script_errors
        report["ui_discard_and_bot_turn"] = True
        report["external_navigation_blocked"] = not window.page.acceptNavigationRequest(
            window.page.origin.resolved(type(window.page.origin)("https://example.com")), None, True)
        finish()

    def poll():
        if time.monotonic() > state["deadline"]:
            finish(f"Window test timed out at {state['phase']}")
            return
        if window.start_error:
            finish(window.start_error)
            return
        if state["busy"] or window.stack.currentWidget() is not window.view:
            return
        phase = state["phase"]
        scripts = {
            "loading": ('''JSON.stringify({ready:typeof state !== 'undefined' && !!state?.bot_play,
              home_visible:!document.getElementById('mode-home').hidden,
              mode_count:document.querySelectorAll('.mode-grid .mode-card').length,
              settings_open:document.getElementById('local-settings').open})''', home_loaded),
            "settings": ('''JSON.stringify({ready:document.getElementById('local-settings').open,
              settings_open:document.getElementById('local-settings').open,
              backdrop_filter:getComputedStyle(document.getElementById('local-settings'), '::backdrop').backdropFilter,
              preparation_seconds:document.getElementById('local-hand-preparation').value,
              profiles:document.querySelectorAll('#bot-profile-list select')[0]?.options.length,
              opponents:document.querySelectorAll('#bot-profile-list select').length})''', loaded),
            "second_bot": ("JSON.stringify({ready:!busy && state.bot_play.opponents[0].profile === 'tsumogiri'})", second_bot),
            "third_bot": ("JSON.stringify({ready:!busy && state.bot_play.opponents[1].profile === 'tsumogiri'})", third_bot),
            "start": ("JSON.stringify({ready:!busy && state.bot_play.opponents[2].profile === 'tsumogiri'})", start),
            "playing": ('''JSON.stringify({playing:state.table.phase === 'PLAYING' && !busy,
              preparing:state.phase === 'HAND_PREPARATION',
              timer_visible:!document.getElementById('timer').hidden && document.getElementById('timer').textContent.includes('理牌'),
              drawn_tile_id:ui().drawn_tile_id,
              tiles:document.querySelectorAll('#hand-regions [data-tile-id]').length,
              settings_open:document.getElementById('local-settings').open,
              horizontal_overflow:document.documentElement.scrollWidth > innerWidth,
              group_controls:!!document.querySelector('#group-mode, #confirm-group'),
              last_id:state.players[0].hand_tile_records.at(-1)?.tile_id})''', playing),
            "reordered": ('''JSON.stringify({ready:!busy, first_id:state.players[0].hand_tile_records[0]?.tile_id,
              hand_nodes_reused:[...document.querySelectorAll('#hand-regions [data-tile-id]')].every(n => checkedHandNodes.get(n.dataset.tileId) === n),
              opponent_nodes_reused:checkedOpponentNodes.every((n,i) => document.querySelectorAll('.opponent-seat')[i] === n)})''', reordered),
            "wait_for_turn": ('''JSON.stringify({ready:!busy && ui().stage === 'OWN_TURN',
              tiles:state.players[0].hand_count})''', wait_for_turn),
            "discard": ("JSON.stringify({ready:!busy})", discard),
            "bot_turn": ("JSON.stringify({bot_discarded:state.players.slice(1).some(p => p.discarded_tiles.length > 0)})", bot_turn),
        }
        try:
            evaluate(*scripts[phase])
        except Exception as exc:
            finish(str(exc))

    timer = QTimer(window)
    timer.timeout.connect(poll)
    timer.start(200)
    app.exec()
    report["shutdown_verified"] = not window.session.thread.is_alive() and window.session.worker.process is None
    report["ok"] = report["ok"] and report["shutdown_verified"] and not report.get("script_errors")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
