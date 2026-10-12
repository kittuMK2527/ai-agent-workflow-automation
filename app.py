"""AI Agent Workflow Automation - Streamlit UI.

Run with:  streamlit run app.py

Everything shown comes from the real catalog and real execution records: nothing on this page is
hard-coded output. The look comes from the ``ui`` package (design tokens, pure HTML components); this file
only wires data to those components.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.agent import Agent  # noqa: E402
from src.condition_evaluator import FUNCTION_DOCS  # noqa: E402
from src.config import PROJECT_ROOT, Settings  # noqa: E402
from src.logging_utils import configure_logging  # noqa: E402
from src.models import AgentResponse, Attachment, ExecutionRecord, ExecutionStatus, PendingState, StepStatus, WorkflowDefinition  # noqa: E402
from ui import components as ui  # noqa: E402
from ui.state import MODE_TEXT, outcome_for, pipeline_stages  # noqa: E402
from ui.theme import DEFAULT_PALETTE, PALETTE_NAMES, PALETTES, stylesheet  # noqa: E402

st.set_page_config(page_title="AI Agent Workflow Automation", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")

EXTENSION_DIR = PROJECT_ROOT / "examples" / "extension_wf011"
MODE_LABELS = {"auto": "Auto: LLM if available", "llm": "LLM only (strict)", "deterministic": "Deterministic fallback (no LLM)"}
FAULT_OPTIONS = {
    "None": None,
    "Shipment API: 2 timeouts, then recovers (retry demo)": ("shipment_lookup", 2),
    "Shipment API: persistent outage (partial-result demo)": ("shipment_lookup", 10),
    "Order search API: persistent outage (failure demo)": ("order_search", 10),
}
STEP_ICON = {"completed": "✅", "skipped": "⏭️", "failed": "❌", "pending": "⏳", "running": "🔄", "awaiting_input": "🟡"}


# ------------------------------------------------------------------ resources
@st.cache_resource(show_spinner="Loading workflow catalog…")
def get_agent(llm_mode: str, with_extension: bool, version: int) -> Agent:
    configure_logging(Settings.from_env().log_level)
    extra = (EXTENSION_DIR,) if with_extension and EXTENSION_DIR.exists() else ()
    settings = Settings.from_env().with_overrides(llm_mode=llm_mode, extra_catalog_dirs=extra)
    return Agent(settings)


def ss(key: str, default: Any = None) -> Any:
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def is_dark() -> bool:
    """Follow the user's Streamlit theme (Settings -> Theme)."""
    try:
        return st.context.theme.type == "dark"
    except Exception:  # older runtimes / headless tests
        return False


# --------------------------------------------------------------------- state
def reset_all() -> None:
    for k in ("response", "pending", "request_text", "sample_files", "upload_key", "history", "example_choice", "last_attachments", "quick_pick"):
        st.session_state.pop(k, None)
    st.session_state["upload_key"] = uuid.uuid4().hex


def apply_deferred_state() -> None:
    """Widget values may only be changed *before* the widget is created, so buttons set a flag
    and the next run applies it at the very top."""
    if st.session_state.pop("_switch_to_auto", False):
        st.session_state["llm_mode_choice"] = "auto"
        st.session_state["_autorun"] = True
    if st.session_state.pop("_reset_pending", False):
        reset_all()
    if st.session_state.pop("_clear_request", False):
        st.session_state["request_text"] = ""


def _load(ex: dict | None) -> None:
    if ex:
        st.session_state["request_text"] = ex["request"]
        st.session_state["sample_files"] = ex["attachments"]
        st.session_state["pending"] = None
        st.session_state["response"] = None


def load_example(examples: list[dict]) -> None:
    label = st.session_state.get("example_choice")
    _load(next((e for e in examples if e["label"] == label), None))


def load_quick(options: dict[str, dict]) -> None:
    label = st.session_state.get("quick_pick")
    _load(options.get(label))
    st.session_state["quick_pick"] = None  # a pill can be chosen again to reload the same example


def load_from_catalog(ex: dict) -> None:
    _load(ex)
    st.toast("Example loaded: open the “Run a workflow” tab and press Execute.", icon="✅")


def inspect_workflow(label: str) -> None:
    st.session_state["catalog_choice"] = label


def save_upload(uploaded, settings: Settings) -> Attachment | None:
    ext = Path(uploaded.name).suffix.lower()
    if ext not in {".csv", ".xlsx"}:
        st.error(f"'{uploaded.name}': only CSV and XLSX files are accepted.")
        return None
    data = uploaded.getvalue()
    if len(data) > settings.max_upload_bytes:
        st.error(f"'{uploaded.name}' is larger than {settings.max_upload_bytes // (1024 * 1024)} MB.")
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(uploaded.name).stem)[:40] or "upload"
    target = settings.upload_dir / f"{uuid.uuid4().hex[:8]}_{safe}{ext}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return Attachment(name=uploaded.name, path=target.name)


