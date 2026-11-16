"""Freeze a numbered export without opening the trainer's mutable aliases."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

CACHE_ROOT = Path(__file__).parent / ".runtime" / "play-models"
RL_SELECTION = Path(__file__).parent / ".runtime" / "rl-selection.json"


@dataclass(frozen=True)
class FrozenModel:
    path: Path
    sha256: str
    source_name: str


def select_export(source):
    """Directory/latest selects the newest finalized ppo-N.npz by filename, not alias contents."""
    source = Path(source).resolve()
    if source.name == "latest.npz":
        source = source.parent
    if source.is_dir():
        exports = []
        for path in (source / "snapshots").glob("ppo-*.npz"):
            match = re.fullmatch(r"ppo-(\d+)\.npz", path.name)
            if match and path.is_file():
                exports.append((int(match[1]), path))
        if not exports:
            raise ValueError("尚无已完成的 snapshots/ppo-xxxx.npz；请等一次训练导出")
        return max(exports, key=lambda item: item[0])[1]
    if source.name == "policy.npz" or ".partial" in source.name or source.suffix.lower() != ".npz":
        raise ValueError("请选已完成的编号 .npz 快照；不读取 policy.npz、临时文件或 .pt")
    return source


def freeze_model(source, *, cache_root=None):
    """Read once; all later validation/inference uses our private copy, never resume.pt."""
    source = select_export(source)
    return freeze_export(source, cache_root=cache_root)


def freeze_export(source, *, cache_root=None):
    """Copy a completed NPZ or CFR average table, not a training checkpoint."""
    source = Path(source).resolve()
    is_npz = source.suffix.lower() == ".npz"
    if ((not is_npz and not source.name.endswith(".json.gz")) or ".partial" in source.name
            or source.name.startswith("resume") or source.name.startswith("abstraction")):
        raise ValueError("请选择完成的 .npz 或平均策略 .json.gz，不使用训练检查点")
    limit = (16 if is_npz else 128) * 1024 * 1024
    with source.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("模型为空或超过读取上限")
    cache = Path(cache_root or CACHE_ROOT).resolve()
    if cache == source.parent or cache in source.parents or source.parent in cache.parents:
        raise ValueError("试玩缓存必须独立于模型来源")
    directory = cache / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    destination = directory / ("policy.npz" if is_npz else "policy.json.gz")
    with destination.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if is_npz:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise BadZipFile("CRC mismatch")
        except BadZipFile:
            raise ValueError("模型归档不完整，请在下一次导出后重试") from None
    return FrozenModel(destination, hashlib.sha256(data).hexdigest(), source.name)


def remember_rl(model, *, selection=RL_SELECTION):
    """Explicit RL selection persists locally; restarts never auto-follow training."""
    selection = Path(selection)
    selection.parent.mkdir(parents=True, exist_ok=True)
    temporary = selection.with_name(f"{selection.name}.{uuid4()}.partial")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump({"path": str(model.path.resolve()), "sha256": model.sha256, "source_name": model.source_name}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, selection)


def recalled_rl(*, selection=RL_SELECTION, cache_root=CACHE_ROOT):
    """Only reopen our private frozen copy, never latest/policy aliases in training."""
    selection = Path(selection)
    if not selection.exists():
        return None
    value = json.loads(selection.read_text(encoding="utf-8"))
    if (type(value) is not dict or type(value.get("path")) is not str
            or type(value.get("source_name")) is not str or type(value.get("sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
        raise ValueError("RL 选择记录损坏，不自动更新模型")
    path = Path(value["path"]).resolve()
    if not path.is_relative_to(Path(cache_root).resolve()):
        raise ValueError("RL 选择记录不在私有试玩缓存中")
    with path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != value["sha256"]:
        raise ValueError("冻结 RL 副本已变化；不自动切换到新训练模型")
    return FrozenModel(path, value["sha256"], value["source_name"])
