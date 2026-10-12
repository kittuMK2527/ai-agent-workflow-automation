"""Plain-text / markdown rendering of agent responses and execution records.

Used by the CLI scripts and tests; the Streamlit app renders the same
``ExecutionRecord`` data with its own widgets.
"""

from __future__ import annotations

import json
from typing import Any

from .models import AgentResponse, ExecutionRecord, StepStatus

STATUS_ICON = {
    StepStatus.COMPLETED: "OK ",
    StepStatus.FAILED: "ERR",
    StepStatus.SKIPPED: "SKP",
    StepStatus.AWAITING_INPUT: "ASK",
    StepStatus.RUNNING: "RUN",
    StepStatus.PENDING: "...",
}


def table_to_markdown(rows: list[dict[str, Any]], *, limit: int = 15) -> str:
    if not rows:
        return "_(none)_"
    cols = [c for c in rows[0] if not str(c).startswith("_")]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:limit]:
        out.append("| " + " | ".join(_cell(r.get(c)) for c in cols) + " |")
    if len(rows) > limit:
        out.append(f"| … {len(rows) - limit} more row(s) |" + " |" * (len(cols) - 1))
    return "\n".join(out)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=str)[:60]
    return str(v).replace("|", "/")[:60]


def result_data_to_markdown(data: dict[str, Any]) -> str:
    parts = []
    for key, value in data.items():
        label = key.replace("_", " ").capitalize()
        if isinstance(value, list) and value and isinstance(value[0], dict):
            parts.append(f"**{label}** ({len(value)})\n\n{table_to_markdown(value)}")
        elif isinstance(value, list):
            parts.append(f"**{label}**: {', '.join(map(str, value)) or '(none)'}")
        elif isinstance(value, dict):
            parts.append(f"**{label}**: " + "; ".join(f"{k}={v}" for k, v in value.items()))
        else:
            parts.append(f"**{label}**: {value}")
    return "\n\n".join(parts)


def trace_to_text(record: ExecutionRecord) -> str:
    """Execution trace as aligned text: steps, tools, statuses, conditions, errors."""
    lines = [f"Run {record.run_id}  {record.workflow_id} {record.workflow_name}  ->  {record.status.value.upper()}"
             + (f"  ({record.duration_ms} ms)" if record.duration_ms is not None else "")]
    for s in record.steps:
        tool = f"[{s.tool}{' · SIMULATED' if s.simulated else ''}]" if s.tool else "[decision]"
        detail = s.output_summary or s.note or s.skipped_reason or ""
        lines.append(f"  {STATUS_ICON[s.status]} {s.step_id:<28} {tool:<34} {detail[:80]}")
        for a in s.attempts:
            lines.append(f"        retry #{a.attempt}: {a.message} (waited {a.delay_seconds}s)")
        if s.error:
            lines.append(f"        ERROR [{s.error.category.value}]: {s.error.message}")
    if record.conditions:
        lines.append("  Conditions:")
        for c in record.conditions:
            res = "-" if c.result is None else ("true" if c.result else "false")
            lines.append(f"    #{c.id} {c.step_id}: {c.label} | {c.expression or 'otherwise'} | values={c.values} | {res} -> {c.outcome}")
    return "\n".join(lines)


def response_to_text(resp: AgentResponse) -> str:
    out = [f"[{resp.state}] {resp.message}"]
    if resp.routing:
        r = resp.routing
        out.append(f"Routing ({r.mode}{', model ' + r.model if r.model else ''}): {r.decision} {r.workflow_id or ''} "
                   f"confidence={r.confidence} - {r.explanation}")
        if r.fallback_reason:
            out.append(f"  note: {r.fallback_reason}")
    if resp.record:
        out.append(trace_to_text(resp.record))
        if resp.record.final_result:
            out.append(result_data_to_markdown(resp.record.final_result.data))
    for w in resp.warnings:
        out.append(f"warning: {w}")
    return "\n".join(out)