# ------------------------------------------------------------------- sidebar
def sidebar() -> Agent:
    with st.sidebar:
        html(ui.brand())
        html('<div class="side-label">Execution mode</div>')
        env_default = Settings.from_env().llm_mode
        mode = st.radio("Execution mode", list(MODE_LABELS), index=list(MODE_LABELS).index(env_default), format_func=MODE_LABELS.get,
                        key="llm_mode_choice", label_visibility="collapsed",
                        help="Auto uses the language model when OPENAI_API_KEY is set and falls back (visibly) if the provider fails. "
                             "LLM only never falls back. Deterministic never calls the LLM.")
        agent = get_agent(mode, st.session_state.get("with_ext", False), ss("catalog_version", 0))
        info = agent.mode_info()
        if info["effective_mode"] == "llm":
            html(ui.callout("success", f"LLM active · {info['model']}", info["reason"]))
        elif info["effective_mode"] == "paused":
            html(ui.callout("warning", "LLM paused · offline fallback" if info["configured_mode"] == "auto" else "LLM paused", info["reason"], icon_name="pause"))
        elif info["effective_mode"] == "unavailable":
            html(ui.callout("danger", "LLM mode needs OPENAI_API_KEY", info["reason"]))
        else:
            html(ui.callout("warning", "Deterministic fallback", "This is keyword matching, not an LLM. " + info["reason"]))
        if st.button("Check LLM connection", width="stretch", disabled=agent.backend is None):
            with st.spinner("Contacting the language model…"):
                ok, msg = agent.check_llm_connection()
            (st.success if ok else st.error)(msg)

        html('<div class="side-label">Catalog</div>')
        st.checkbox("Load demo extension (WF011)", key="with_ext",
                    help="Adds the 11th demo workflow from examples/extension_wf011. It is not part of the assignment's ten.")
        if st.button("↻ Reload catalog", width="stretch", help="Re-reads the Excel workbook and specs without restarting."):
            st.toast(agent.reload_catalog().status_label)

        html('<div class="side-label">Failure simulation</div>')
        st.selectbox("Simulate an outage on the next run", list(FAULT_OPTIONS), key="fault_choice",
                     help="Arms a deterministic fault in the SIMULATED APIs so retries and partial results can be demonstrated.")

        html('<div class="side-label">Appearance</div>')
        st.radio("Colour palette", PALETTE_NAMES, key="palette", label_visibility="collapsed", format_func=lambda n: f"{PALETTES[n]['dot']}  {n}",
                 help="Light or dark follows your Streamlit theme (menu → Settings → Theme).")
        chosen = st.session_state.get("palette", DEFAULT_PALETTE)
        p = PALETTES[chosen]["light"]
        html(ui.palette_strip(chosen, (p["accent"], p["accent2"], p["accent3"]), PALETTES[chosen]["tagline"]))

        html('<div class="side-label">Session</div>')
        if st.button("🧹 Reset / new execution", width="stretch"):
            st.session_state["_reset_pending"] = True
            st.rerun()
        html('<div class="muted" style="margin-top:14px;font-size:.78rem">All business data is <b>simulated fixture data</b> (data/fixtures). '
             "No email, payment or external system is ever contacted.</div>")
    return agent


# ---------------------------------------------------------------------- hero
def header(agent: Agent) -> None:
    info, rep = agent.mode_info(), agent.registry.report
    defs = agent.registry.all()
    sim = [t for t in agent.tools.specs() if t.simulated]
    if info["effective_mode"] == "llm":
        mode_chip = ui.glass_chip(f"LLM · {info['model']}", "success")
    elif info["effective_mode"] == "paused":
        mode_chip = ui.glass_chip("LLM paused · offline fallback active" if info["configured_mode"] == "auto" else "LLM paused", "warning")
    elif info["effective_mode"] == "deterministic":
        mode_chip = ui.glass_chip("Deterministic fallback (not an LLM)", "warning")
    else:
        mode_chip = ui.glass_chip("LLM unavailable: key missing", "danger")
    chips = [
        mode_chip,
        ui.glass_chip(f"Catalog valid · {len(defs)} workflows" if rep.ok else "Catalog degraded", "success" if rep.ok else "danger"),
        ui.glass_chip("Tools: simulated data", "violet"),
        ui.glass_chip(f"Workbook {rep.workbook_sha256[:8]}…", icon_name="file"),
    ]
    kpis = [(len(defs), "Workflows", "workflow"), (sum(len(d.steps) for d in defs), "Executable steps", "layers"),
            (sum(len(d.conditions) for d in defs), "Decision rules", "branch"), (len(agent.tools.names()), "Registered tools", "tool"),
            (len(sim), "Simulated systems", "database")]
    html(ui.hero(eyebrow="Excel-driven · LLM-routed · fully traceable", title="AI Agent Workflow Automation",
                 subtitle="Describe a business task in plain language. The agent picks one of the Excel-defined workflows, extracts the inputs, "
                          "runs it step by step through one generic engine with registered tools, and shows every decision it made.",
                 chips=chips, kpis=kpis))
    if not rep.ok:
        st.error("The workflow catalog did not load cleanly: " + "; ".join(i.message for i in rep.errors[:3]))


