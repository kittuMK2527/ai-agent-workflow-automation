"""Architecture guarantees: one generic engine, data-only extensibility (the 11th workflow),
tool permissions, and honest labelling of simulated systems."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from src import execution_engine, router
from src.agent import Agent
from src.config import PROJECT_ROOT, REQUIRED_WORKFLOW_IDS
from src.execution_engine import ExecutionEngine
from src.models import ExecutionStatus
from tests.conftest import EXTENSION_DIR

SRC = PROJECT_ROOT / "src"


# ------------------------------------------------- no workflow-specific code
def test_source_code_contains_no_workflow_specific_logic():
    """The ten workflows are data. Python code must not mention any workflow ID or name."""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for m in re.finditer(r"\bWF\d{3}\b", text):
            offenders.append(f"{path.relative_to(SRC)}: {m.group()}")
    assert not offenders, offenders


def test_engine_has_one_generic_run_method_and_no_per_workflow_functions():
    public = [n for n, _ in inspect.getmembers(ExecutionEngine, inspect.isfunction) if not n.startswith("_")]
    assert public == ["run"]
    names = " ".join(n.lower() for n, _ in inspect.getmembers(execution_engine) + inspect.getmembers(router))
    for word in ("restock", "price_valid", "vendor_file", "campaign", "keyword", "duplicate", "performance"):
        assert word not in names


def test_all_ten_workflows_run_through_the_same_engine_instance(agent):
    engine = agent.engine
    seen = set()
    for wid in REQUIRED_WORKFLOW_IDS:
        d = agent.registry.get(wid)
        sample = next(e for e in d.examples if e.expect_state == "executed")
        atts = [__import__("src.models", fromlist=["Attachment"]).Attachment(name=a, path=a) for a in sample.attachments]
        r = agent.handle(sample.request, attachments=atts)
        assert r.state == "executed" and r.record.workflow_id == wid
        seen.add(id(engine))
    assert len(seen) == 1


def test_engine_only_executes_tools_from_the_registry(agent):
    for d in agent.registry.all():
        for s in d.steps:
            if s.kind == "tool":
                assert agent.tools.has(s.tool)
                assert s.tool in d.allowed_tools or agent.tools.get(s.tool).core, (d.id, s.tool)


# --------------------------------------------------- the 11th workflow
def agent_with(settings, tools, *dirs):
    s = settings.with_overrides(extra_catalog_dirs=tuple(dirs))
    return Agent(s, tools=tools, llm=None, audit=False, sleep=lambda x: None)


def test_the_demo_extension_runs_through_the_same_agent_and_engine(settings, tools):
    a = agent_with(settings, tools, EXTENSION_DIR)
    assert a.registry.report.ok and "WF011" in a.registry.ids() and len(a.registry.ids()) == 11
    r = a.handle("What is the total value of our inventory by category?")
    assert r.routing.workflow_id == "WF011" and r.state == "executed" and r.record.status == ExecutionStatus.COMPLETED
    inv = pd.read_csv(PROJECT_ROOT / "data" / "fixtures" / "inventory.csv")
    inv["v"] = inv["current_stock"] * inv["unit_cost"]
    expected = inv.groupby("category")["v"].sum().round(2).sort_values(ascending=False)
    got = {row["category"]: row["total_value"] for row in r.record.final_result.data["by_category"]}
    assert got == pytest.approx(expected.to_dict(), abs=0.01)
    assert r.record.final_result.data["total_value"] == pytest.approx(inv["v"].sum(), abs=0.01)
    assert all(row["flagged"] is False for row in r.record.final_result.data["by_category"])  # none above 40%
    assert {s.tool for s in r.record.steps} <= set(a.tools.names())


def test_extension_threshold_rule_flags_concentrated_categories(settings, tools):
    a = agent_with(settings, tools, EXTENSION_DIR)
    r = a.run_workflow("WF011", {"share_limit_pct": 20})
    flagged = {row["category"] for row in r.record.final_result.data["by_category"] if row["flagged"]}
    assert flagged == {"Accessories", "Footwear"}  # 26.4% and 21.9% are above 20%; the rest are not


def test_the_original_ten_are_unchanged_by_the_extension(settings, tools):
    base = Agent(settings, tools=tools, llm=None, audit=False)
    ext = agent_with(settings, tools, EXTENSION_DIR)
    for wid in REQUIRED_WORKFLOW_IDS:
        assert base.registry.get(wid).model_dump() == ext.registry.get(wid).model_dump()
    assert base.registry.report.workbook_sha256 == ext.registry.report.workbook_sha256
    assert all(not ext.registry.get(w).is_extension for w in REQUIRED_WORKFLOW_IDS)
    assert base.handle("What is the total value of our inventory by category?").routing.workflow_id != "WF011"


def test_extension_does_not_steal_the_requests_of_existing_workflows(settings, tools):
    ext = agent_with(settings, tools, EXTENSION_DIR)
    assert ext.handle("Which products need restocking?").routing.workflow_id == "WF001"


def make_new_workflow(tmp_path: Path, settings, *, tool="table_query", extra_step="") -> Path:
    """Build a brand-new extension (one workbook row + one spec) using ONLY data files."""
    ext = tmp_path / "ext12"
    (ext / "specs").mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Workflows"
    ws.append(["Workflow_ID", "Workflow_Name", "Trigger", "Inputs", "Steps", "Decision_Logic", "Tools_Required", "Expected_Output"])
    ws.append(["WF012", "Budget Product Finder", "User asks for cheap products", "Product CSV", "Load products → filter budget products",
               "A product is budget when its price is below the limit", "CSV reader; calculator", "List of budget products"])
    wb.save(ext / "twelve.xlsx")
    (ext / "specs" / "WF012.yaml").write_text(f"""
