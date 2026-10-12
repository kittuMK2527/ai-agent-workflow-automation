"""HTML building blocks for the premium UI.

Rules every function here follows:

* **Escape everything dynamic.** Request text, messages, file names, tool outputs: all go through :func:`e`.
* **Single-line HTML starting with a block element.** Streamlit passes it through Markdown first; blank lines or a leading
  inline tag would break the block.
* **No Streamlit import.** Components are pure ``(data) -> str`` functions and are unit-tested directly.
* **Colour is never the only signal:** every status also has an icon and a text label (accessibility).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Iterable

from src.models import StepRecord, StepStatus, WorkflowDefinition

from .icons import icon

TONES = ("neutral", "success", "warning", "danger", "info", "accent", "violet")


def e(value: Any) -> str:
    """HTML-escape any value (``None`` becomes an empty string)."""
    return html.escape("" if value is None else str(value), quote=True)


def tone_of(tone: str) -> str:
    return tone if tone in TONES else "neutral"


def trunc(text: Any, limit: int = 140) -> str:
    s = "" if text is None else str(text).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def fmt_value(v: Any) -> str:
    """Business-friendly text for a scalar shown in a KPI card."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v):,}"
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return ""
    if ms < 1:
        return "<1 ms"
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.1f} s"


# ------------------------------------------------------------------ small parts
def chip(text: Any, tone: str = "neutral", *, icon_name: str | None = None, dot: bool = False, mono: bool = False, title: str | None = None) -> str:
    ic = icon(icon_name, 12) if icon_name else ""
    d = '<span class="dot"></span>' if dot else ""
    t = f' title="{e(title)}"' if title else ""
    m = " mono" if mono else ""
    return f'<span class="chip {tone_of(tone)}{m}"{t}>{d}{ic}{e(text)}</span>'


def glass_chip(text: Any, tone: str = "neutral", *, icon_name: str | None = None) -> str:
    """A chip for the dark hero banner (translucent), with a coloured status dot."""
    ic = icon(icon_name, 13) if icon_name else f'<span class="g-dot {tone_of(tone)}"></span>'
    return f'<span class="glass-chip">{ic}{e(text)}</span>'


def chip_row(chips: Iterable[str]) -> str:
    return '<div class="chips">' + "".join(chips) + "</div>"


STEP_STYLE: dict[str, tuple[str, str, str]] = {
    "completed": ("Completed", "success", "check"),
    "skipped": ("Skipped", "neutral", "skip"),
    "failed": ("Failed", "danger", "x"),
    "pending": ("Pending", "neutral", "clock"),
    "running": ("Running", "info", "play"),
    "awaiting_input": ("Waiting for input", "warning", "pause"),
}


def status_chip(status: str) -> str:
    label, tone, ic = STEP_STYLE.get(status, ("Unknown", "neutral", "info"))
    return chip(label, tone, icon_name=ic)


def meter(fraction: float | None, label: str = "") -> str:
    if fraction is None:
        return ""
    pct = max(0, min(100, round(fraction * 100)))
    return (f'<div class="meter-row"><div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct}" '
            f'aria-label="{e(label or "confidence")}"><span style="width:{pct}%"></span></div><b>{pct}%</b></div>')


# ------------------------------------------------------------------- page chrome
def brand() -> str:
    return ('<div class="brand"><div class="brand-mark">' + icon("workflow", 20) + '</div><div><div class="brand-name">Workflow Agent</div>'
            '<div class="brand-sub">Excel-driven automation</div></div></div>')


def hero(*, eyebrow: str, title: str, subtitle: str, chips: Iterable[str], kpis: Iterable[tuple]) -> str:
    """Hero banner. ``kpis`` items are ``(value, label)`` or ``(value, label, icon_name)``."""
    cells = []
    for item in kpis:
        value, label = item[0], item[1]
        ic = f'<span class="kpi-ic">{icon(item[2], 18)}</span>' if len(item) > 2 and item[2] else ""
        cells.append(f'<div class="hero-kpi">{ic}<div><b>{e(fmt_value(value))}</b><span class="k">{e(label)}</span></div></div>')
    return (f'<header class="hero"><span class="orb orb-a"></span><span class="orb orb-b"></span>'
            f'<div class="hero-eyebrow">{icon("sparkle", 14)}{e(eyebrow)}</div>'
            f'<h1 class="hero-title">{e(title)}</h1><p class="hero-sub">{e(subtitle)}</p>'
            f'<div class="hero-chips">{"".join(chips)}</div><div class="hero-kpis">{"".join(cells)}</div></header>')


def section(num: int | str | None, title: str, subtitle: str | None = None, *, icon_name: str | None = None) -> str:
    badge = f'<span class="sec-num">{e(num)}</span>' if num is not None else (f'<span class="sec-ic">{icon(icon_name, 16)}</span>' if icon_name else "")
    sub = f'<div class="sec-sub">{e(subtitle)}</div>' if subtitle else ""
    return f'<div class="sec">{badge}<div><div class="sec-title">{e(title)}</div>{sub}</div></div>'


