"""Tool implementations. :func:`build_default_registry` registers all of them."""

from __future__ import annotations

from ..tool_registry import ToolRegistry


def build_default_registry() -> ToolRegistry:
    from . import classification, content, files, matching, simulated, tables, validation

    registry = ToolRegistry()
    for module in (files, tables, validation, simulated, matching, classification, content):
        module.register(registry)
    return registry
