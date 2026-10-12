"""Generate docs/workflow_analysis.md from the workbook + specs (so it cannot drift from the code).

    python scripts/generate_workflow_analysis.py

The per-workflow sections are produced from the validated catalog (Excel text verbatim, and the
executable interpretation). The cross-cutting analysis at the top and bottom is written here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import REQUIRED_WORKFLOW_IDS, Settings  # noqa: E402
from src.tools import build_default_registry  # noqa: E402
from src.workflow_registry import WorkflowRegistry  # noqa: E402

INTRO = """# Workflow analysis

This document analyses the ten business workflows in the assignment workbook
(`AI_Agent_Workflow_Assessment (1).xlsx`, preserved unchanged as `data/workflows.xlsx`) and records how each one
was turned into an executable definition. The per-workflow sections are **generated from the same catalog the
engine runs** (`python scripts/generate_workflow_analysis.py`), so they cannot disagree with the code.

## 1. What is in the workbook

| Sheet | Role | Content |
|---|---|---|
| `Workflows` | workflow definitions | 10 rows (WF001-WF010) × 8 columns: `Workflow_ID`, `Workflow_Name`, `Trigger`, `Inputs`, `Steps`, `Decision_Logic`, `Tools_Required`, `Expected_Output` |
| `Test_Questions` | sample requests | 10 rows: `Workflow_ID`, `Test_Request`, `What_To_Check` |

There are no merged cells, hidden sheets, tables, formulas, comments or defined names. Every business definition is
**free text inside a cell**:

* `Steps` is one cell of steps separated by `→` (__TOTAL_STEPS__ steps in total across the ten workflows).
* `Inputs` and `Tools_Required` are lists separated by `;`.
* `Decision_Logic` is a sentence of prose, not a formal rule.
* There is **no column for failure conditions or recovery behaviour**, and tool names are generic phrases
  ("calculator", "LLM", "CSV reader") rather than identifiers.

The loader (`src/excel_loader.py`) therefore finds sheets and header rows by content, tolerates merged cells,
multi-row workflows, line-break or numbered steps and unknown columns, and records the sheet and row of every workflow.
The same code handles the assignment workbook and the extension workbook in `examples/extension_wf011/`.

## 2. From free text to an executable definition

The workbook says **what** a workflow does; it cannot say **how** a machine should do it. Each workflow therefore has
a second file, `data/specs/<ID>.yaml` (the *execution spec*), that adds only what the Excel cannot express:

| Excel gives | The spec adds |
|---|---|
| `Inputs` phrases | typed inputs (type, required, default, limits) with `covers:` pointing back at the Excel phrase |
| `Steps` phrases | executable steps (tool + parameters) with `excel_step:` naming the Excel step each one implements |
| `Decision_Logic` prose | safe expressions (`row.current_stock < row.threshold`) used by filters, guards and branches |
| `Tools_Required` phrases | `tool_map:` from each phrase to registered tools |
| `Expected_Output` | the structured final result and headline |

**The Excel stays authoritative.** At load time the registry refuses a workflow if the spec's `excel_step` sequence does not
equal the workbook's Steps cell, if an Excel input or tool is not accounted for, or if the spec claims to cover something the
workbook does not say. Editing a step in the workbook without updating the spec is therefore a loud, specific error.

Input coverage, tool mapping and the other checks are listed in `docs/validation_report.md`.

## 3. Summary

| ID | Workflow | Trigger (Excel) | Excel steps | Executable steps | Excel tools | Conditions |
|---|---|---|---|---|---|---|
"""

CROSS_CUTTING = """
## 5. Ambiguities, gaps and contradictions in the workbook

These are the places where the workbook is silent or vague and a decision had to be made. Every decision is also stored in
the workflow's `interpretation_notes` (shown in the app's catalog view). Nothing below was decided silently, and no decision
changes what the Excel *does* say.