# ------------------------------------------------------------------- run tab
def examples_for(agent: Agent) -> list[dict]:
    return [{**e, "label": f"{e['workflow_id']} · {e['title']} — “{e['request'][:70]}{'…' if len(e['request']) > 70 else ''}”"} for e in agent.examples()]


def quick_options(agent: Agent, exs: list[dict]) -> dict[str, dict]:
    """One quick-start pill per workflow: its first (complete) example."""
    out: dict[str, dict] = {}
    for d in agent.registry.all():
        first = next((e for e in exs if e["workflow_id"] == d.id), None)
        if first:
            out[d.name] = first
    return out


def run_tab(agent: Agent) -> None:
    pending: PendingState | None = ss("pending")
    resp: AgentResponse | None = ss("response")
    exs = examples_for(agent)
    quick = quick_options(agent, exs)
    left, right = st.columns([7, 5], gap="large")

    with left:
        html(ui.section(1, "Ask the agent", "Plain language, or pick a quick start"))
        with st.container(key="card_command"):
            if pending:
                html(ui.callout("info", "The agent is waiting for your answer", resp.message.splitlines()[0] if resp and resp.message else "", icon_name="pause"))
            st.text_area("Your reply to the agent's question" if pending else "What would you like the agent to do?", key="request_text", height=116,
                         placeholder="e.g. Which products need restocking?  ·  Where is order ORD-1001?  ·  Create a campaign brief …")
            st.pills("Quick start: one example per workflow", list(quick), key="quick_pick", on_change=load_quick, args=(quick,),
                     help="Loads the workflow's first example into the box. Press Execute to run it.")
            with st.expander(f"More examples ({len(exs)}) · every Excel test question and fuller variants"):
                st.selectbox("Example requests", [e["label"] for e in exs], index=None, placeholder="Pick an example to fill the box…",
                             key="example_choice", on_change=load_example, args=(exs,), label_visibility="collapsed")
            samples = [f for f in ("vendor_file_sample.csv", "vendor_file_sample.xlsx", "keywords_sample.csv") if (agent.settings.fixtures_dir / f).exists()]
            with st.expander("Attachments (CSV / XLSX)", expanded=bool(st.session_state.get("sample_files"))):
                up = st.file_uploader("Attach a vendor file, keyword list…", type=["csv", "xlsx"], accept_multiple_files=True, key=f"uploader_{ss('upload_key', 'u0')}")
                st.multiselect("…or use a sample file", samples, key="sample_files", help="Sample files live in data/fixtures.")
            b1, b2, b3 = st.columns([2.4, 1.1, 2.6], gap="small")
            go = b1.button("▶ Execute Workflow" if not pending else "▶ Send reply", type="primary", width="stretch")
            go = st.session_state.pop("_autorun", False) or go  # "Switch to Auto and retry" re-runs the same request
            if b2.button("Reset", width="stretch"):
                st.session_state["_reset_pending"] = True
                st.rerun()
            b3.caption("Runs through the generic engine. Simulated systems are always labelled.")

    with right:
        html(ui.section(2, "Pipeline", "What the agent is doing"))
        with st.container(key="card_pipeline"):
            html(ui.pipeline(pipeline_stages(resp)))

    if go:
        execute(agent, up, pending)

    resp = ss("response")
    if resp is None:
        html(ui.section(None, "How it works", "Pick a quick start or type a request above, then press Execute Workflow", icon_name="sparkle"))
        html(ui.how_it_works([
            ("route", "Understands your request", "Matches your words to one of the Excel-defined workflows: with an LLM when a key is set, otherwise a clearly labelled fallback."),
            ("cpu", "Runs the steps with tools", "One generic engine executes each step with registered tools, applies the Excel decision rules, and retries transient errors."),
            ("flag", "Explains every decision", "See which steps ran, which conditions were checked with which values, and what came back. Simulated data is always labelled."),
        ]))
        return
    render_outcome(resp)
    render_selection(agent, resp)
    if resp.state == "ambiguous":
        choose_workflow(agent, resp)
    if resp.record:
        render_trace(resp.record)
    render_result(resp)


