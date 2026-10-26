"""Local human-vs-bot entry. No training imports, GPU, model publication or hot reload."""

import argparse
from pathlib import Path

from multiplayer.bot_worker import PolicyWorker, lower_own_priority
from multiplayer.play_model import freeze_model, freeze_export, recalled_rl, remember_rl
from multiplayer.bot_rooms import BotRooms
from multiplayer.security import WebSettings
from multiplayer.web import create_app


def main():
    parser = argparse.ArgumentParser(description="CPU 人机试玩：冻结已发布模型，不干预训练。仅本机。")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--model", type=Path, help="显式选择/更新 RL；省略时沿用上次冻结副本，不追随训练")
    parser.add_argument("--nn-model", type=Path, default=Path(".data/models/p4-v1/policy.npz"))
    parser.add_argument("--cfr-model", type=Path, default=Path(".data/models/cfr-mahjong/policy.json.gz"))
    parser.add_argument("--acfr-model", type=Path, default=Path(".data/models/cfr-abstract-v1/policy.json.gz"))
    parser.add_argument("--no-models", action="store_true", help="只启用无需模型的策略，不读取任何模型")
    parser.add_argument("--compute-ms", type=float, default=500)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 10 <= args.compute_ms <= 1000:
        parser.error("端口须为 1 到 65535，计算预算须为 10 到 1000 ms")
    if args.no_models and args.model is not None:
        parser.error("--no-models 与 --model 不能同时使用")
    lower_own_priority()
    models = {}
    try:
        if not args.no_models:
            if args.model is not None:
                rl = freeze_model(args.model)
            else:
                try:
                    rl = recalled_rl()
                except (OSError, ValueError):
                    rl = None
                    print("Saved RL copy unavailable; RL disabled, no automatic model update.", flush=True)
            if rl is not None:
                models["rl"] = rl
            for kind, path in (("nn", args.nn_model), ("cfr", args.cfr_model), ("acfr", args.acfr_model)):
                try:
                    if path.exists():
                        models[kind] = freeze_export(path)
                except (OSError, ValueError):
                    print(f"{kind}: model copy unavailable; this family will be disabled.", flush=True)
        worker = PolicyWorker(models=models)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        if worker.info["models"]["rl"]["available"]:
            remember_rl(models["rl"])
        rooms = BotRooms(worker, compute_ms=args.compute_ms)
        settings = WebSettings((f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"))
        app = create_app(settings=settings, runtime=rooms)
        print(f"CPU bot practice: http://127.0.0.1:{args.port}", flush=True)
        for kind, info in worker.info["models"].items():
            print(f"{kind}: {info}", flush=True)
        print("One low-priority CPU policy process; no Torch/CUDA; training files untouched.", flush=True)
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, reload=False, proxy_headers=False,
                    access_log=False, server_header=False, ws="websockets", ws_max_size=4096,
                    ws_max_queue=4, ws_ping_interval=5, ws_ping_timeout=10,
                    ws_per_message_deflate=False, limit_concurrency=128, timeout_graceful_shutdown=3)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
