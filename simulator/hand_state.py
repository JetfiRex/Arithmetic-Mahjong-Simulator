"""Stable identity for physical hand tiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class HandTile:
    """One physical tile in a concealed hand."""

    tile_id: str
    value: Any

    @classmethod
    def create(cls, value: Any) -> "HandTile":
        return cls(tile_id=str(uuid4()), value=value)

    def to_dict(self) -> dict[str, Any]:
        return {"tile_id": self.tile_id, "value": self.value}