| # | Where | What the Excel says / omits | Decision taken |
|---|---|---|---|
| 1 | WF001 | "calculate reorder quantity" - no formula | target stock = minimum × `restock_multiplier` (default 2.0); reorder = ceil(target − current). The multiplier is a visible, overridable input |
| 2 | WF001 | "minimum stock threshold" - per product or one global number? | Per-product `minimum_stock` column by default; a threshold in the request overrides it for every product |
| 3 | WF002 | "percentage difference" - relative to which price? | Relative to the **internal** price; signed; rounded to 2 decimals before comparing so float noise cannot flip a result |
| 4 | WF002 | "exceeds 10%" - strict? absolute? | Strictly greater than, on the absolute value (vendor cheaper counts too). Exactly 10.00% is **not** flagged |
| 5 | WF003 | which header names count as SKU / product name? | Synonym lists in the spec; "description" is deliberately not accepted as a product name |
| 6 | WF003 | "cleaned file" - what is cleaned? | Canonical column names, trimmed whitespace, invalid rows removed. Values are not rewritten. A real CSV is written to `outputs/` |
| 7 | WF004 | "required attributes" - which of the six inputs are required? | Only the product name blocks. The other five are *expected*: if missing they are listed under `missing_information`, left out of the copy, and a warning is shown (the Excel rule "do not invent … explicitly mark missing information") |
| 8 | WF004 | "text validation" - what to validate? | Length limits, product name in the SEO title, and no colour/material words that are not in the supplied facts |
| 9 | WF005 | identifier is "Order ID or customer email" - an email can match several orders | All matching orders are returned |
| 10 | WF005 | "If no order is found, ask for another identifier" | The workflow pauses in an *awaiting input* state and the conversation continues with the new identifier |
| 11 | WF006 | "high attribute similarity" is undefined | Weighted similarity ≥ 0.85 (product name 0.7, category 0.15, material 0.15); products that differ in brand, colour or size are variants, not duplicates |
| 12 | WF006 | "assign confidence" | definite = 1.0; possible = lowest similarity in the group, labelled *high* ≥ 0.95 else *medium* |
| 13 | WF007 | "dates" | A start and an end date (ISO). The Excel rule requires the goal and dates; the product list is also required because "summarize products" cannot run without it |
| 14 | WF008 | "high-priority keywords" is undefined | high = transactional/commercial intent AND mapped to a category AND (volume ≥ threshold, or no volume supplied) |
| 15 | WF008 | "LLM/classifier" | A transparent rule lexicon, not an LLM, so results are reproducible; keywords with no signal words are classified *commercial* with **low** confidence and flagged |
| 16 | WF009 | "employee list; skills; workload" are listed as *inputs* | They are data held in the (simulated) employee/task database, so they are modelled as a data source, not user inputs |
| 17 | WF009 | "available capacity", "suitable employee" | Suitable = all required skills, active, and ≥ `min_available_hours` (default 4) free this week. Score = 100 × (0.6 × skill match + 0.4 × free-capacity ratio) |
| 18 | WF010 | "defined threshold" for execution time - not defined | `slow_run_threshold_sec` default 30 s and `slow_step_threshold_sec` default 5 s, both overridable. The 10 % failure threshold is from the Excel |
| 19 | all | no failure / recovery behaviour specified | Engine defaults: fail fast, keep partial results, retry only transient tool errors (3 attempts); individual steps may opt into *continue* (WF005 shipment lookup) |
| 20 | all | tool names are generic phrases | Each phrase maps to registered tools in `tool_map`; generic table tools back "calculator"/"ranking logic" |
| 21 | tests | the `Test_Questions` are terse ("Assign this urgent task…") and often carry no data | The correct behaviour is to **ask** (WF003, WF004, WF007, WF008, WF009) or to use the named system data source (WF001, WF002, WF005, WF006, WF010). Complete versions of each request are provided as examples |

No contradictions between rows were found. The two places that *look* contradictory are not: WF003 and WF008 are triggered by a
user-supplied file whereas WF001/WF002/WF006/WF010 read named system data, which is why the former ask for a file when none is attached.

## 6. Validation result

