"""Validation tools: field/identifier checks, column detection and
normalisation, row validation and generated-text validation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from ..condition_evaluator import compile_expression, is_blank, norm_code, norm_text
from ..errors import ExpressionError, ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row, public_columns, to_snake

ORDER_ID_RE = re.compile(r"^ORD[-\s]?(\d{3,8})$", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


# -------------------------------------------------------------- field_validator
class FieldValidatorIn(In):
    values: dict[str, Any]
    required: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)


class FieldValidatorOut(Out):
    present: dict[str, Any]
    missing_required: list[str]
    missing_expected: list[str]
    is_valid: bool
    present_count: int
    missing_count: int


def field_validator(p: FieldValidatorIn, ctx: ToolContext) -> dict:
    present = {k: v for k, v in p.values.items() if not is_blank(v) and v != []}
    missing_required = [f for f in p.required if f not in present]
    missing_expected = [f for f in p.expected if f not in present and f not in p.required]
    return {
        "present": present,
        "missing_required": missing_required,
        "missing_expected": missing_expected,
        "is_valid": not missing_required,
        "present_count": len(present),
        "missing_count": len(missing_required) + len(missing_expected),
    }


# ---------------------------------------------------------- identifier_validator
class IdentifierIn(In):
    identifier: str


class IdentifierOut(Out):
    valid: bool
    kind: str | None
    normalized: str | None
    reason: str


def identifier_validator(p: IdentifierIn, ctx: ToolContext) -> dict:
    raw = (p.identifier or "").strip()
    m = ORDER_ID_RE.match(raw)
    if m:
        return {"valid": True, "kind": "order_id", "normalized": f"ORD-{m.group(1)}", "reason": "Recognised as an order ID"}
    if EMAIL_RE.match(raw):
        return {"valid": True, "kind": "email", "normalized": raw.lower(), "reason": "Recognised as an email address"}
    return {
        "valid": False, "kind": None, "normalized": None,
        "reason": "Not a valid order ID (like ORD-1001) or email address",
    }


# -------------------------------------------------------------- detect_columns
class DetectColumnsIn(In):
    headers: list[str]
    targets: dict[str, list[str]] = Field(description="canonical column -> accepted header synonyms")


class DetectColumnsOut(Out):
    mapping: dict[str, str]  # original header -> canonical name
    detected: dict[str, str]  # canonical -> original header
    missing_targets: list[str]
    unmapped_headers: list[str]
    warnings: list[str] = Field(default_factory=list)


def detect_columns(p: DetectColumnsIn, ctx: ToolContext) -> dict:
    lookup: dict[str, str] = {}
    for canonical, synonyms in p.targets.items():
        for name in [canonical, *synonyms]:
            lookup.setdefault(norm_code(name), canonical)
    mapping: dict[str, str] = {}
    detected: dict[str, str] = {}
    unmapped: list[str] = []
    warnings: list[str] = []
    for header in p.headers:
        canonical = lookup.get(norm_code(header))
        if canonical is None:
            unmapped.append(header)
        elif canonical in detected:
            warnings.append(f"Both '{detected[canonical]}' and '{header}' look like '{canonical}'; using '{detected[canonical]}'.")
            unmapped.append(header)
        else:
            mapping[header] = canonical
            detected[canonical] = header
    return {
        "mapping": mapping, "detected": detected,
        "missing_targets": [t for t in p.targets if t not in detected],
        "unmapped_headers": unmapped, "warnings": warnings,
    }


# ------------------------------------------------------------ normalize_columns
class NormalizeColumnsIn(In):
    rows: list[Row]
    mapping: dict[str, str]
    strip_text: bool = True
    keep_unmapped: bool = True


class NormalizeColumnsOut(Out):
    rows: list[Row]
    columns: list[str]
    renamed: dict[str, str]
    row_count: int


def normalize_columns(p: NormalizeColumnsIn, ctx: ToolContext) -> dict:
    renamed: dict[str, str] = dict(p.mapping)
    out: list[Row] = []
    for row in p.rows:
        new: Row = {}
        for key, value in row.items():
            if key.startswith("_"):
                new[key] = value
                continue
            if key in p.mapping:
                name = p.mapping[key]
            elif p.keep_unmapped:
                name = to_snake(key)
                renamed.setdefault(key, name)
            else:
                continue
            if p.strip_text and isinstance(value, str):
                value = re.sub(r"\s+", " ", value).strip() or None
            new[name] = value
        out.append(new)
    return {"rows": out, "columns": public_columns(out), "renamed": renamed, "row_count": len(out)}


# --------------------------------------------------------------- data_validator
class RuleIn(In):
    id: str
    description: str
    invalid_when: str = Field(description="expression on `row`; true means the row is INVALID")


class DataValidatorIn(In):
    rows: list[Row]
    required_columns: list[str] = Field(default_factory=list)
    rules: list[RuleIn] = Field(default_factory=list)


class InvalidRow(Out):
    row_number: int | None
    reasons: list[str]
    row: Row


class DataValidatorOut(Out):
    missing_columns: list[str]
    valid_rows: list[Row]
    invalid_rows: list[InvalidRow]
    total: int
    valid_count: int
    invalid_count: int
    by_rule: dict[str, int]


def data_validator(p: DataValidatorIn, ctx: ToolContext) -> dict:
    present = set(public_columns(p.rows))
    missing_columns = [c for c in p.required_columns if c not in present]
    compiled = [(r, compile_expression(r.invalid_when)) for r in p.rules]
    valid: list[Row] = []
    invalid: list[dict] = []
    by_rule = {r.id: 0 for r in p.rules}
    for i, row in enumerate(p.rows):
        reasons: list[str] = []
        for rule, expr in compiled:
            try:
                bad = expr.evaluate_bool({"row": row})
            except ExpressionError as exc:
                raise ToolInputError(f"Rule '{rule.id}' could not be evaluated: {exc.user_message}", code="rule_error") from exc
            if bad:
                reasons.append(rule.description)
                by_rule[rule.id] += 1
        if reasons:
            invalid.append({"row_number": row.get("_source_row", i + 2), "reasons": reasons, "row": row})
        else:
            valid.append(row)
    for rule, expr in compiled:
        ctx.record_condition(
            kind="row_rule", label=rule.description, expression=rule.invalid_when,
            rows_evaluated=len(p.rows), rows_matched=by_rule[rule.id],
            samples=[{"matched": True, "row_ref": r["row_number"]} for r in invalid if rule.description in r["reasons"]][:3],
        )
    return {
        "missing_columns": missing_columns, "valid_rows": valid, "invalid_rows": invalid,
        "total": len(p.rows), "valid_count": len(valid), "invalid_count": len(invalid), "by_rule": by_rule,
    }


# --------------------------------------------------------------- text_validator
class TextValidatorIn(In):
    text: str
    field: str = "text"
    min_length: int = Field(default=1, ge=0)
    max_length: int | None = Field(default=None, ge=1)
    required_terms: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict, description="the product facts the text may rely on")


class TextValidatorOut(Out):
    valid: bool
    issues: list[str]
    length: int
    unsupported_terms: list[str]
    field: str


def _facts_text(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    for v in facts.values():
        parts.append(" ".join(map(str, v)) if isinstance(v, list) else str(v))
    return norm_text(" ".join(parts))


def text_validator(p: TextValidatorIn, ctx: ToolContext) -> dict:
    issues: list[str] = []
    text = p.text or ""
    n = len(text.strip())
    if n == 0:
        issues.append("Text is empty")
    elif n < p.min_length:
        issues.append(f"Text is shorter than {p.min_length} characters ({n})")
    if p.max_length and n > p.max_length:
        issues.append(f"Text is longer than {p.max_length} characters ({n})")
    low = norm_text(text)
    for term in p.required_terms:
        if norm_text(term) and norm_text(term) not in low:
            issues.append(f"Required term '{term}' is missing")
    # Anti-invention check: colour / material words must come from the supplied facts.
    lex = ctx.data.load_reference_json("attribute_lexicon.json")
    allowed = f" {_facts_text(p.facts)} "
    words = set(low.split())
    unsupported = sorted(
        w for w in words
        if (w in lex["colors"] or w in lex["materials"]) and f" {w} " not in allowed
    )
    if unsupported:
        issues.append("Mentions details that were not provided: " + ", ".join(unsupported))
    return {"valid": not issues, "issues": issues, "length": n, "unsupported_terms": unsupported, "field": p.field}


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec("field_validator", "Check which expected fields are present or missing (blank counts as missing).",
                          FieldValidatorIn, FieldValidatorOut, field_validator, core=True))
    reg.register(ToolSpec("identifier_validator", "Recognise an order ID (ORD-1234) or an email address.",
                          IdentifierIn, IdentifierOut, identifier_validator))
    reg.register(ToolSpec("detect_columns", "Match file headers to canonical column names using synonym lists.",
                          DetectColumnsIn, DetectColumnsOut, detect_columns))
    reg.register(ToolSpec("normalize_columns", "Rename columns to canonical names and trim text values.",
                          NormalizeColumnsIn, NormalizeColumnsOut, normalize_columns))
    reg.register(ToolSpec("data_validator", "Validate rows against rules (expressions) and split valid from invalid rows with reasons.",
                          DataValidatorIn, DataValidatorOut, data_validator, core=True,
                          expression_params=("rules.*.invalid_when",)))
    reg.register(ToolSpec("text_validator", "Validate generated text: length limits, required terms and unsupported colour/material claims.",
                          TextValidatorIn, TextValidatorOut, text_validator))
