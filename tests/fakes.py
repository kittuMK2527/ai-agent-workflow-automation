"""Test doubles for the LLM layer (no network, deterministic)."""

from __future__ import annotations

from typing import Any


class FakeLLM:
    """Implements the LLMBackend protocol. ``responses`` maps a function name to
    a dict, an Exception (raised), or a callable ``(user, parameters) -> dict``."""

    model = "fake-model"

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def call_function(self, *, system: str, user: str, function_name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "function": function_name, "parameters": parameters})
        r = self.responses.get(function_name)
        if callable(r):
            r = r(user, parameters)
        if isinstance(r, BaseException):
            raise r
        if r is None:
            raise AssertionError(f"FakeLLM has no response for {function_name}")
        return r

    def check_connection(self) -> tuple[bool, str]:
        return True, "fake connection ok"

    def calls_for(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["function"] == name]


def route_to(workflow_id: str, **extra: Any) -> dict[str, Any]:
    return {"workflow_id": workflow_id, "confidence": 0.93, "ambiguous": False, "alternatives": [],
            "explanation": f"The request is about {workflow_id}.", "clarification_question": None, **extra}
