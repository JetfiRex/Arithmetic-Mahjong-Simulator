"""One low-priority CPU policy process, shared serially by the practice bots."""

from dataclasses import replace
import multiprocessing as mp
import os
from threading import RLock
import time

from multiplayer.bot_catalog import PROFILES


def lower_own_priority():
    """Only touch this process; never inspect/change the training process priority."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
        elif hasattr(os, "nice"):
            os.nice(10)
    except OSError:
        pass


def _serve(pipe, exports):
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    lower_own_priority()
    from bots.common_bot_helper.observation import DecisionContext
    from multiplayer.bot_catalog import make_practice_bot, decision_status
    import sys

    try:
        paths, models = {}, {}
        # Load every configured family before a live clock, sharing read-only caches.
        for kind in ("rl", "nn", "cfr", "acfr"):
            descriptor = exports.get(kind)
            models[kind] = {"available": False, "error": "未配置冻结模型"}
            if descriptor is None:
                continue
            try:
                probe = make_practice_bot(f"{kind}-fast", seed=0, seat=0, model_paths={kind: descriptor["path"]})
                probe.warmup()
                policy = probe._policy
                if policy is None or descriptor.get("sha256", policy.digest) != policy.digest:
                    raise ValueError("Frozen policy contract/digest mismatch")
                paths[kind] = descriptor["path"]
                models[kind] = {"available": True, "sha256": policy.digest,
                                "source_name": descriptor["source_name"], "iteration": policy.metadata.get("rl_iteration")}
            except Exception:
                models[kind] = {"available": False, "error": "冻结模型加载失败或合同不兼容"}
        if "torch" in sys.modules:
            raise ValueError("CPU practice must not import Torch")
        rl = models["rl"]
        pipe.send({"ready": True, "sha256": rl.get("sha256"), "iteration": rl.get("iteration"),
                   "models": models, "cpu_only": True, "torch_loaded": False})
        bots, current_round = {}, None
        while True:
            message = pipe.recv()
            if message is None:
                return
            round_id, pid, seat, profile, seed, request, deadline = message
            if round_id != current_round:
                bots, current_round = {}, round_id
            started = time.monotonic()
            try:
                key = (pid, seat, profile)
                if key not in bots:
                    bot = make_practice_bot(profile, seed=seed, seat=seat, model_paths=paths)
                    if hasattr(bot, "warmup"):
                        bot.warmup()
                    bots[key] = bot
                bot = bots[key]
                # Cooperative analysis stops early; the process watchdog keeps
                # the full hard limit for a non-interruptible rules call.
                reserve = min(0.1, request.observation.decision_context.budget_ms / 1000 * 0.2)
                soft_deadline = deadline - reserve
                context = DecisionContext(max(0, (soft_deadline - time.monotonic()) * 1000), soft_deadline,
                                          request.observation.decision_context.game_remaining_ms)
                selected = bot.act(replace(request.observation, decision_context=context), request.legal_actions)
                completed_at = time.monotonic()
                canonical = next((a for a in request.legal_actions if type(a) is type(selected) and a == selected), None)
                pipe.send({"task_id": request.decision_id, "action_id": canonical.action_id if canonical else None,
                           "status": decision_status(bot, profile), "error": canonical is None,
                           "completed_at": completed_at, "elapsed_ms": (completed_at - started) * 1000})
            except Exception:
                pipe.send({"task_id": request.decision_id, "action_id": None, "status": "policy_error", "error": True})
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        try:
            pipe.send({"ready": False, "error": "模型加载失败或合同不兼容"})
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


class PolicyWorker:
    """At most one calculation in flight; a hard timeout disables it until next round."""

    def __init__(self, model_path=None, *, models=None, startup_seconds=30):
        if model_path is not None and models is not None:
            raise ValueError("Choose model_path or models, not both")
        self.exports = {kind: {"path": str(model.path), "sha256": model.sha256, "source_name": model.source_name}
                        for kind, model in (models or {}).items()}
        if model_path is not None:
            self.exports["rl"] = {"path": str(model_path), "source_name": "frozen.npz"}
        self.startup_seconds = startup_seconds
        self.lock = RLock()
        self.process = self.pipe = None
        self.ready = False
        self.info = {}
        self.busy = None
        self.restart()

    def restart(self):
        with self.lock:
            self.close()
            context = mp.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(target=_serve, args=(child, self.exports), daemon=True, name="practice-cpu-policy")
            self.process, self.pipe = process, parent
            process.start()
            child.close()
        # Startup happens before a new live round and outside the room lock.
        if not parent.poll(self.startup_seconds):
            self.close()
            raise ValueError("Bot CPU 进程启动超时；未启动牌局")
        try:
            info = parent.recv()
        except (OSError, EOFError):
            self.close()
            raise ValueError("Bot CPU 进程未能加载模型") from None
        if not isinstance(info, dict) or not info.get("ready"):
            self.close()
            raise ValueError("模型加载失败或合同不兼容，未启动牌局")
        with self.lock:
            self.info, self.ready = info, True

    def submit(self, round_id, pid, profile, seed, request, deadline, *, seat=0):
        with self.lock:
            if not self.ready or self.busy is not None or not self.process.is_alive():
                return False
            try:
                self.pipe.send((round_id, pid, seat, profile, seed, request, deadline))
                self.busy = request.decision_id
                return True
            except (OSError, EOFError):
                self.ready = False
                return False

    def poll(self):
        with self.lock:
            if self.busy is None:
                return None
            try:
                if self.pipe.poll():
                    result = self.pipe.recv()
                    self.busy = None
                    return result
                if not self.process.is_alive():
                    self.ready, self.busy = False, None
                    return {"error": True, "status": "worker_exited"}
            except (EOFError, OSError):
                self.ready, self.busy = False, None
                return {"error": True, "status": "worker_exited"}
            return None

    def retire(self):
        with self.lock:
            self.ready, self.busy = False, None
            if self.process is not None and self.process.is_alive():
                self.process.terminate()

    def close(self):
        with self.lock:
            self.retire()
            if self.process is not None:
                self.process.join(timeout=0.5)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=0.5)
                if self.process.is_alive():
                    raise RuntimeError("Cannot replace the previous practice worker")
                self.process.close()
            if self.pipe is not None:
                self.pipe.close()
            self.process = self.pipe = None
