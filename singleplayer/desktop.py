"""Double-click Windows launcher and lifecycle for the local game."""

from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
from threading import Thread
import time

from singleplayer.play_model import FrozenModel


RESOURCE_ROOT = Path(__file__).resolve().parents[1]


def bundled_models(root=RESOURCE_ROOT):
    """Use the three explicit exports shipped with this build, independent of cwd."""
    directory = (Path(root) / "models").resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    models = {}
    for kind in ("nn", "rl", "acfr"):
        item = manifest[kind]
        relative = item.get("path", f"{kind}/" + ("policy.json.gz" if kind == "acfr" else "policy.npz"))
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("模型路径须位于 models 目录内")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"{kind.upper()} 模型文件校验失败，请重新解压或复制游戏")
        models[kind] = FrozenModel(path, digest, item["source_name"])
    return models


class DesktopSession:
    """Own the socket, local HTTP server and policy process as one application."""

    def __init__(self, models=None):
        self.models = models
        self.worker = self.runtime = self.server = self.thread = self.socket = None
        self.failure = None
        self.url = None

    def start(self):
        from singleplayer.bot_worker import PolicyWorker
        from singleplayer.runtime import SinglePlayer
        from singleplayer.web import create_app
        import uvicorn

        try:
            self.worker = PolicyWorker(models=self.models if self.models is not None else bundled_models())
            if self.models is None and not all(m["available"] for m in self.worker.info["models"].values()):
                raise ValueError("内置模型加载失败，请重新下载完整版本")
            self.runtime = SinglePlayer(self.worker)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.bind(("127.0.0.1", 0))
            port = self.socket.getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            app = create_app(self.runtime, port=port)
            config = uvicorn.Config(app, host="127.0.0.1", port=port, workers=1, loop="asyncio",
                                    http="h11", ws="none", lifespan="on", log_config=None,
                                    access_log=False, server_header=False, proxy_headers=False,
                                    timeout_graceful_shutdown=3)
            self.server = uvicorn.Server(config)

            def serve():
                try:
                    self.server.run(sockets=[self.socket])
                except BaseException as exc:
                    self.failure = exc
                    logging.exception("Local game server stopped")

            self.thread = Thread(target=serve, name="desktop-game-server", daemon=True)
            self.thread.start()
            deadline = time.monotonic() + 15
            while not self.server.started:
                if not self.thread.is_alive():
                    raise RuntimeError("本机牌桌启动失败") from self.failure
                if time.monotonic() >= deadline:
                    raise RuntimeError("本机牌桌启动超时")
                time.sleep(0.05)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            # HTTP shutdown may first wait for its grace period, followed by a
            # calculator worker finishing its bounded ten-second analysis.
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError("游戏仍在退出，请稍候")
        if self.socket is not None:
            self.socket.close()
        if self.worker is not None:
            self.worker.close()


