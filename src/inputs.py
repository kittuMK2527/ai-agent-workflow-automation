"""Input validation: turn raw extracted values into a typed, complete input set.

Used by the agent before execution (and by scripts/tests). It applies defaults,
maps attachments to file inputs, coerces types, enforces limits, checks that
"grounded" identifiers really appear in the user's request, and reports
*missing* and *invalid* inputs separately so the agent can ask precisely.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .condition_evaluator import is_blank
from .errors import ToolInputError
from .models import Attachment, InputResolution, InputSpec, InputType, InvalidInput, MissingInput, WorkflowDefinition
from .tools.data_access import DataAccess, to_number

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", s.lower())


def coerce_value(spec: InputSpec, value: Any) -> tuple[Any, str | None]:
    """Return ``(clean_value, error)``. ``clean_value`` is ``None`` for blank input."""
    if is_blank(value) or value == []:
        return None, None
    t = spec.type
    if t in (InputType.STRING, InputType.FILE):
        text = str(value).strip()
        if spec.pattern and not re.fullmatch(spec.pattern, text):
            return None, f"'{text}' does not look right (expected pattern {spec.pattern})"
        return text, None
    if t in (InputType.NUMBER, InputType.INTEGER):
        num = to_number(value)
        if num is None:
            return None, f"'{value}' is not a number"
        if t == InputType.INTEGER:
            if float(num) != int(num):
                return None, f"{num} is not a whole number"
            num = int(num)
        if spec.minimum is not None and num < spec.minimum:
            return None, f"{num} is below the minimum of {spec.minimum:g}"
        if spec.maximum is not None and num > spec.maximum:
            return None, f"{num} is above the maximum of {spec.maximum:g}"
        return num, None
    if t == InputType.BOOLEAN:
        if isinstance(value, bool):
            return value, None
        low = str(value).strip().lower()
        if low in _TRUE:
            return True, None
        if low in _FALSE:
            return False, None
        return None, f"'{value}' is not yes/no"
    if t == InputType.DATE:
        try:
            return (value if isinstance(value, date) else date.fromisoformat(str(value).strip())).isoformat(), None
        except ValueError:
            return None, f"'{value}' is not a valid date (use YYYY-MM-DD)"
    if t == InputType.CHOICE:
        low = str(value).strip().lower()
        for c in spec.choices or []:
            if c.lower() == low:
                return c, None
        return None, f"'{value}' is not one of: {', '.join(spec.choices or [])}"
    if t == InputType.LIST:
        items = value if isinstance(value, list) else [p for p in re.split(r"[;,\n]", str(value))]
        cleaned: list[Any] = []
        for it in items:
            if is_blank(it):
                continue
            if spec.items == "number":
                n = to_number(it)
                if n is None:
                    return None, f"'{it}' is not a number"
                cleaned.append(n)
            else:
                cleaned.append(str(it).strip())
        return (cleaned or None), None
    return value, None  # pragma: no cover


def assign_attachments(definition: WorkflowDefinition, attachments: list[Attachment]) -> dict[str, str]:
    """Map attachments onto the workflow's file inputs (inputs without a default first)."""
    files = [i for i in definition.inputs if i.type == InputType.FILE and i.expose]
    files.sort(key=lambda i: (i.default is not None,))  # stable: user-provided files first
    usable = [a for a in attachments if Path(a.path).suffix.lower() in {".csv", ".xlsx", ".tsv"}]
    return {spec.name: att.path for spec, att in zip(files, usable)}


def resolve_inputs(
    definition: WorkflowDefinition,
    extracted: dict[str, Any] | None = None,
    *,
    sources: dict[str, str] | None = None,
    attachments: list[Attachment] | None = None,
    data: DataAccess | None = None,
    request_text: str = "",
) -> InputResolution:
    extracted = dict(extracted or {})
    src_in = dict(sources or {})
    res = InputResolution()
    attach_map = assign_attachments(definition, attachments or [])
    for a in attachments or []:
        if Path(a.path).suffix.lower() not in {".csv", ".xlsx", ".tsv"}:
            res.warnings.append(f"Ignored attachment '{a.name}': only CSV and XLSX files are supported.")
    declared = {i.name for i in definition.inputs}
    for key in extracted:
        if key not in declared:
            res.warnings.append(f"Ignored unknown input '{key}'.")

    for spec in definition.inputs:
        raw = extracted.get(spec.name)
        source = src_in.get(spec.name, "request")
        if spec.name in attach_map and is_blank(raw):
            raw, source = attach_map[spec.name], "attachment"
        elif spec.name in attach_map and source == "request":
            # an attached file always wins over a file name the text mentioned
            raw, source = attach_map[spec.name], "attachment"
        value, error = coerce_value(spec, raw)
        if error:
            res.invalid.append(InvalidInput(name=spec.name, value=raw, reason=error))
            res.values[spec.name] = None
            continue
        if value is not None and spec.grounded and source == "request" and request_text:
            if _compact(str(value)) not in _compact(request_text):
                res.warnings.append(f"Ignored '{spec.name}' = '{value}' because it does not appear in the request.")
                value = None
        if value is None and spec.default is not None:
            value, source = spec.default, "default"
        if value is None:
            res.values[spec.name] = None
            if spec.required:
                res.missing.append(MissingInput(name=spec.name, description=spec.description))
            continue
        if spec.type == InputType.FILE and data is not None:
            try:
                data.resolve(str(value))
            except ToolInputError as exc:
                res.invalid.append(InvalidInput(name=spec.name, value=value, reason=exc.user_message))
                res.values[spec.name] = None
                continue
        res.values[spec.name] = value
        res.sources[spec.name] = source

    for group in definition.input_groups:
        if all(res.values.get(n) is None for n in group.any_of) and not any(m.name in group.any_of for m in res.missing):
            res.missing.append(MissingInput(name=group.any_of[0], description=group.message, reason="one of these is required"))
    return res
