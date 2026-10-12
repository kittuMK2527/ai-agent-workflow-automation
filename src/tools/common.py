"""Small helpers shared by tool modules."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict


class In(BaseModel):
    """Base class for tool input schemas: unknown parameters are rejected."""

    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    """Base class for tool output schemas: unexpected fields are rejected."""

    model_config = ConfigDict(extra="forbid")


Row = dict[str, Any]


def to_snake(header: str) -> str:
    """``'Unit Price ($)'`` -> ``'unit_price'``."""
    s = re.sub(r"[^a-z0-9]+", "_", str(header).lower()).strip("_")
    if not s:
        return "column"
    return s if not s[0].isdigit() else f"c_{s}"


def public_columns(rows: list[Row]) -> list[str]:
    """Column names in first-seen order, excluding ``_``-prefixed metadata."""
    seen: dict[str, None] = {}
    for r in rows:
        for k in r:
            if not str(k).startswith("_"):
                seen.setdefault(k, None)
    return list(seen)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