def callout(tone: str, title: str, text: str | None = None, *, lines: Iterable[str] | None = None, icon_name: str | None = None) -> str:
    tone = tone_of(tone)
    ic = icon_name or {"success": "check", "warning": "alert", "danger": "x", "info": "info"}.get(tone, "info")
    body = f'<div class="co-text">{e(text)}</div>' if text else ""
    if lines:
        body += '<ul class="co-list">' + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ul>"
    return f'<div class="callout {tone}" role="note"><span class="co-ic">{icon(ic, 18)}</span><div><div class="co-title">{e(title)}</div>{body}</div></div>'


def empty_state(title: str, text: str, icon_name: str = "sparkle") -> str:
    return f'<div class="empty"><span class="empty-ic">{icon(icon_name, 22)}</span><div class="empty-title">{e(title)}</div><div class="empty-text">{e(text)}</div></div>'


def footer(text: str) -> str:
    return f'<div class="foot">{e(text)}</div>'


# ---------------------------------------------------------------------- outcome
OUTCOME_ICON = {"success": "check", "warning": "alert", "danger": "x", "info": "pause", "neutral": "info"}


def outcome(*, tone: str, label: str, headline: str, chips: Iterable[str] = (), lines: Iterable[str] = ()) -> str:
    tone = tone_of(tone)
    extra = "".join(f'<div class="o-line">{e(x)}</div>' for x in lines)
    meta = f'<div class="chips o-chips">{"".join(chips)}</div>' if chips else ""
    return (f'<section class="outcome {tone}" aria-live="polite"><div class="o-ic">{icon(OUTCOME_ICON.get(tone, "info"), 26)}</div>'
            f'<div class="o-body"><div class="o-label">{e(label)}</div><div class="o-headline">{e(headline)}</div>{extra}{meta}</div></section>')


def kpi_grid(items: Iterable[dict[str, Any]]) -> str:
    cells = []
    for it in items:
        hint = f'<div class="k-hint">{e(it["hint"])}</div>' if it.get("hint") else ""
        cells.append(f'<div class="kpi {tone_of(it.get("tone", "neutral"))}"><div class="k-label">{e(it["label"])}</div>'
                     f'<div class="k-value">{e(fmt_value(it["value"]))}</div>{hint}</div>')
    return f'<div class="kpis">{"".join(cells)}</div>'


# --------------------------------------------------------------------- pipeline
@dataclass
class Stage:
    key: str
    label: str
    state: str  # idle | done | attention | error
    detail: str


STAGE_ICONS = {"route": "route", "extract": "filter", "validate": "shield", "execute": "cpu", "result": "flag"}
STATE_TEXT = {"idle": "Not started", "done": "Done", "attention": "Needs attention", "error": "Stopped"}


def pipeline(stages: Iterable[Stage]) -> str:
    rows = []
    for s in stages:
        ic = "check" if s.state == "done" else "alert" if s.state == "attention" else "x" if s.state == "error" else STAGE_ICONS.get(s.key, "info")
        rows.append(
            f'<li class="pipe-row {s.state}"><span class="pipe-node">{icon(ic, 18)}</span>'
            f'<div><div class="pipe-title">{e(s.label)}<span class="sr">: {e(STATE_TEXT[s.state])}</span></div>'
            f'<div class="pipe-detail">{e(s.detail)}</div></div></li>')
    return f'<ol class="pipe" aria-label="Agent pipeline">{"".join(rows)}</ol>'


# --------------------------------------------------------------------- timeline
def timeline(steps: Iterable[StepRecord]) -> str:
    items = []
    for s in steps:
        status = s.status.value if isinstance(s.status, StepStatus) else str(s.status)
        label, tone, ic = STEP_STYLE.get(status, STEP_STYLE["pending"])
        chips = [chip("Decision", "info", icon_name="branch")] if s.kind == "decision" else [chip(s.tool, "accent", mono=True)] if s.tool else []
        if s.simulated:
            chips.append(chip("SIMULATED", "violet", icon_name="database", title="Fixture data, not a live system"))
        if s.attempts:
            chips.append(chip(f"retried ×{len(s.attempts)}", "warning", icon_name="retry"))
        if s.on_failure == "continue" and s.kind == "tool":
            chips.append(chip("optional step", "neutral", title="A failure here does not stop the workflow"))
        if s.error:
            msg, msg_cls = s.error.message, "danger"
        elif s.note:
            msg, msg_cls = s.note, ""
        elif s.skipped_reason:
            msg, msg_cls = s.skipped_reason, ""
        else:
            msg, msg_cls = s.output_summary, ""
        dur = fmt_ms(s.duration_ms) if status not in ("pending", "skipped") else ""
        items.append(
            f'<li class="tl-item {status}"><span class="tl-dot" title="{e(label)}">{icon(ic, 15)}</span>'
            f'<div class="tl-card"><div class="tl-head"><span class="tl-name">{e(s.name)}</span>{"".join(chips)}'
            f'<span class="tl-meta"><span class="sr">{e(label)}. </span>{e(dur)}</span></div>'
            + (f'<div class="tl-msg {msg_cls}">{e(trunc(msg, 170))}</div>' if msg else "")
            + "</div></li>")
    return f'<div class="tl-wrap"><ol class="tl" aria-label="Execution timeline">{"".join(items)}</ol></div>'