workflow_id: WF012
routing_keywords: [budget products, cheap products]
inputs:
  - {{name: products_file, type: file, default: products.csv, description: Product CSV, covers: ["Product CSV"]}}
  - {{name: limit, type: number, default: 15, description: Price limit}}
tool_map:
  "CSV reader": [csv_reader]
  "calculator": [table_query]
steps:
  - id: load
    name: Load products
    excel_step: Load products
    tool: csv_reader
    params: {{source: "=inputs.products_file", numeric_columns: [price]}}
  - id: pick
    name: Filter budget products
    excel_step: filter budget products
    tool: {tool}
    params:
      rows: "=steps.load.rows"
      vars: {{limit: "=inputs.limit"}}
      where: "row.price < vars.limit"
{extra_step}
final:
  outcomes:
    completed:
      headline: "{{{{ steps.pick.count }}}} budget products"
      result: {{products: "=steps.pick.rows"}}
""")
    return ext


def test_a_twelfth_workflow_needs_only_a_workbook_row_and_a_spec(tmp_path, settings, tools):
    ext = make_new_workflow(tmp_path, settings)
    a = agent_with(settings, tools, ext)
    assert a.registry.report.ok and "WF012" in a.registry.ids()
    r = a.run_workflow("WF012", {"limit": 10})
    assert r.state == "executed" and r.message == "3 budget products"
    assert {p["sku"] for p in r.record.final_result.data["products"]} == {"SKU-1009", "SKU-1012", "SKU-1018"}  # 8.00, 6.99, 9.99 (SKU-1099 at exactly 10.00 is not below the limit)
    r2 = a.handle("show me the cheap products")
    assert r2.routing.workflow_id == "WF012" and r2.state == "executed"


def test_a_new_workflow_with_an_unsupported_tool_fails_validation_without_harming_the_rest(tmp_path, settings, tools):
    ext = make_new_workflow(tmp_path, settings, tool="teleporter")
    a = agent_with(settings, tools, ext)
    assert "WF012" not in a.registry.ids() and a.registry.report.ok  # the ten are still healthy
    assert any("teleporter" in i.message and i.workflow_id == "WF012" for i in a.registry.report.errors)
    assert a.registry.report.invalid_ids == ["WF012"]


def test_a_new_workflow_cannot_use_a_tool_outside_its_excel_mapping(tmp_path, settings, tools):
    extra = "  - id: sneaky\n    name: Sneaky\n    excel_step: filter budget products\n    tool: order_search\n    params: {kind: email, value: a@b.co}\n"
    a = agent_with(settings, tools, make_new_workflow(tmp_path, settings, extra_step=extra))
    assert any("not permitted for this workflow" in i.message for i in a.registry.report.errors)


# ------------------------------------------------- simulated != real
FORBIDDEN_REAL_ACTIONS = ("email", "mail", "sms", "payment", "pay", "charge", "refund", "notify", "webhook", "post", "upload", "delete", "transfer")


def test_no_tool_can_perform_a_real_world_side_effect(tools):
    for spec in tools.specs():
        assert not any(w in spec.name.lower().split("_") for w in FORBIDDEN_REAL_ACTIONS), spec.name
        assert spec.kind in ("local_data", "simulated_api", "computation", "llm", "file_output")
    effectful = [s.name for s in tools.specs() if s.side_effect]
    assert effectful == ["dataset_exporter"]  # the only side effect writes a local CSV into outputs/


def test_every_simulated_tool_result_says_so(agent):
    ctx_results = {}
    for wid, inputs in (("WF005", {"identifier": "ORD-1001"}), ("WF009", {"task_description": "Fix the React frontend"})):
        rec = agent.run_workflow(wid, inputs).record
        for s in rec.steps:
            if s.simulated:
                assert s.output_preview["simulated"] is True and "SIMULATED" in s.output_preview["note"], s.step_id
                ctx_results[s.tool] = True
    assert set(ctx_results) == {"order_search", "order_details", "shipment_lookup", "employee_db"}


def test_llm_free_runs_never_claim_to_have_used_an_llm(agent):
    rec = agent.run_workflow("WF004", {"product_name": "Mug"}).record
    assert rec.llm_used_in_tools is False and rec.final_result.data["generation_modes"] == ["template"]
    assert all(s.output_preview["mode"] == "template" for s in rec.steps if s.tool == "content_generator")


def test_fault_injection_is_off_by_default(agent):
    assert agent.faults.pending() == 0
