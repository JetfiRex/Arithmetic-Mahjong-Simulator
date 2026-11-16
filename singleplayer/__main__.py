"""Local human-vs-bot entry. No training imports, GPU, model publication or hot reload."""

import argparse
from pathlib import Path

from singleplayer.bot_worker import PolicyWorker, lower_own_priority
from singleplayer.play_model import freeze_model, freeze_export, recalled_rl, remember_rl
from singleplayer.runtime import SinglePlayer
from singleplayer.web import create_app


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="算术麻将单机版：冻结已发布模型，不干预训练。仅本机。")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--model", type=Path, help="显式选择/更新 RL；省略时沿用上次冻结副本，不追随训练")
    parser.add_argument("--nn-model", type=Path, default=ROOT / "models/p4-v1/policy.npz")
    parser.add_argument("--acfr-model", type=Path,
                        default=ROOT / "models/cfr-abstract-v1/policy.json.gz",
                        help="CFR 冻结模型")
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
            for kind, path in (("nn", args.nn_model), ("acfr", args.acfr_model)):
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
        runtime = SinglePlayer(worker, compute_ms=args.compute_ms)
        app = create_app(runtime, port=args.port)
        print(f"Single-player: http://127.0.0.1:{args.port}", flush=True)
        for kind, info in worker.info["models"].items():
            print(f"{kind}: {info}", flush=True)
        print("One low-priority CPU policy process; no Torch/CUDA; training files untouched.", flush=True)
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, reload=False, proxy_headers=False,
                    access_log=False, server_header=False, ws="none", limit_concurrency=128, timeout_graceful_shutdown=3)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