# ------------------------------------------------------------- selection panel
SOURCE_TONE = {"request": "info", "attachment": "accent", "default": "neutral", "reply": "violet"}


def input_rows(rows: Iterable[dict[str, Any]]) -> str:
    out = []
    for r in rows:
        out.append(f'<div class="kv"><span class="kv-k">{e(str(r["name"]).replace("_", " "))}</span>'
                   f'<span class="kv-v">{e(trunc(r["value"], 90))}</span>{chip(r.get("source", ""), SOURCE_TONE.get(r.get("source", ""), "neutral"))}</div>')
    return f'<div class="kvs">{"".join(out)}</div>' if out else '<div class="muted">No input values yet.</div>'


def workflow_title(name: str, wid: str, chips: Iterable[str] = ()) -> str:
    return f'<div class="wf-title"><span class="wf-id">{e(wid)}</span><span class="wf-name-lg">{e(name)}</span>{"".join(chips)}</div>'


def quote(text: str) -> str:
    return f'<blockquote class="why">{e(text)}</blockquote>'


# ----------------------------------------------------------------- catalog cards
def workflow_icon(d: WorkflowDefinition, tool_kinds: dict[str, str] | None = None) -> str:
    """An icon hinting at what a workflow does, derived from the kinds of tools it uses (no workflow IDs)."""
    kinds = {(tool_kinds or {}).get(s.tool or "", "") for s in d.steps}
    if "llm" in kinds:
        return "sparkle"
    if "simulated_api" in kinds:
        return "database"
    if "file_output" in kinds or "local_data" in kinds:
        return "file"
    return "workflow"


def workflow_card(d: WorkflowDefinition, *, selected: bool = False, simulated_tools: frozenset[str] = frozenset(),
                  tool_kinds: dict[str, str] | None = None) -> str:
    uses_sim = bool(simulated_tools & {s.tool for s in d.steps if s.tool})
    chips = [chip(f"{len(d.excel_steps)} steps", "neutral", icon_name="layers"), chip(f"{len(d.conditions)} rules", "neutral", icon_name="branch")]
    if uses_sim:
        chips.append(chip("simulated data", "violet", icon_name="database"))
    if d.is_extension:
        chips.append(chip("extension", "warning"))
    req = ", ".join(i.name.replace("_", " ") for i in d.required_inputs) or "none required"
    sel = " selected" if selected else ""
    return (f'<article class="wf-card{sel}"><div class="wf-top"><span class="wf-lead"><span class="wf-ic">{icon(workflow_icon(d, tool_kinds), 16)}</span><span class="wf-id">{e(d.id)}</span></span>'
            f'<span class="wf-tools">{icon("tool", 13)}{e(", ".join(d.tools_required))}</span></div>'
            f'<div class="wf-name">{e(d.name)}</div><div class="wf-trigger">{e(d.triggers[0] if d.triggers else "")}</div>'
            f'<div class="wf-req"><b>Needs:</b> {e(trunc(req, 70))}</div><div class="chips">{"".join(chips)}</div></article>')


def spec_list(items: Iterable[str], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    return f'<{tag} class="speclist">' + "".join(f"<li>{e(x)}</li>" for x in items) + f"</{tag}>"


def excel_card(rows: Iterable[tuple[str, str]]) -> str:
    cells = "".join(f'<div class="ex-row"><div class="ex-k">{e(k)}</div><div class="ex-v">{e(v)}</div></div>' for k, v in rows)
    return f'<div class="excel">{cells}</div>'


def code_chip(text: str) -> str:
    return f'<code class="expr">{e(text)}</code>'


def subhead(title: str, meta: str | None = None) -> str:
    m = f'<span class="subhead-meta">{e(meta)}</span>' if meta else ""
    return f'<div class="subhead"><span class="subhead-title">{e(title)}</span>{m}</div>'


def how_it_works(steps: Iterable[tuple[str, str, str]]) -> str:
    """Three-or-so explainer cards: ``(icon_name, title, text)`` each."""
    cards = "".join(
        f'<div class="how-card"><span class="how-n">{n}</span><span class="how-ic">{icon(ic, 20)}</span>'
        f'<div class="how-t">{e(t)}</div><div class="how-d">{e(d)}</div></div>'
        for n, (ic, t, d) in enumerate(steps, start=1))
    return f'<div class="how">{cards}</div>'


def palette_strip(name: str, colors: tuple[str, str, str], tagline: str) -> str:
    """A gradient preview of the selected accent palette for the sidebar picker."""
    c1, c2, c3 = colors
    return (f'<div class="pstrip" role="img" aria-label="{e(name)} palette preview"><span class="ps-bar" '
            f'style="background:linear-gradient(90deg,{e(c1)},{e(c2)},{e(c3)})"></span><span class="ps-tag">{e(tagline)}</span></div>')