See `docs/validation_report.md` (generated by `python scripts/validate_workflows.py --write`): all 10 workflows were extracted,
registered and statically validated with zero errors or warnings.
"""


def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def build_document(reg: WorkflowRegistry) -> str:
    assert reg.report.ok, "catalog must be healthy to generate the analysis"
    defs = [reg.get(w) for w in REQUIRED_WORKFLOW_IDS]

    out = [INTRO.replace("__TOTAL_STEPS__", str(sum(len(d.excel_steps) for d in defs)))]
    for d in defs:
        out.append(f"| {d.id} | {d.name} | {md_escape(d.triggers[0])} | {len(d.excel_steps)} | {len(d.steps)} | {md_escape('; '.join(d.tools_required))} | {len(d.conditions)} |")
    out.append("\n## 4. Per-workflow analysis\n")
    for d in defs:
        out += [f"### {d.id} · {d.name}", "",
                f"*Source:* `{d.source}`", "",
                "**Excel definition (verbatim)**", "",
                "| Column | Text |", "|---|---|",
                f"| Trigger | {md_escape(d.triggers[0])} |",
                f"| Inputs | {md_escape('; '.join(d.excel_inputs))} |",
                f"| Steps | {md_escape(' → '.join(d.excel_steps))} |",
                f"| Decision_Logic | {md_escape(d.decision_logic)} |",
                f"| Tools_Required | {md_escape('; '.join(d.tools_required))} |",
                f"| Expected_Output | {md_escape(d.success_output)} |",
                ""]
        if d.test_requests:
            out += ["*Excel test question:* " + "; ".join(f"“{t.request}” ({t.what_to_check})" for t in d.test_requests), ""]

        out += ["**Inputs (executable)**", "", "| Name | Type | Required | Default | Covers (Excel) | Meaning |", "|---|---|---|---|---|---|"]
        for i in d.inputs:
            out.append(f"| `{i.name}` | {i.type.value} | {'yes' if i.required else 'no'} | {md_escape('' if i.default is None else i.default)} | "
                       f"{md_escape(', '.join(i.covers))} | {md_escape(i.description)} |")
        for g in d.input_groups:
            out.append(f"\n*At least one of* `{'`, `'.join(g.any_of)}` *is required.*")
        for s in d.data_sources:
            out.append(f"\n*Data source:* {s.name}: {s.description} (covers: {', '.join(s.covers)})")
        out.append("")

        out += ["**Steps: Excel step → executable steps**", "", "| Excel step | Executable step | Tool | Notes |", "|---|---|---|---|"]
        for s in d.steps:
            tool = f"`{s.tool}`" if s.tool else "decision"
            notes = []
            if s.when:
                notes.append(f"runs only if `{s.when}`")
            if s.kind == "decision":
                notes += [f"{c.label}: `{c.when or 'otherwise'}` → {c.then.type}" for c in s.cases]
            if s.on_failure == "continue":
                notes.append("failure does not stop the workflow")
            for e in s.ensure:
                notes.append(f"must satisfy `{e.expr}`")
            out.append(f"| {md_escape(s.excel_step)} | `{s.id}` | {tool} | {md_escape('; '.join(notes))} |")
        out.append("")

        out += ["**Tool mapping**", ""] + [f"- “{k}” → {', '.join(f'`{t}`' for t in v)}" for k, v in d.tool_map.items()] + [""]

        out += ["**Decision logic as executable conditions**", ""]
        for c in d.conditions:
            if c.kind in ("row_rule", "guard", "decision_case"):
                out.append(f"- *{md_escape(c.label)}* (`{c.step_id}`): `{c.expression or 'otherwise'}`" + (f" → {c.action}" if c.action else ""))
        out += ["", "**Success output**", "",
                "Outcomes: " + ", ".join(f"`{k}`" for k in d.final.outcomes) + ". Result fields: " + ", ".join(f"`{k}`" for k in d.final.outcomes["completed"].result), "",
                "**Failure and recovery**", ""] + [f"- {n}" for n in d.error_handling if not n.startswith("Default failure policy") and not n.startswith("Transient tool errors") and not n.startswith("Steps marked")]
        out += ["- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried."]
        out += ["", "**Interpretations (where the Excel is silent or vague)**", ""] + [f"- {n}" for n in d.interpretation_notes] + [""]
    out.append(CROSS_CUTTING)
    return "\n".join(out)


def main() -> None:
    settings = Settings.from_env(load_dotenv_file=False)
    reg = WorkflowRegistry(settings, build_default_registry())
    reg.load()
    path = ROOT / "docs" / "workflow_analysis.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(build_document(reg), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
