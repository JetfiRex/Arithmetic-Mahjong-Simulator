"""Stable hand-tile identity and player-created hand groups."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class HandGroup:
    """A private UI-only group of concealed hand tiles."""

    tile_ids: list[str]
    created_order: int
    group_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self, tiles_by_id: dict[str, Any]) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "tile_ids": list(self.tile_ids),
            "created_order": self.created_order,
            "tiles": [
                {"tile_id": tile_id, "value": tiles_by_id[tile_id]}
                for tile_id in self.tile_ids
            ],
        }
