"""Atomic local checkpoint storage, outside all static/public directories."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


class StateStoreError(RuntimeError):
    """Storage failed or a checkpoint failed integrity/concurrency checks."""


class SQLiteStateStore:
    """Store complete JSON records atomically, with optimistic write versions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        try:
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self.connection.execute("PRAGMA synchronous = FULL")
            with self.connection:
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "key TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT, "
                    "digest TEXT NOT NULL, updated_at REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0)"
                )
        except sqlite3.Error as exc:
            raise StateStoreError("Cannot open checkpoint database") from exc

    def load(self, key: str) -> tuple[int, dict[str, Any] | None]:
        """Return version plus data; a closed record stays closed across restarts."""
        try:
            with self.lock:
                row = self.connection.execute(
                    "SELECT version, payload, digest, closed FROM checkpoints WHERE key = ?", (key,),
                ).fetchone()
            if row is None:
                return 0, None
            version, payload, digest, closed = row
            if closed:
                return version, None
            if not isinstance(payload, str) or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
                raise StateStoreError("Checkpoint integrity check failed")
            result = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(result, dict):
                raise StateStoreError("Invalid checkpoint payload")
            return version, result
        except (sqlite3.Error, ValueError, RecursionError) as exc:
            raise StateStoreError("Cannot read checkpoint database") from exc

    def save(self, key: str, payload: dict[str, Any], *, expected_version: int) -> int:
        """Commit one checkpoint, or reject a stale writer without changing it."""
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise StateStoreError("Checkpoint must be finite JSON data") from exc
        return self._write(key, encoded, expected_version=expected_version, closed=False)

    def terminate(self, key: str, *, expected_version: int) -> int:
        """Persist a tombstone instead of leaving an old live checkpoint behind."""
        return self._write(key, None, expected_version=expected_version, closed=True)

    def _write(self, key: str, payload: str | None, *, expected_version: int, closed: bool) -> int:
        digest = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
        try:
            with self.lock, self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute("SELECT version, closed FROM checkpoints WHERE key = ?", (key,)).fetchone()
                version = row[0] if row else 0
                if version != expected_version or (row and row[1]):
                    raise StateStoreError("Checkpoint writer is stale or the record is closed")
                self.connection.execute(
                    "INSERT INTO checkpoints (key, version, payload, digest, updated_at, closed) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET version=excluded.version, payload=excluded.payload, "
                    "digest=excluded.digest, updated_at=excluded.updated_at, closed=excluded.closed",
                    (key, version + 1, payload, digest, time.time(), int(closed)),
                )
                return version + 1
        except sqlite3.Error as exc:
            raise StateStoreError("Cannot commit checkpoint database") from exc

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