def execute(agent: Agent, uploaded, pending: PendingState | None) -> None:
    text = (st.session_state.get("request_text") or "").strip()
    attachments = [Attachment(name=n, path=n) for n in st.session_state.get("sample_files", [])]
    for f in uploaded or []:
        a = save_upload(f, agent.settings)
        if a:
            attachments.append(a)
    fault = FAULT_OPTIONS[st.session_state.get("fault_choice", "None")]
    if fault:
        agent.faults.arm_transient(fault[0], fault[1])
    live = st.empty()
    lines: list[str] = []

    def listener(ev: dict) -> None:
        t = ev["type"]
        if t == "step_started":
            lines.append(f"🔄 {ev['name']}…")
        elif t == "step_retry":
            lines.append(f"🔁 retry #{ev['attempt']}: {ev['message']}")
        elif t == "step_failed":
            lines.append(f"❌ {ev['error']['message']}")
        elif t == "step_awaiting_input":
            lines.append("🟡 waiting for your input")
        if lines:
            live.markdown("\n\n".join(lines[-6:]))

    with st.status("Routing the request and running the workflow…", expanded=False) as status:
        resp = agent.handle(text, attachments=attachments, pending=pending, listener=listener)
        status.update(label=f"Finished: {resp.state.replace('_', ' ')}", state="complete" if resp.state != "error" else "error")
    agent.faults.clear()
    live.empty()
    st.session_state["response"] = resp
    st.session_state["pending"] = resp.pending if resp.state == "needs_input" else None
    st.session_state["last_attachments"] = attachments
    if resp.state == "needs_input":
        st.session_state["_clear_request"] = True
    ss("history", []).append(resp)
    st.rerun()


def choose_workflow(agent: Agent, resp: AgentResponse) -> None:
    html(ui.subhead("Which one do you mean?", "choose to continue"))
    cols = st.columns(max(len(resp.routing.candidates), 1))
    for col, cand in zip(cols, resp.routing.candidates):
        if col.button(f"{cand.workflow_id} · {cand.name}", key=f"choose_{cand.workflow_id}", width="stretch"):
            new = agent.handle(resp.request, attachments=st.session_state.get("last_attachments") or [], workflow_id=cand.workflow_id)
            st.session_state["response"] = new
            st.session_state["pending"] = new.pending if new.state == "needs_input" else None
            st.rerun()


# ----------------------------------------------------------------- run panels
def mode_chip(mode: str, model: str | None) -> str:
    if mode == "llm":
        return ui.chip(f"Selected by LLM · {model or ''}", "success", icon_name="sparkle")
    if mode == "deterministic":
        return ui.chip("Fallback router · not an LLM", "warning", icon_name="alert")
    return ui.chip("Continuing your request" if mode == "continuation" else "Chosen by you", "info", icon_name="route")


def render_outcome(resp: AgentResponse) -> None:
    o = outcome_for(resp)
    chips: list[str] = []
    rec, r = resp.record, resp.routing
    if r and r.workflow_id:
        chips.append(ui.chip(r.workflow_id, "accent", mono=True, icon_name="workflow"))
    if rec:
        if rec.duration_ms is not None:
            chips.append(ui.chip(ui.fmt_ms(rec.duration_ms), "neutral", icon_name="clock"))
        chips.append(ui.chip(f"{len(rec.completed_step_ids)}/{len(rec.steps)} steps", "neutral", icon_name="layers"))
        sim = [t.tool for t in rec.tools_used if t.simulated]
        if sim:
            chips.append(ui.chip(f"{len(sim)} simulated tool(s)", "violet", icon_name="database", title=", ".join(sim)))
        if rec.llm_used_in_tools:
            chips.append(ui.chip("LLM used for content", "success", icon_name="sparkle"))
    elif resp.duration_ms is not None:
        chips.append(ui.chip(ui.fmt_ms(resp.duration_ms), "neutral", icon_name="clock"))
    html(ui.outcome(tone=o["tone"], label=o["label"], headline=o["headline"], chips=chips, lines=o["lines"]))
    if resp.state == "needs_input":
        st.caption("Type your answer in the box above and press **Send reply**.")
    if resp.state == "error" and resp.error is not None and str(resp.error.code).startswith("llm_") and st.session_state.get("llm_mode_choice") == "llm":
        st.caption("**LLM only** mode never falls back, so this request stopped. **Auto** answers with the labelled offline fallback instead.")
        if st.button("↻ Switch to Auto and retry", key="retry_in_auto", type="primary"):
            st.session_state["_switch_to_auto"] = True
            st.rerun()