def log_directory():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    directory = base / "ArithmeticMahjong"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def main():
    from singleplayer.window import run_window

    logs = log_directory()
    logging.basicConfig(filename=logs / "game.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return run_window()


def self_test(destination):
    """Build verification: run the shipped binary without Python or a browser."""
    from urllib.request import Request, urlopen
    from uuid import uuid4
    from bots.common_bot_helper.decision_driver import Control, DecisionDriver
    from bots.common_bot_helper.observation import DecisionContext
    from simulator import GameState, TimeoutConfig
    from singleplayer.bot_catalog import PROFILES

    report = {"ok": False}
    session = DesktopSession()
    try:
        session.start()

        def get(path):
            with urlopen(session.url + path, timeout=10) as response:
                return response.read()

        def act(kind, **data):
            packet = json.loads(get("/api/state"))
            body = {"type": kind, "instance_id": packet["instance_id"], "command_id": str(uuid4()),
                    "round_id": packet["round_id"], "hand_revision": packet["hand_revision"],
                    "turn_window_id": packet["turn_window_id"], "settings_revision": packet["table"]["revision"], **data}
            request = Request(session.url + "/api/action", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=10) as response:
                return json.load(response)["state"]

        report["models"] = session.worker.info["models"]
        report["cpu_only"] = session.worker.info["cpu_only"]
        report["torch_loaded"] = session.worker.info["torch_loaded"]
        assert all(m["available"] for m in report["models"].values())
        for path in ("/", "/app.js", "/client.js", "/practice.js", "/calculator.js", "/study.js", "/tutorial.js", "/reference.js", "/styles.css", "/singleplayer.css",
                     "/tiles/app_icon.png", "/tiles/plus.png", "/assets/icons.svg"):
            assert get(path)

        def practice(action, **data):
            request = Request(session.url + "/api/practice",
                              data=json.dumps({"action": action, **data}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=20) as response:
                return json.load(response)["question"]

        question = practice("new", seconds=30)
        assert question["phase"] == "READY" and len(question["hand"]) == 13
        assert question["time_limit_seconds"] == 30 and not question["consider_min_fan"]
        assert "answers" not in question and "waits" not in question
        question_id = question["question_id"]
        assert practice("start", question_id=question_id)["phase"] == "ANSWERING"
        answer = practice("reveal", question_id=question_id)
        assert answer["phase"] == "REVEALED" and len(answer["answers"]) == len(answer["waits"])
        for item in answer["answers"]:
            assert item["solutions"]
            for solution in item["solutions"]:
                tiles = [tile for group in solution["groups"] for tile in group["tiles"]]
                assert len(tiles) == 14 and sum(tile["winning"] for tile in tiles) == 1
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["practice_verified"] = True
        report["practice_wait_count"] = len(answer["waits"])

        def calculate(hand, **data):
            request = Request(session.url + "/api/calculator",
                              data=json.dumps({"hand": hand, **data}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=20) as response:
                return json.load(response)["analysis"]

        ready = [1, "+", 2, 3, 2, "+", 2, 4, 3, "+", 3, 6, 5]
        analysis = calculate(ready)
        assert analysis["complete"] and analysis["shanten"] == 0
        assert len(analysis["improvements"]) == 7 and analysis["unseen_total"] == 95
        assert abs(analysis["value"] - sum(row["contribution"] for row in analysis["improvements"])) < 1e-9
        answer = calculate(ready, action="explain", tile=5)
        assert answer["solutions"]
        won = calculate(ready + [5], winning_tile=5)
        assert won["complete"] and won["win"]["qualifies"]
        assert won["value"] == 2 ** max(won["win"]["fan"], 1)
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["calculator_verified"] = True
        reference = json.loads(get("/api/reference"))
        assert reference["total"] == 108 and len(reference["tiles"]) == 24 and reference["fans"]
        report["reference_verified"] = True
        tutorial_state = json.loads(get("/api/tutorial"))["tutorial"]
        assert len(tutorial_state["hand"]) == 14 and tutorial_state["step"] == 0

        def tutorial(action, **data):
            nonlocal tutorial_state
            request = Request(session.url + "/api/tutorial", data=json.dumps({"action": action, **data,
                "tutorial_id": tutorial_state["tutorial_id"], "revision": tutorial_state["revision"]}).encode(),
                headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=10) as response:
                tutorial_state = json.load(response)["tutorial"]

        for action in [*("next" for _ in range(13)), "hu"]:
            tutorial(action)
        assert tutorial_state["won"] and tutorial_state["result"]["win_result"]["can_win"]
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_verified"] = True
        tutorial("select_lesson", lesson=1)
        for _ in range(3): tutorial("next")
        assert len(tutorial_state["hand"]) == 13 and not tutorial_state["hu_enabled"]
        for _ in range(4):
            record = next(t for t in tutorial_state["hand"][4:] if t["value"] == 14)
            tutorial("move", tile_id=record["tile_id"], index=0)
        tutorial("next"); tutorial("next")
        for value, position in [("+",5), (3,6), (5,7)]:
            record = next(t for t in tutorial_state["hand"][4:] if t["value"] == value)
            tutorial("move", tile_id=record["tile_id"], index=position)
        for _ in range(3): tutorial("next")
        tutorial("draw")
        assert tutorial_state["hand"][-1]["value"] == 10 and tutorial_state["hu_enabled"]
        for _ in range(3): tutorial("next")
        tutorial("hu")
        assert tutorial_state["won"] and tutorial_state["result"]["win_result"]["winning_tile"] == 10
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_sort_verified"] = True
        tutorial("select_lesson", lesson=2)
        assert len(tutorial_state["hand"]) == 13 and tutorial_state["interaction"]["history_count"] == 9
        while tutorial_state["step"] < len(tutorial_state["steps"])-1:
            requirement = tutorial_state["interaction"]["requirement"]
            if requirement == "discard":
                record = next(t for t in tutorial_state["hand"] if t["value"] == tutorial_state["interaction"]["discard"])
                tutorial("discard", tile_id=record["tile_id"])
            elif requirement == "chi_detail":
                option = next(o for o in tutorial_state["interaction"]["chi_options"] if 8 in o["required_values"])
                tutorial("chi_detail", option_id=option["option_id"])
            else:
                tutorial("chi" if requirement == "chi_blocked" else requirement or "next")
        assert tutorial_state["won"] and tutorial_state["result"]["win_result"]["winning_tile"] == 7
        assert [m["group_type"] for m in tutorial_state["interaction"]["melds"]] == ["碰", "吃", "暗杠"]
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_interactions_verified"] = True
        tutorial("select_lesson", lesson=3)
        assert len(tutorial_state["hand"]) == 13 and tutorial_state["interaction"]["history_count"] == 8
        assert tutorial_state["traditional"]["arithmetic_preview"]["total_fan"] == 2
        while tutorial_state["step"] < len(tutorial_state["steps"])-1:
            tutorial("hu" if not tutorial_state["next_enabled"] else tutorial_state["next_action"])
        traditional_win = tutorial_state["result"]["win_result"]
        assert traditional_win["win_type"] == "传统麻将" and traditional_win["win_method"] == "点胡"
        assert traditional_win["total_fan"] == 3 and traditional_win["winning_tile"] == 2
        assert tutorial_state["result"]["source_player_id"] == "p3"
        assert sorted(map(len, traditional_win["groups"])) == [2,3,3,3,3]
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_traditional_verified"] = True
        tutorial("select_lesson", lesson=4)
        assert tutorial_state["display_only"] and len(tutorial_state["steps"]) == 6
        examples = set()
        while not tutorial_state["completed"]:
            examples.add(tutorial_state["example"]["name"])
            assert len(tutorial_state["hand"]) == 14 and not tutorial_state["hu_enabled"]
            tutorial(tutorial_state["next_action"])
        assert examples == {"七小对", "连七对", "清龙"} and not tutorial_state["won"]
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_special_verified"] = True
        tutorial("select_lesson", lesson=5)
        assert len(tutorial_state["hand"]) == 14 and len(tutorial_state["blood_battle"]["active_player_ids"]) == 4
        while not tutorial_state["completed"]:
            tutorial("hu" if not tutorial_state["next_enabled"] else tutorial_state["next_action"])
        blood = tutorial_state["blood_battle"]
        assert blood["active_player_ids"] == ["p1", "p3"]
        assert [e["winner_id"] for e in blood["settlement_events"]] == ["p0", "p2"]
        assert all(d["player_id"] != "p0" for d in blood["settlement_events"][1]["score_deltas"])
        assert blood["round_result"]["end_reason"] == "wall_empty" and tutorial_state["interaction"]["wall_remaining"] == 0
        assert sum(d["delta"] for d in blood["round_result"]["score_deltas"]) == 0
        assert json.loads(get("/api/state"))["ui"]["round_number"] == 0
        report["tutorial_blood_verified"] = True
        game = GameState.new_game(timeout_config=TimeoutConfig())
        game.draw_tile("p0", now=time.monotonic())
        driver = DecisionDriver(game, {"p0": Control.BOT})
        statuses = {}
        for profile in PROFILES:
            driver.invalidate()
            now = time.monotonic()
            deadline = now + 1
            request = driver.prepare("p0", now=now, context=DecisionContext(1000, deadline, None))
            assert session.worker.submit(game.round_id, "p0", profile, 1, request, deadline, seat=0)
            result = None
            while result is None and time.monotonic() < deadline + 0.2:
                result = session.worker.poll()
                time.sleep(0.01)
            assert result and not result.get("error"), (profile, result)
            assert result["action_id"] in {a.action_id for a in request.legal_actions}
            statuses[profile] = result["status"]
        report["profiles"] = statuses
        for index in range(1, 4):
            act("set_bot", player_id=f"p{index}", profile="tsumogiri")
        packet = act("start", mode="normal", allow_multiple_hu=True, timeouts=asdict(TimeoutConfig()),
                     hand_preparation_seconds=0)
        ids = [t["tile_id"] for t in packet["players"][0]["hand_tile_records"][:3]]
        packet = act("move", tile_id=ids[2], index=0)
        assert packet["players"][0]["hand_tile_records"][0]["tile_id"] == ids[2]
        options = packet["turn_options_by_player"].get("p0", [])
        if any(o["action_type"] in {"HU", "AN_GANG", "JIA_GANG"} for o in options):
            packet = act("turn_action", action="PASS")
        act("discard", tile_id=packet["ui"]["drawn_tile_id"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            packet = json.loads(get("/api/state"))
            if any(p["discarded_tiles"] for p in packet["players"][1:]):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Bot turn did not advance")
        report["play_verified"] = True
        report["ok"] = True
    except Exception:
        import traceback
        report["error"] = traceback.format_exc()
    finally:
        try:
            session.close()
            report["shutdown_verified"] = True
        except Exception:
            report["ok"] = False
        Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
