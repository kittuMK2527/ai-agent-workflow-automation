"""Generic table tools ("calculator" family): query/derive/filter/sort, group
and aggregate, join and de-duplicate.

Row rules are *expressions* from the workflow definition (see
:mod:`src.condition_evaluator`). They are evaluated per row with the scope
``{"row": <row>, "vars": <vars>}``. A row whose expression cannot be evaluated
(e.g. a blank number) is **excluded and reported** in ``errors`` - it is never
silently counted as passing or failing.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Literal

from pydantic import Field

from ..condition_evaluator import compile_expression, explain_values, norm_code, round_half_up
from ..errors import ExpressionError, ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row, plural, public_columns

MAX_ERRORS_LISTED = 25


class RowError(Out):
    row: int
    message: str


def _sample_row(row: Row, expr, scope_extra: dict) -> dict[str, Any]:
    scope = {"row": row, **scope_extra}
    return {"values": explain_values(expr, scope), "row_ref": row.get("_source_row", None)}


# ------------------------------------------------------------------ table_query
class TableQueryIn(In):
    rows: list[Row]
    vars: dict[str, Any] = Field(default_factory=dict)
    derive: dict[str, str] = Field(default_factory=dict, description="new column -> expression (evaluated in order)")
    where: str | None = Field(default=None, description="keep rows where this expression is true")
    sort_by: list[str] = Field(default_factory=list, description="'field' ascending, '-field' descending")
    rank_field: str | None = None
    select: list[str] | None = None
    select_optional: list[str] = Field(default_factory=list, description="extra columns kept only when the data has them")
    limit: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, description="human label of the rule, shown in the trace")


class TableQueryOut(Out):
    rows: list[Row]
    count: int
    input_count: int
    columns: list[str]
    errors: list[RowError] = Field(default_factory=list)
    error_count: int = 0


def _sort_rows(rows: list[Row], keys: list[str]) -> list[Row]:
    out = list(rows)
    for key in reversed(keys):
        desc = key.startswith("-")
        field = key[1:] if desc else key
        present = [r for r in out if r.get(field) is not None]
        absent = [r for r in out if r.get(field) is None]
        try:
            present.sort(key=lambda r: r[field], reverse=desc)
        except TypeError as exc:
            raise ToolInputError(f"Cannot sort by '{field}': it mixes incompatible value types.", detail=str(exc)) from exc
        out = present + absent  # missing values always last
    return out


def table_query(p: TableQueryIn, ctx: ToolContext) -> dict:
    derive_exprs = {name: compile_expression(e) for name, e in p.derive.items()}
    where_expr = compile_expression(p.where) if p.where else None
    scope_extra = {"vars": p.vars}
    errors: list[dict] = []
    error_count = 0
    kept: list[Row] = []
    matched_samples: list[dict] = []
    rejected_samples: list[dict] = []
    derived_rows: list[Row] = []
    for i, original in enumerate(p.rows):
        row = dict(original)
        try:
            for name, expr in derive_exprs.items():
                row[name] = expr.evaluate({"row": row, **scope_extra})
            derived_rows.append(row)
            keep = True
            if where_expr is not None:
                keep = where_expr.evaluate_bool({"row": row, **scope_extra})
        except ExpressionError as exc:
            error_count += 1
            if len(errors) < MAX_ERRORS_LISTED:
                ref = original.get("_source_row", i + 1)
                errors.append({"row": ref, "message": exc.user_message})
            continue
        if keep:
            kept.append(row)
            if where_expr is not None and len(matched_samples) < 3:
                matched_samples.append(_sample_row(row, where_expr, scope_extra))
        elif len(rejected_samples) < 3:
            rejected_samples.append(_sample_row(row, where_expr, scope_extra))
    # A derived true/false column IS a decision rule (e.g. "price difference exceeds threshold"):
    # report it to the trace exactly like a filter, with the values that decided it.
    bool_cols = [n for n in derive_exprs if derived_rows and all(isinstance(r[n], bool) for r in derived_rows)]
    for name in bool_cols:
        expr = derive_exprs[name]
        yes = [r for r in derived_rows if r[name]][:3]
        no = [r for r in derived_rows if not r[name]][:3]
        ctx.record_condition(
            kind="row_rule",
            label=(p.label or name) if len(bool_cols) == 1 else f"{p.label or 'Rule'}: {name}",
            expression=f"{name} = {p.derive[name]}",
            rows_evaluated=len(derived_rows),
            rows_matched=sum(1 for r in derived_rows if r[name]),
            samples=[{"matched": True, **_sample_row(r, expr, scope_extra)} for r in yes]
            + [{"matched": False, **_sample_row(r, expr, scope_extra)} for r in no],
        )
    if where_expr is not None:
        ctx.record_condition(
            kind="row_rule",
            label=p.label or "Row filter",
            expression=p.where,
            rows_evaluated=len(p.rows) - error_count,
            rows_matched=len(kept),
            samples=[{"matched": True, **s} for s in matched_samples] + [{"matched": False, **s} for s in rejected_samples],
            error=f"{plural(error_count, 'row')} could not be evaluated and were excluded" if error_count else None,
        )
    elif derive_exprs and error_count and not bool_cols:
        ctx.record_condition(
            kind="row_rule", label=p.label or "Derived columns", expression="; ".join(f"{k} = {v}" for k, v in p.derive.items()),
            rows_evaluated=len(p.rows) - error_count, rows_matched=None,
            error=f"{plural(error_count, 'row')} could not be calculated and were excluded",
        )
    if p.sort_by:
        kept = _sort_rows(kept, p.sort_by)
    if p.rank_field:
        for n, r in enumerate(kept, start=1):
            r[p.rank_field] = n
    if p.limit is not None:
        kept = kept[: p.limit]
    optional: list[str] = []
    if p.select is not None:
        missing = [c for c in p.select if kept and c not in kept[0]]
        if missing:
            raise ToolInputError(f"Cannot select unknown column(s): {', '.join(missing)}.", code="unknown_column")
        optional = [c for c in p.select_optional if kept and c in kept[0] and c not in p.select]
        kept = [{c: r.get(c) for c in [*p.select, *optional]} | ({"_source_row": r["_source_row"]} if "_source_row" in r else {}) for r in kept]
    return {
        "rows": kept,
        "count": len(kept),
        "input_count": len(p.rows),
        "columns": ([*p.select, *optional] if p.select is not None else public_columns(kept)),
        "errors": errors,
        "error_count": error_count,
    }


# -------------------------------------------------------------- table_aggregate
class AggSpec(In):
    op: Literal["count", "sum", "avg", "min", "max", "first", "count_if", "distinct_count"]
    field: str | None = None
    where: str | None = Field(default=None, description="expression, required for count_if")


class TableAggregateIn(In):
    rows: list[Row]
    group_by: list[str] = Field(default_factory=list)
    aggregations: dict[str, AggSpec]
    vars: dict[str, Any] = Field(default_factory=dict)
    sort_by: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=0)
    decimals: int = Field(default=2, ge=0, le=6)


class TableAggregateOut(Out):
    rows: list[Row]
    count: int
    input_count: int
    columns: list[str]
    errors: list[RowError] = Field(default_factory=list)
    error_count: int = 0


def table_aggregate(p: TableAggregateIn, ctx: ToolContext) -> dict:
    for name, spec in p.aggregations.items():
        if spec.op == "count_if" and not spec.where:
            raise ToolInputError(f"Aggregation '{name}' (count_if) needs a `where` expression.")
        if spec.op not in ("count", "count_if") and not spec.field:
            raise ToolInputError(f"Aggregation '{name}' ({spec.op}) needs a `field`.")
    exprs = {n: compile_expression(s.where) for n, s in p.aggregations.items() if s.where}
    groups: "OrderedDict[tuple, list[Row]]" = OrderedDict()
    for row in p.rows:
        missing = [g for g in p.group_by if g not in row]
        if missing:
            raise ToolInputError(f"Cannot group by unknown column(s): {', '.join(missing)}.", code="unknown_column")
        groups.setdefault(tuple(row[g] for g in p.group_by), []).append(row)
    errors: list[dict] = []
    error_count = 0
    out_rows: list[Row] = []
    for key, members in groups.items():
        out: Row = dict(zip(p.group_by, key))
        for name, spec in p.aggregations.items():
            if spec.op == "count":
                out[name] = len(members)
            elif spec.op == "count_if":
                n = 0
                for m in members:
                    try:
                        if exprs[name].evaluate_bool({"row": m, "vars": p.vars}):
                            n += 1
                    except ExpressionError as exc:
                        error_count += 1
                        if len(errors) < MAX_ERRORS_LISTED:
                            errors.append({"row": m.get("_source_row", 0) or 0, "message": exc.user_message})
                out[name] = n
            else:
                vals = [m.get(spec.field) for m in members if m.get(spec.field) is not None]
                if spec.op == "first":
                    out[name] = vals[0] if vals else None
                elif spec.op == "distinct_count":
                    out[name] = len({repr(v) for v in vals})
                else:
                    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
                    if len(nums) != len(vals):
                        raise ToolInputError(f"Cannot {spec.op} column '{spec.field}': it contains non-numeric values.")
                    if not nums:
                        out[name] = None
                    elif spec.op == "sum":
                        total = sum(nums)
                        out[name] = total if all(isinstance(n, int) for n in nums) else round_half_up(total, p.decimals)
                    elif spec.op == "avg":
                        out[name] = round_half_up(sum(nums) / len(nums), p.decimals)
                    elif spec.op == "min":
                        out[name] = min(nums)
                    else:
                        out[name] = max(nums)
        out_rows.append(out)
    if p.sort_by:
        out_rows = _sort_rows(out_rows, p.sort_by)
    if p.limit is not None:
        out_rows = out_rows[: p.limit]
    return {
        "rows": out_rows, "count": len(out_rows), "input_count": len(p.rows),
        "columns": [*p.group_by, *p.aggregations], "errors": errors, "error_count": error_count,
    }


# ------------------------------------------------------------------- table_join
class TableJoinIn(In):
    left: list[Row]
    right: list[Row]
    left_key: str
    right_key: str
    how: Literal["inner", "left"] = "inner"
    normalize_keys: bool = Field(default=True, description="match keys ignoring case, spaces and punctuation")
    right_fields: list[str] | None = Field(default=None, description="right columns to bring over (default: all but the key)")
    right_suffix: str = "right"


class TableJoinOut(Out):
    rows: list[Row]
    count: int
    unmatched_left: list[Row]
    unmatched_right: list[Row]
    unmatched_left_count: int
    unmatched_right_count: int
    duplicate_right_keys: int = 0


def _key(value: Any, normalize: bool) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return norm_code(value) if normalize else str(value).strip()


def table_join(p: TableJoinIn, ctx: ToolContext) -> dict:
    if p.left and p.left_key not in p.left[0]:
        raise ToolInputError(f"Left table has no column '{p.left_key}'.", code="unknown_column")
    if p.right and p.right_key not in p.right[0]:
        raise ToolInputError(f"Right table has no column '{p.right_key}'.", code="unknown_column")
    right_cols = p.right_fields if p.right_fields is not None else (
        [c for c in public_columns(p.right) if c != p.right_key] if p.right else []
    )
    index: dict[str, Row] = {}
    dup = 0
    for r in p.right:
        k = _key(r.get(p.right_key), p.normalize_keys)
        if k is None:
            continue
        if k in index:
            dup += 1
            continue
        index[k] = r
    left_cols = set(public_columns(p.left))
    rename = {c: (f"{c}_{p.right_suffix}" if c in left_cols else c) for c in right_cols}
    rows: list[Row] = []
    unmatched_left: list[Row] = []
    used: set[str] = set()
    for l in p.left:
        k = _key(l.get(p.left_key), p.normalize_keys)
        match = index.get(k) if k is not None else None
        if match is None:
            unmatched_left.append(l)
            if p.how == "left":
                rows.append({**l, **{rename[c]: None for c in right_cols}})
            continue
        used.add(k)
        rows.append({**l, **{rename[c]: match.get(c) for c in right_cols}})
    unmatched_right = [
        r for r in p.right
        if (k := _key(r.get(p.right_key), p.normalize_keys)) is None or k not in used
    ]
    return {
        "rows": rows, "count": len(rows),
        "unmatched_left": unmatched_left, "unmatched_right": unmatched_right,
        "unmatched_left_count": len(unmatched_left), "unmatched_right_count": len(unmatched_right),
        "duplicate_right_keys": dup,
    }


# ----------------------------------------------------------------- table_dedupe
class TableDedupeIn(In):
    rows: list[Row]
    key: str = Field(description="expression producing the de-duplication key, evaluated on `row`")
    keep_max: str | None = Field(default=None, description="keep the row with the largest value of this column (default: first)")


class DuplicateGroup(Out):
    key: str
    kept: Any
    removed_count: int


class TableDedupeOut(Out):
    rows: list[Row]
    count: int
    removed_count: int
    duplicate_groups: list[DuplicateGroup]
    errors: list[RowError] = Field(default_factory=list)


def table_dedupe(p: TableDedupeIn, ctx: ToolContext) -> dict:
    expr = compile_expression(p.key)
    chosen: "OrderedDict[str, Row]" = OrderedDict()
    counts: dict[str, int] = {}
    errors: list[dict] = []
    for i, row in enumerate(p.rows):
        try:
            k = str(expr.evaluate({"row": row}))
        except ExpressionError as exc:
            errors.append({"row": row.get("_source_row", i + 1), "message": exc.user_message})
            continue
        counts[k] = counts.get(k, 0) + 1
        if k not in chosen:
            chosen[k] = row
        elif p.keep_max:
            cur, new = chosen[k].get(p.keep_max), row.get(p.keep_max)
            if isinstance(new, (int, float)) and (not isinstance(cur, (int, float)) or new > cur):
                chosen[k] = row
    kept = list(chosen.values())
    groups = [
        {"key": k, "kept": (r.get("keyword") or r.get("sku") or next(iter(r.values()), None)), "removed_count": counts[k] - 1}
        for k, r in chosen.items() if counts[k] > 1
    ]
    return {
        "rows": kept, "count": len(kept), "removed_count": sum(g["removed_count"] for g in groups),
        "duplicate_groups": groups, "errors": errors[:MAX_ERRORS_LISTED],
    }


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec(
        "table_query",
        "Calculator: derive columns, filter rows with a rule, sort, rank, select and limit (rules are safe expressions).",
        TableQueryIn, TableQueryOut, table_query, kind="computation", core=True,
        expression_params=("derive.*", "where")))
    reg.register(ToolSpec(
        "table_aggregate",
        "Calculator: group rows and compute count/sum/avg/min/max/first/count_if/distinct_count.",
        TableAggregateIn, TableAggregateOut, table_aggregate, kind="computation", core=True,
        expression_params=("aggregations.*.where",)))
    reg.register(ToolSpec(
        "table_join", "Match two tables on a key (case/punctuation-insensitive) and report unmatched rows on both sides.",
        TableJoinIn, TableJoinOut, table_join, kind="computation", core=True))
    reg.register(ToolSpec(
        "table_dedupe", "Remove duplicate rows by a key expression and report what was removed.",
        TableDedupeIn, TableDedupeOut, table_dedupe, kind="computation", core=True,
        expression_params=("key",)))