def render_selection(agent: Agent, resp: AgentResponse) -> None:
    r = resp.routing
    html(ui.section(3, "Workflow selection", "Which workflow, why, and which inputs were understood"))
    with st.container(key="card_selection"):
        if r is None:
            html(ui.callout("danger", "Rejected before routing", resp.message))
            return
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            if r.workflow_id and agent.registry.has(r.workflow_id):
                d = agent.registry.get(r.workflow_id)
                html(ui.workflow_title(d.name, d.id, [mode_chip(r.mode, r.model)]))
                st.caption(d.description)
            elif r.decision == "no_match":
                html(ui.workflow_title("No matching workflow", "—", [mode_chip(r.mode, r.model)]))
            else:
                html(ui.workflow_title("Needs clarification", "?", [mode_chip(r.mode, r.model)]))
            html(ui.quote(r.explanation or "—"))
            html(ui.meter(r.confidence, "confidence"))
            if r.fallback_reason:
                html(ui.callout("info", "Mode note", r.fallback_reason))
            if r.candidates and r.decision != "selected":
                st.dataframe(pd.DataFrame([{"Workflow": f"{c.workflow_id} · {c.name}", "Score": c.score, "Matched": c.reason} for c in r.candidates]),
                             hide_index=True, width="stretch")
        with c2:
            res = resp.resolution
            if res is None:
                html('<div class="muted">No inputs were extracted for this request.</div>')
                return
            how = {"llm": ui.chip("LLM extraction", "success"), "deterministic": ui.chip("fallback extraction", "warning")}.get(res.extraction_mode, "")
            html(ui.subhead("Extracted inputs") + (f'<div class="chips" style="margin-bottom:8px">{how}</div>' if how else ""))
            html(ui.input_rows([{"name": k, "value": ", ".join(map(str, v)) if isinstance(v, list) else v, "source": res.sources.get(k, "")}
                                for k, v in res.values.items() if v is not None]))
            for m in res.missing:
                html(ui.callout("warning", "Missing", m.description))
            for i in res.invalid:
                html(ui.callout("danger", f"Invalid: {i.name.replace('_', ' ')}", i.reason))
            if res.extraction_note and res.extraction_mode == "deterministic":
                st.caption(res.extraction_note)


def render_trace(rec: ExecutionRecord) -> None:
    html(ui.section(4, "Execution trace", "Every step, tool, condition and branch that actually ran"))
    with st.container(key="card_trace"):
        done = len(rec.completed_step_ids)
        skipped = sum(s.status == StepStatus.SKIPPED for s in rec.steps)
        st.progress(min((done + skipped) / max(len(rec.steps), 1), 1.0),
                    text=f"{done} of {len(rec.steps)} steps completed" + (f" · {skipped} skipped" if skipped else "")
                    + (f" · {ui.fmt_ms(rec.duration_ms)}" if rec.duration_ms is not None else ""))
        t_time, t_steps, t_cond, t_err = st.tabs(["Timeline", f"Step details ({len(rec.steps)})", f"Conditions & branches ({len(rec.conditions)})",
                                                  f"Errors & retries ({len(rec.errors) + len(rec.recovery_attempts)})"])
        with t_time:
            html(ui.timeline(rec.steps))
        with t_steps:
            for s in rec.steps:
                tool = f"{s.tool}{' · SIMULATED' if s.simulated else ''}" if s.tool else "decision"
                dur = f" · {ui.fmt_ms(s.duration_ms)}" if s.duration_ms is not None and s.status != StepStatus.PENDING else ""
                retry = f" · retried ×{len(s.attempts)}" if s.attempts else ""
                with st.expander(f"{STEP_ICON[s.status.value]} {s.name} — {tool}{retry}{dur}",
                                 expanded=s.status in (StepStatus.FAILED, StepStatus.AWAITING_INPUT)):
                    if s.excel_step:
                        st.caption(f"Excel step: “{s.excel_step}” · step id `{s.step_id}`")
                    if s.description:
                        st.write(s.description)
                    if s.skipped_reason:
                        html(ui.callout("neutral", "Skipped", s.skipped_reason))
                    if s.note:
                        html(ui.callout("info", "Note", s.note))
                    if s.output_summary:
                        st.markdown(f"**Output:** {s.output_summary}")
                    if s.error:
                        html(ui.callout("danger", s.error.category.value.replace("_", " ").capitalize(), s.error.message))
                    for a in s.attempts:
                        html(ui.callout("warning", f"Attempt {a.attempt} failed", f"{a.message}: retried after {a.delay_seconds}s"))
                    if s.params:
                        with st.popover("Tool input"):
                            st.json(s.params, expanded=1)
                    if s.output_preview is not None:
                        with st.popover("Tool output (preview)"):
                            st.json(s.output_preview, expanded=1)
        with t_cond:
            if not rec.conditions:
                st.caption("This run evaluated no conditions.")
            for c in rec.conditions:
                res = "—" if c.result is None else ("true" if c.result else "false")
                with st.expander(f"#{c.id} · {c.label} → {c.outcome[:90]}"):
                    st.markdown(f"Step `{c.step_id}` · kind **{c.kind}**")
                    if c.expression:
                        html(f'<div>{ui.code_chip(c.expression)}</div>')
                    else:
                        st.caption("otherwise (default branch)")
                    if c.values:
                        st.markdown("**Values used**")
                        st.json(c.values, expanded=True)
                    if c.rows_evaluated is not None:
                        st.markdown(f"Rows evaluated: **{c.rows_evaluated}** · matched: **{c.rows_matched}**")
                    if c.samples:
                        st.caption("Sample evaluations")
                        st.json(c.samples, expanded=False)
                    st.markdown(f"Result: **{res}** · Outcome: {c.outcome}" + (f" · Next step: `{c.next_step}`" if c.next_step else ""))
                    if c.error:
                        html(ui.callout("danger", "Evaluation problem", c.error))
        with t_err:
            if not rec.errors and not rec.recovery_attempts:
                st.caption("No errors or retries.")
            for e in rec.errors:
                tone = {"user_correctable": "warning", "recoverable": "info", "terminal": "danger"}[e.category.value]
                html(ui.callout(tone, f"{e.category.value.replace('_', ' ').capitalize()} · {e.code}" + (" · recovered" if e.recovered else ""), e.message))
            if rec.recovery_attempts:
                html(ui.subhead("Recovery attempts"))
                st.dataframe(pd.DataFrame([a.model_dump(mode="json") for a in rec.recovery_attempts]), hide_index=True, width="stretch")


