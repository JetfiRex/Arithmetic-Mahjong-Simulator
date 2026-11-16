"""Build the self-contained Windows game with three explicit inference exports."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="打包 Windows 免安装单机版")
    parser.add_argument("--nn-model", type=Path, default=ROOT / "models/p4-v1/policy.npz")
    parser.add_argument("--rl-model", type=Path, default=ROOT / "models/rl-3h/snapshots/ppo-0068.npz")
    parser.add_argument("--acfr-model", type=Path, default=ROOT / "models/cfr-abstract-newrules-1h/policy.json.gz")
    parser.add_argument("--clean", action="store_true", help="清理 PyInstaller 缓存后完整重建")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows exe 必须在 Windows 上构建")
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:
        parser.error(f"无法加载内嵌窗口组件，请安装 windows/requirements-build.txt：{exc}")
    exports = {"nn": args.nn_model, "rl": args.rl_model, "acfr": args.acfr_model}
    stage = ROOT / "build/windows-models"
    manifest = {}
    for kind, source in exports.items():
        source = source.resolve()
        if not source.is_file():
            parser.error(f"模型不存在：{source}")
        destination = stage / kind / ("policy.json.gz" if kind == "acfr" else "policy.npz")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        manifest[kind] = {"path": destination.relative_to(stage).as_posix(), "source_name": source.name,
                          "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", *(["--clean"] if args.clean else []),
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
                    str(ROOT / "windows/ArithmeticMahjong.spec")], cwd=ROOT, check=True)
    executable = ROOT / "dist/算术麻将.exe"
    report_path = ROOT / "build/windows-self-test.json"
    subprocess.run([str(executable), "--self-test", str(report_path)], cwd=executable.parent,
                   check=True, timeout=120)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("ok") or not report.get("shutdown_verified"):
        raise RuntimeError(f"exe 自检失败，请查看 {report_path}")
    window_report = ROOT / "build/windows-window-self-test.json"
    subprocess.run([str(executable), "--window-self-test", str(window_report)], cwd=executable.parent,
                   check=True, timeout=120)
    if not json.loads(window_report.read_text(encoding="utf-8")).get("ok"):
        raise RuntimeError(f"独立窗口自检失败，请查看 {window_report}")
    instructions = ROOT / "dist/使用说明.txt"
    shutil.copyfile(ROOT / "windows/使用说明.txt", instructions)
    archive = ROOT / "dist/算术麻将-Windows独立窗口版.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        bundle.write(executable, executable.name)
        bundle.write(instructions, instructions.name)
    print(f"\n完成：{executable}\n分发压缩包：{archive}")


if __name__ == "__main__":
    main()
