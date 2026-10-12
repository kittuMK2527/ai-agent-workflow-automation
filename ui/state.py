"""View state derived from agent responses (pure, no Streamlit)."""

from __future__ import annotations

from src.models import AgentResponse, ExecutionStatus, StepStatus

from .components import Stage

IDLE_DETAILS = {
    "route": "Pick the matching workflow",
    "extract": "Read the inputs from your words",
    "validate": "Check types, files and rules",
    "execute": "Run the steps with tools",
    "result": "Show the answer and the trace",
}
LABELS = {"route": "Route", "extract": "Extract", "validate": "Validate", "execute": "Execute", "result": "Result"}
MODE_TEXT = {"llm": "LLM", "deterministic": "fallback router", "continuation": "continuing", "direct": "chosen by you"}


def pipeline_stages(resp: AgentResponse | None) -> list[Stage]:
    """The five pipeline stages and their state for the last response (``None`` = nothing run yet)."""
    st = {k: ["idle", IDLE_DETAILS[k]] for k in LABELS}
    if resp is not None:
        _fill(st, resp)
    return [Stage(key=k, label=LABELS[k], state=v[0], detail=v[1]) for k, v in st.items()]


def _fill(st: dict[str, list[str]], resp: AgentResponse) -> None:
    r, res, rec = resp.routing, resp.resolution, resp.record
    if r is None:
        st["route"] = ["error", "Request rejected before routing"]
        st["result"] = ["error", "Nothing was run"]
        return
    if r.decision == "no_match":
        st["route"] = ["attention", "No workflow matches"]
        st["result"] = ["attention", "Nothing was run"]
        return
    if r.decision == "ambiguous":
        st["route"] = ["attention", "Needs your choice"]
        st["result"] = ["attention", "Waiting for your choice"]
        return
    st["route"] = ["done", f"{r.workflow_id} · {MODE_TEXT.get(r.mode, r.mode)}"]
    if res is not None:
        found = sum(1 for k, v in res.values.items() if v is not None and res.sources.get(k) in ("request", "attachment", "reply"))
        mode = {"llm": "LLM", "deterministic": "fallback", "none": ""}.get(res.extraction_mode, "")
        st["extract"] = ["done", f"{found} value(s) found" + (f" · {mode}" if mode else "")]
        if res.complete:
            st["validate"] = ["done", "All inputs valid"]
        else:
            n = len(res.missing) + len(res.invalid)
            st["validate"] = ["attention", f"{n} input(s) need your answer"]
    if rec is None:
        if resp.state == "needs_input":
            st["result"] = ["attention", "Waiting for your reply"]
        return
    done = len(rec.completed_step_ids)
    total = len(rec.steps)
    if rec.status == ExecutionStatus.COMPLETED:
        st["execute"] = ["done", f"{done}/{total} steps"]
        st["result"] = ["done", "Completed"]
    elif rec.status == ExecutionStatus.PARTIAL:
        st["execute"] = ["attention", f"{done}/{total} steps · one failed"]
        st["result"] = ["attention", "Partial result"]
    elif rec.status == ExecutionStatus.AWAITING_INPUT:
        st["execute"] = ["attention", "Paused for your input"]
        st["result"] = ["attention", "Waiting for your reply"]
    elif rec.status == ExecutionStatus.FAILED:
        failed = next((s for s in rec.steps if s.status == StepStatus.FAILED), None)
        st["execute"] = ["error", f"Stopped at “{failed.name}”" if failed else "Stopped"]
        st["result"] = ["error", "No result"]


def outcome_for(resp: AgentResponse) -> dict:
    """Tone / label / headline / lines for the outcome strip of a response."""
    rec = resp.record
    if resp.state == "executed" and rec and rec.final_result:
        fr = rec.final_result
        partial = rec.status == ExecutionStatus.PARTIAL
        tone = "warning" if partial or fr.severity == "warning" else "info" if fr.severity == "info" else "success"
        label = "Completed with problems (partial)" if partial else "Escalated" if fr.outcome == "escalated" else "Completed"
        return {"tone": tone, "label": label, "headline": fr.headline, "lines": []}
    if resp.state == "needs_input":
        return {"tone": "info", "label": "Waiting for your input", "headline": resp.message.splitlines()[0] if resp.message else "More information is needed",
                "lines": resp.message.splitlines()[1:]}
    if resp.state == "error":
        return {"tone": "danger", "label": "Workflow failed" if rec else "Request could not be processed", "headline": resp.message, "lines": []}
    if resp.state == "ambiguous":
        return {"tone": "warning", "label": "Which one do you mean?", "headline": resp.message, "lines": []}
    return {"tone": "neutral", "label": "No matching workflow", "headline": resp.message, "lines": []}