def render_result(resp: AgentResponse) -> None:
    rec = resp.record
    if resp.state == "error" and rec and rec.partial_results:
        html(ui.section(5, "Results", "Collected before the failure (preserved)"))
        with st.container(key="card_result"):
            st.json(rec.partial_results, expanded=False)
        return
    if resp.state != "executed" or rec is None or rec.final_result is None:
        return
    fr = rec.final_result
    html(ui.section(5, "Result", "Business-friendly answer and the structured data behind it"))
    with st.container(key="card_result"):
        for w in fr.warnings:
            html(ui.callout("warning", "Heads up", w))
        render_data(fr.data, rec.run_id)
        sim = [t.tool for t in rec.tools_used if t.simulated]
        if sim:
            html(ui.callout("info", "Simulated systems", "These results came from fixture data, not a live system: " + ", ".join(sim), icon_name="database"))
        st.download_button("⬇ Download execution record (JSON)", data=json.dumps(rec.model_dump(mode="json"), indent=2, default=str),
                           file_name=f"execution_{rec.run_id}.json", mime="application/json")


def render_data(data: dict[str, Any], run_id: str) -> None:
    scalars = {k: v for k, v in data.items() if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 70 and "\n" not in v)}
    if scalars:
        html(ui.kpi_grid([{"label": k.replace("_", " "), "value": v} for k, v in scalars.items()]))
    for k, v in data.items():
        if k in scalars:
            continue
        title = k.replace("_", " ").capitalize()
        if isinstance(v, list) and v and isinstance(v[0], dict):
            html(ui.subhead(title, f"{len(v):,} row(s)"))
            df = _frame(v)
            st.dataframe(df, hide_index=True, width="stretch", height=min(40 + 36 * len(df), 440))
            st.download_button("⬇ CSV", df.to_csv(index=False), file_name=f"{k}.csv", mime="text/csv", key=f"dl_{run_id}_{k}")
        elif isinstance(v, list):
            html(ui.subhead(title, f"{len(v)} item(s)"))
            html(ui.chip_row(ui.chip(x, "neutral") for x in v) if v else '<div class="muted">None</div>')
        elif isinstance(v, dict):
            html(ui.subhead(title))
            if all(not isinstance(x, (dict, list)) for x in v.values()):
                html(ui.input_rows([{"name": a, "value": b if b is not None else "—", "source": ""} for a, b in v.items()]))
            else:
                st.json(v, expanded=2)
        elif isinstance(v, str) and "\n" not in v and len(v) <= 400:
            html(ui.subhead(title))
            html(ui.quote(v))
        else:
            with st.expander(title, expanded=len(str(v)) < 700):
                st.markdown(str(v))


def _frame(rows: list[dict]) -> pd.DataFrame:
    clean = [{k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v) for k, v in r.items() if not str(k).startswith("_")} for r in rows]
    return pd.DataFrame(clean)


# --------------------------------------------------------------- catalog tab
def catalog_tab(agent: Agent) -> None:
    rep = agent.registry.report
    defs = agent.registry.all(include_disabled=True)
    html(ui.callout("success" if rep.ok else "danger", f"Catalog validation: {rep.status_label}",
                    f"Workbook SHA-256 {rep.workbook_sha256[:16]}… · sheets: " + ", ".join(f"{s.name} ({s.role}, {s.rows_read} rows)" for s in rep.sheets)))
    if rep.issues:
        with st.expander(f"Validation issues ({len(rep.issues)})", expanded=not rep.ok):
            for i in rep.issues:
                html(ui.callout("danger" if i.severity == "error" else "warning", i.workflow_id or "catalog", i.message))
    html(ui.kpi_grid([
        {"label": "Workflows", "value": len(defs), "hint": "from the Excel workbook"},
        {"label": "Excel steps", "value": sum(len(d.excel_steps) for d in defs), "hint": "as written in the workbook"},
        {"label": "Executable steps", "value": sum(len(d.steps) for d in defs), "hint": "after interpretation"},
        {"label": "Decision rules", "value": sum(len(d.conditions) for d in defs), "hint": "conditions, branches, row rules"},
        {"label": "Example requests", "value": sum(len(d.examples) for d in defs)},
    ]))
    html(ui.section(None, "Workflows", "Select one to inspect its definition, steps and rules", icon_name="grid"))
    labels = [f"{d.id} · {d.name}" for d in defs]
    sim = frozenset(t.name for t in agent.tools.specs() if t.simulated)
    kinds = {t.name: t.kind for t in agent.tools.specs()}
    chosen = ss("catalog_choice", labels[0])
    if chosen not in labels:
        chosen = st.session_state["catalog_choice"] = labels[0]
    for row_start in range(0, len(defs), 3):
        cols = st.columns(3, gap="medium")
        for col, d, label in zip(cols, defs[row_start:row_start + 3], labels[row_start:row_start + 3]):
            with col:
                html(ui.workflow_card(d, selected=(label == chosen), simulated_tools=sim, tool_kinds=kinds))
                st.button("Inspect" if label != chosen else "Inspecting ✓", key=f"inspect_{d.id}", width="stretch", on_click=inspect_workflow, args=(label,),
                          disabled=(label == chosen))
    st.selectbox("Or pick a workflow from the list", labels, key="catalog_choice")
    render_definition(agent, agent.registry.get(st.session_state["catalog_choice"].split(" · ")[0]))


def render_definition(agent: Agent, d: WorkflowDefinition) -> None:
    html(ui.section(None, f"{d.id} · {d.name}", d.source, icon_name="book"))
    with st.container(key="card_definition"):
        t_excel, t_steps, t_inputs, t_rules, t_notes, t_ex = st.tabs(["Excel definition", "Executable steps", "Inputs", "Rules & conditions", "Interpretations & errors", "Examples"])
        with t_excel:
            html(ui.excel_card([("Trigger", d.triggers[0] if d.triggers else ""), ("Inputs", "; ".join(d.excel_inputs)), ("Steps", " → ".join(d.excel_steps)),
                                ("Decision logic", d.decision_logic), ("Tools required", "; ".join(d.tools_required)), ("Expected output", d.success_output)]))
            st.caption("Verbatim from the workbook. The registry refuses the workflow if the spec no longer matches this text.")
        with t_steps:
            rows = []
            for s in d.steps:
                t = agent.tools.get(s.tool) if s.tool else None
                rows.append({"Step": s.id, "Excel step": s.excel_step, "Kind": s.kind, "Tool": s.tool or "decision", "Simulated": bool(t and t.simulated),
                             "Guard": s.when or "", "On failure": s.on_failure})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(40 + 36 * len(rows), 460))
            html(ui.subhead("Tool mapping (Excel phrase → registered tools)"))
            html(ui.spec_list(f"{k} → {', '.join(v)}" for k, v in d.tool_map.items()))
        with t_inputs:
            st.dataframe(pd.DataFrame([{"Name": i.name, "Type": i.type.value, "Required": i.required, "Default": "" if i.default is None else str(i.default),
                                        "Covers (Excel)": ", ".join(i.covers), "Description": i.description} for i in d.inputs]), hide_index=True, width="stretch")
            for s in d.data_sources:
                html(ui.callout("info", f"Data source: {s.name}", s.description + f" (covers: {', '.join(s.covers)})", icon_name="database"))
        with t_rules:
            if not d.conditions:
                st.caption("No conditions.")
            for c in d.conditions:
                html(f'<div class="kv" style="grid-template-columns:minmax(120px,28%) 1fr auto"><span class="kv-k">{ui.e(c.step_id)}</span>'
                     f'<span class="kv-v"><b>{ui.e(c.label)}</b><br>{ui.code_chip(c.expression or "otherwise")}</span>{ui.chip(c.action or c.kind.replace("_", " "), "info")}</div>')
        with t_notes:
            html(ui.subhead("Interpretations (where the Excel is silent or vague)"))
            html(ui.spec_list(d.interpretation_notes) if d.interpretation_notes else '<div class="muted">None.</div>')
            html(ui.subhead("Error handling"))
            html(ui.spec_list(d.error_handling))
        with t_ex:
            for n, ex in enumerate(d.examples):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{ex.title}**  \n“{ex.request}”" + (f"  \n📎 {', '.join(ex.attachments)}" if ex.attachments else ""))
                c2.button("Try this", key=f"try_{d.id}_{n}", on_click=load_from_catalog,
                          args=({"request": ex.request, "attachments": ex.attachments},), width="stretch")


# ------------------------------------------------------------ developer tab
def developer_tab(agent: Agent) -> None:
    resp: AgentResponse | None = ss("response")
    t_cfg, t_tools, t_def, t_rec, t_expr = st.tabs(["Configuration", "Tools: simulation vs real", "Normalized definition", "Execution record", "Expression language"])
    with t_cfg:
        st.caption("Secrets are never shown.")
        st.json({**agent.settings.public_summary(), **agent.mode_info()}, expanded=False)
        audit = agent.settings.output_dir / "audit_log.jsonl"
        if audit.exists():
            with st.expander("Audit log: last 10 executions (redacted)"):
                st.code("\n".join(audit.read_text(encoding="utf-8").splitlines()[-10:]), language="json")
    with t_tools:
        st.dataframe(pd.DataFrame([{"Tool": t.name, "Kind": t.kind, "Simulated": t.simulated, "Side effect": t.side_effect, "Idempotent": t.idempotent,
                                    "Core": t.core, "Description": t.description} for t in agent.tools.infos()]), hide_index=True, width="stretch")
        html(ui.callout("info", "How to read this", "`simulated_api` tools read fixture data and never contact a real system. `file_output` writes CSV files into outputs/. "
                        "`llm` calls the language model only when a key is configured; otherwise it uses a labelled template."))
        sel = st.selectbox("Tool schema", agent.tools.names(), key="tool_schema_choice")
        info = agent.tools.get(sel).info()
        a, b = st.tabs(["Input schema", "Output schema"])
        a.json(info.input_schema, expanded=1)
        b.json(info.output_schema, expanded=1)
    with t_def:
        pick = st.selectbox("Workflow", [d.id for d in agent.registry.all(include_disabled=True)], key="dev_wf")
        st.json(agent.registry.get(pick).model_dump(mode="json"), expanded=1)
        st.download_button("⬇ Download normalized catalog (JSON)", json.dumps(agent.registry.normalized_catalog(), indent=2, default=str),
                           file_name="normalized_workflows.json", mime="application/json")
    with t_rec:
        if resp and resp.record:
            st.json(resp.record.model_dump(mode="json"), expanded=1)
            html(ui.subhead("Tool executions"))
            st.dataframe(pd.DataFrame([t.model_dump() for t in resp.record.tools_used]), hide_index=True, width="stretch")
        elif resp:
            st.json(resp.model_dump(mode="json", exclude={"record"}), expanded=1)
        else:
            html(ui.empty_state("No execution yet", "Run a workflow to see its structured execution record here.", "file"))
    with t_expr:
        st.caption("Rules in the specs use this safe language (no eval, no attribute access, whitelisted functions only).")
        html(ui.spec_list(f"{sig}: {doc}" for sig, doc in FUNCTION_DOCS.items()))


# ----------------------------------------------------------------------- main
def main() -> None:
    apply_deferred_state()
    html(stylesheet(is_dark(), st.session_state.get("palette", DEFAULT_PALETTE)))
    agent = sidebar()
    header(agent)
    t_run, t_cat, t_dev = st.tabs(["Run a workflow", "Workflow catalog", "Developer view"])
    with t_run:
        run_tab(agent)
    with t_cat:
        catalog_tab(agent)
    with t_dev:
        developer_tab(agent)
    html(ui.footer("AI Agent Workflow Automation · Excel is the source of truth · all business data is simulated"))


main()
