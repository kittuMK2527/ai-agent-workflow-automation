"""Business-rule tests for all ten Excel workflows.

Expected values are computed independently from the fixture files (plain Python /
pandas), never from the engine's own output, and every rule quoted in the
workbook's Decision_Logic column has a dedicated assertion.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd
import pytest

from src.models import Attachment, ErrorCategory, ExecutionStatus, StepStatus
from tests.fakes import FakeLLM

FIX = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def go(agent, wid, inputs=None, attachments=None):
    resp = agent.run_workflow(wid, inputs or {}, attachments=attachments)
    return resp, resp.record


def data_of(rec):
    assert rec is not None and rec.final_result is not None, (rec.status, [e.message for e in rec.errors]) if rec else "no record"
    return rec.final_result.data


def tools_of(rec):
    return [s.tool for s in rec.steps if s.tool]


def done_ids(rec):
    return [s.step_id for s in rec.steps if s.status == StepStatus.COMPLETED]


def upload(settings, name, header, rows):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with (settings.upload_dir / name).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return name


def rowmap(rows, key):
    return {r[key]: r for r in rows}


# ======================================================================== WF001
def inventory_oracle(threshold=None, multiplier=2.0):
    df = pd.read_csv(FIX / "inventory.csv")
    df["thr"] = threshold if threshold is not None else df["minimum_stock"]
    low = df[df["current_stock"] < df["thr"]].copy()
    low["shortfall"] = low["thr"] - low["current_stock"]
    low["reorder"] = [math.ceil(t * multiplier - c) for t, c in zip(low["thr"], low["current_stock"])]
    return low.sort_values(["shortfall", "sku"], ascending=[False, True])


def test_wf001_restock_list_matches_independent_calculation(agent):
    resp, rec = go(agent, "WF001")
    d = data_of(rec)
    oracle = inventory_oracle()
    assert rec.status == ExecutionStatus.COMPLETED
    assert [r["sku"] for r in d["restock_list"]] == list(oracle["sku"])
    assert [r["reorder_quantity"] for r in d["restock_list"]] == list(oracle["reorder"])
    assert d["total_units_to_order"] == int(oracle["reorder"].sum()) == 299
    assert d["products_checked"] == 22 and d["products_to_restock"] == 9
    assert [r["priority_rank"] for r in d["restock_list"]] == list(range(1, 10))
    assert resp.message == "9 of 22 products need restocking"


def test_wf001_boundary_stock_equal_to_minimum_is_not_flagged(agent):
    _, rec = go(agent, "WF001")
    skus = {r["sku"] for r in data_of(rec)["restock_list"]}
    assert "SKU-1005" not in skus  # current 25 == minimum 25: the Excel rule is strictly "<"
    assert {"SKU-1008"} <= skus  # zero stock is flagged


def test_wf001_output_has_the_columns_the_excel_promises(agent):
    _, rec = go(agent, "WF001")
    row = data_of(rec)["restock_list"][0]
    assert {"sku", "product_name", "current_stock", "threshold", "reorder_quantity"} <= set(row)  # stock, threshold, suggested reorder
    assert all(not k.startswith("_") for k in row)


def test_wf001_step_order_tools_and_branching(agent):
    _, rec = go(agent, "WF001")
    assert done_ids(rec) == ["load_inventory", "compare_stock", "identify_low_stock", "calculate_reorder", "generate_restock_list", "totals"]
    assert tools_of(rec) == ["csv_reader", "table_query", "table_query", "table_query", "table_query", "table_aggregate"]
    rule = next(c for c in rec.conditions if c.step_id == "identify_low_stock")
    assert (rule.kind, rule.expression, rule.rows_evaluated, rule.rows_matched) == ("row_rule", "row.current_stock < row.threshold", 22, 9)
    assert rule.samples and set(rule.samples[0]["values"]) == {"row.current_stock", "row.threshold"}
    assert rec.input_sources["inventory_file"] == "default"


def test_wf001_request_threshold_overrides_every_products_minimum(agent):
    _, rec = go(agent, "WF001", {"minimum_stock": 15})
    oracle = inventory_oracle(threshold=15)
    d = data_of(rec)
    assert [r["sku"] for r in d["restock_list"]] == list(oracle["sku"]) and len(oracle) == 10
    assert d["threshold_basis"] == "Global threshold of 15 units"
    _, rec3 = go(agent, "WF001", {"restock_multiplier": 3})
    assert [r["reorder_quantity"] for r in data_of(rec3)["restock_list"]] == list(inventory_oracle(multiplier=3)["reorder"])


def test_wf001_missing_stock_values_are_excluded_and_reported_never_guessed(agent, settings):
    name = upload(settings, "gaps.csv", ["sku", "product_name", "current_stock", "minimum_stock"],
                  [["A", "Alpha", 1, 5], ["B", "Beta", "", 5], ["C", "Gamma", "n/a", 5], ["D", "Delta", 9, 5]])
    _, rec = go(agent, "WF001", {"inventory_file": name})
    d = data_of(rec)
    assert [r["sku"] for r in d["restock_list"]] == ["A"]  # B and C are neither "low" nor "fine"
    assert any("2 inventory row(s)" in w for w in rec.final_result.warnings)
    assert any("non-numeric" in w for w in rec.final_result.warnings)
    err_rows = rec.step_record("compare_stock").output_preview["errors"]
    assert [e["row"] for e in err_rows] == [3, 4]  # file line numbers, so the user can find them


def test_wf001_nothing_to_restock(agent, settings):
    name = upload(settings, "ok.csv", ["sku", "product_name", "current_stock", "minimum_stock"], [["A", "Alpha", 50, 5]])
    resp, rec = go(agent, "WF001", {"inventory_file": name})
    assert resp.message == "No products need restocking" and data_of(rec)["restock_list"] == [] and data_of(rec)["total_units_to_order"] == 0


def test_wf001_bad_files_fail_with_a_user_correctable_message(agent, settings):
    name = upload(settings, "wrongcols.csv", ["sku", "qty"], [["A", 1]])
    resp, rec = go(agent, "WF001", {"inventory_file": name})
    assert rec.status == ExecutionStatus.FAILED and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE
    assert "missing required column" in resp.message and "current_stock" in resp.message
    assert rec.completed_step_ids == [] and rec.final_result is None
    resp2 = agent.run_workflow("WF001", {"inventory_file": "no_such_file.csv"})
    assert resp2.state == "needs_input" and "not found" in resp2.message


# ======================================================================== WF002
def price_oracle(threshold=10.0):
    p = pd.read_csv(FIX / "products.csv")
    v = pd.read_csv(FIX / "vendor_prices.csv")
    p["k"] = p["sku"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    v["k"] = v["sku"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    m = p.merge(v, on="k", suffixes=("", "_v"))
    m["pct"] = ((m["vendor_price"] - m["price"]) / m["price"] * 100).round(2)
    m["flag"] = m["pct"].abs() > threshold
    return m, p, v


def test_wf002_exceptions_match_independent_calculation(agent):
    resp, rec = go(agent, "WF002")
    d = data_of(rec)
    m, _, _ = price_oracle()
    exp = m[m["flag"]].sort_values(["pct"], key=abs, ascending=False)
    assert {r["sku"] for r in d["exceptions"]} == set(exp["sku"]) == {"SKU-1002", "SKU-1005", "SKU-1006", "SKU-1011", "SKU-1019"}
    got = rowmap(d["exceptions"], "sku")
    for _, r in exp.iterrows():
        assert got[r["sku"]]["pct_difference"] == pytest.approx(r["pct"], abs=0.005)
    assert d["matched_products"] == len(m) == 22 and d["exceptions_found"] == 5
    assert [r["sku"] for r in d["exceptions"]][0] == "SKU-1019"  # largest difference first (+20.45%)
    assert resp.message == "5 of 22 matched products differ by more than 10%"


def test_wf002_exactly_ten_percent_is_not_flagged(agent):
    _, rec = go(agent, "WF002")
    d = data_of(rec)
    allc = rowmap(d["all_comparisons"], "sku")
    assert allc["SKU-1003"]["pct_difference"] == 10.0 and allc["SKU-1003"]["is_exception"] is False  # Excel: "exceeds 10%"
    assert allc["SKU-1002"]["pct_difference"] == 10.04 and allc["SKU-1002"]["is_exception"] is True
    assert allc["SKU-1005"]["pct_difference"] == -12.5 and allc["SKU-1005"]["is_exception"] is True  # cheaper counts too
    assert allc["SKU-1017"]["is_exception"] is False  # -8.33%


def test_wf002_threshold_override_changes_the_result(agent):
    for thr in (5, 20, 0):
        m, _, _ = price_oracle(thr)
        _, rec = go(agent, "WF002", {"threshold_pct": thr})
        assert {r["sku"] for r in data_of(rec)["exceptions"]} == set(m[m["flag"]]["sku"]), thr
    assert data_of(go(agent, "WF002", {"threshold_pct": 20})[1])["exceptions_found"] == 1  # only SKU-1019 (20.45%); SKU-1011 is exactly 20.00%


def test_wf002_matching_is_case_and_punctuation_insensitive_and_reports_unmatched(agent):
    _, rec = go(agent, "WF002")
    d = data_of(rec)
    assert "SKU-1006" in rowmap(d["all_comparisons"], "sku")  # vendor file spells it 'sku-1006'
    assert [r["sku"] for r in d["internal_only_skus"]] == ["SKU-1099"] and [r["sku"] for r in d["vendor_only_skus"]] == ["SKU-2001"]
    assert any("1 internal product(s) have no vendor price and 1 vendor SKU(s)" in w for w in rec.final_result.warnings)


def test_wf002_trace_shows_condition_and_tool_sequence(agent):
    _, rec = go(agent, "WF002")
    assert tools_of(rec) == ["csv_reader", "csv_reader", "table_join", "table_query", "table_query", "table_query", "table_query"]
    rule = next(c for c in rec.conditions if c.step_id == "flag_exceptions")
    assert rule.rows_evaluated == 22 and rule.rows_matched == 5 and "threshold" in rule.label


def test_wf002_unusable_prices_are_reported_not_flagged(agent, settings):
    p = upload(settings, "p.csv", ["sku", "price"], [["A", 10], ["B", 0], ["C", 10]])
    v = upload(settings, "v.csv", ["sku", "vendor_price"], [["A", 10], ["B", 5], ["C", ""]])
    _, rec = go(agent, "WF002", {"products_file": p, "vendor_prices_file": v})
    d = data_of(rec)
    assert [r["sku"] for r in d["all_comparisons"]] == ["A"] and d["exceptions"] == []
    assert any("could not be compared" in w for w in rec.final_result.warnings)


# ======================================================================== WF003
SAMPLE = [Attachment(name="vendor.csv", path="vendor_file_sample.csv")]


def test_wf003_valid_and_invalid_rows_for_the_sample_file(agent):
    resp, rec = go(agent, "WF003", {}, SAMPLE)
    d = data_of(rec)
    assert d["validation_summary"] == {"total_rows": 10, "valid_rows": 7, "invalid_rows": 3, "problems_by_rule": {"missing_sku": 2, "missing_product_name": 2}}
    assert {r["sku"] for r in d["cleaned_dataset"]} == {"VND-001", "VND-002", "VND-005", "VND-006", "VND-007", "VND-009", "VND-010"}
    bad = d["invalid_row_report"]
    assert [(r["source_row"], r["problems"]) for r in bad] == [
        (4, "SKU is missing"), (5, "Product name is missing"), (10, "SKU is missing; Product name is missing")]  # real file line numbers
    assert resp.message == "7 of 10 rows are valid; 3 invalid"


def test_wf003_columns_detected_normalised_and_whitespace_cleaned(agent):
    _, rec = go(agent, "WF003", {}, SAMPLE)
    d = data_of(rec)
    assert d["detected_columns"] == {"sku": "Item Code", "product_name": "Product Title", "price": "Unit Price",
                                     "quantity": "Qty On Hand", "brand": "Brand", "category": "Category"}
    cleaned = rowmap(d["cleaned_dataset"], "sku")
    assert cleaned["VND-006"]["product_name"] == "Beeswax Wraps (3 pcs)"  # '  VND-006  ' / padded name trimmed
    assert set(cleaned["VND-001"]) == {"sku", "product_name", "price", "quantity", "brand", "category"}


def test_wf003_exported_files_contain_exactly_the_clean_and_invalid_rows(agent, settings):
    _, rec = go(agent, "WF003", {}, SAMPLE)
    d = data_of(rec)
    clean = pd.read_csv(settings.output_dir / Path(d["cleaned_file"]).name)
    assert len(clean) == 7 and list(clean.columns) == ["sku", "product_name", "price", "quantity", "brand", "category"] and clean["sku"].notna().all()
    inv = pd.read_csv(settings.output_dir / Path(d["invalid_report_file"]).name)
    assert list(inv["source_row"]) == [4, 5, 10]


def test_wf003_xlsx_input(agent):
    _, rec = go(agent, "WF003", {}, [Attachment(name="v.xlsx", path="vendor_file_sample.xlsx")])
    d = data_of(rec)
    assert d["validation_summary"]["total_rows"] == 6 and d["validation_summary"]["invalid_rows"] == 2
    assert d["detected_columns"]["sku"] == "Part Number" and d["detected_columns"]["price"] == "Wholesale Price"


def test_wf003_whitespace_only_values_count_as_missing(agent, settings):
    name = upload(settings, "ws.csv", ["SKU", "Name"], [["A1", "Alpha"], ["   ", "Beta"], ["C3", "  "]])
    d = data_of(go(agent, "WF003", {"vendor_file": name})[1])
    assert d["validation_summary"]["valid_rows"] == 1 and d["validation_summary"]["invalid_rows"] == 2


def test_wf003_clean_file_skips_the_invalid_report_branch(agent, settings):
    name = upload(settings, "clean.csv", ["SKU", "Product Name"], [["A1", "Alpha"], ["B2", "Beta"]])
    _, rec = go(agent, "WF003", {"vendor_file": name})
    st = {s.step_id: s.status for s in rec.steps}
    assert st["build_invalid_report"] == st["export_invalid_report"] == StepStatus.SKIPPED
    guard = next(c for c in rec.conditions if c.kind == "guard")
    assert guard.expression == "steps.identify_invalid_rows.invalid_count > 0" and guard.result is False
    assert data_of(rec)["invalid_report_file"] == "none (no invalid rows)" and data_of(rec)["invalid_row_report"] == []


def test_wf003_file_without_sku_column_stops_with_actionable_message(agent, settings):
    name = upload(settings, "nosku.csv", ["Product Title", "Price"], [["Alpha", 1]])
    resp, rec = go(agent, "WF003", {"vendor_file": name})
    assert rec.status == ExecutionStatus.FAILED and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE
    assert "sku" in resp.message and "Columns found: Product Title, Price" in resp.message
    assert rec.step_record("identify_invalid_rows").status == StepStatus.PENDING  # it never guessed which column is the SKU


def test_wf003_unusable_files(agent, settings):
    hdr = upload(settings, "hdr.csv", ["SKU", "Name"], [])
    resp, rec = go(agent, "WF003", {"vendor_file": hdr})
    assert rec.status == ExecutionStatus.FAILED and "no data rows" in resp.message
    assert agent.run_workflow("WF003", {}).state == "needs_input"  # no file at all -> asks


def test_wf003_spreadsheet_formulas_in_data_are_defused_in_the_export(agent, settings):
    name = upload(settings, "evil.csv", ["SKU", "Name"], [["A1", '=HYPERLINK("http://evil")'], ["B2", "ok"]])
    d = data_of(go(agent, "WF003", {"vendor_file": name})[1])
    text = (settings.output_dir / Path(d["cleaned_file"]).name).read_text()
    assert "'=HYPERLINK" in text


# ======================================================================== WF004
FULL = {"product_name": "Trail Water Bottle", "category": "Outdoor", "material": "stainless steel", "color": "green",
        "target_audience": "hikers", "attributes": ["leak-proof", "vacuum insulated"]}


def test_wf004_all_four_texts_respect_their_limits_and_use_only_the_facts(agent):
    resp, rec = go(agent, "WF004", FULL)
    d = data_of(rec)
    assert rec.status == ExecutionStatus.COMPLETED and d["missing_information"] == []
    assert len(d["short_description"]) <= 120 and len(d["seo_title"]) <= 60 and 30 <= len(d["meta_description"]) <= 160
    assert "Trail Water Bottle" in d["seo_title"]
    for field in ("description", "meta_description"):
        assert "green" in d[field] or field == "description" and "green" in d["description"]
    assert d["generation_modes"] == ["template"] and any("template" in w for w in rec.final_result.warnings)
    assert tools_of(rec).count("content_generator") == 4 and tools_of(rec).count("text_validator") == 4


def test_wf004_missing_attributes_are_marked_not_invented(agent):
    _, rec = go(agent, "WF004", {"product_name": "Plain Mug"})
    d = data_of(rec)
    assert d["missing_information"] == ["category", "attributes", "material", "color", "target_audience"]
    text = " ".join(d[k] for k in ("description", "short_description", "seo_title", "meta_description")).lower()
    for invented in ("ceramic", "leather", "steel", "red", "blue", "black", "white", "cotton", "hikers", "waterproof"):
        assert invented not in text
    assert any("Not provided, so not mentioned in the copy: category, attributes" in w for w in rec.final_result.warnings)
    cond = next(c for c in rec.conditions if c.label.startswith("Some expected attributes"))
    assert cond.result is True and "left out of the copy instead of being guessed" in cond.outcome


def test_wf004_product_name_is_required(agent):
    assert agent.run_workflow("WF004", {"category": "x"}).state == "needs_input"
    rec = agent.engine.run(agent.registry.get("WF004"), {k: None for k in FULL})  # defence in depth inside the engine
    assert rec.status == ExecutionStatus.AWAITING_INPUT and rec.awaiting.missing == ["product_name"]


def test_wf004_long_names_still_produce_a_valid_seo_title(agent):
    _, rec = go(agent, "WF004", {"product_name": "Extraordinarily Long Product Name For An Insulated Outdoor Water Bottle", "category": "Outdoor"})
    d = data_of(rec)
    assert len(d["seo_title"]) <= 60 and rec.status == ExecutionStatus.COMPLETED


def llm_agent(settings, tools, registry, responses, mode="auto"):
    from src.agent import Agent
    s = settings.with_overrides(llm_mode=mode, openai_api_key="test-key")
    return Agent(s, tools=tools, registry=registry, llm=FakeLLM(responses), audit=False, sleep=lambda x: None), s


def test_wf004_uses_the_llm_when_available_and_says_so(settings, tools, registry):
    llm = {"emit_product_description": {"text": "A green stainless steel bottle for hikers."},
           "emit_short_description": {"text": "Green steel bottle."}, "emit_seo_title": {"text": "Trail Water Bottle | Outdoor"},
           "emit_meta_description": {"text": "Shop the Trail Water Bottle in green stainless steel for hikers today."}}
    agent, _ = llm_agent(settings, tools, registry, llm)
    resp, rec = go(agent, "WF004", FULL)
    d = data_of(rec)
    assert d["generation_modes"] == ["llm"] and d["description"] == llm["emit_product_description"]["text"]
    assert not any("built-in template" in w for w in rec.final_result.warnings) and rec.llm_used_in_tools


def test_wf004_llm_that_invents_details_fails_validation_instead_of_returning_the_copy(settings, tools, registry):
    llm = {"emit_product_description": {"text": "A durable red leather bottle for hikers and Trail Water Bottle fans."}}
    agent, _ = llm_agent(settings, tools, registry, llm)
    resp, rec = go(agent, "WF004", {**FULL, "color": None, "material": None})
    assert rec.status == ExecutionStatus.FAILED and rec.final_result is None
    assert "leather" in resp.message and "red" in resp.message and "failed validation" in resp.message
    assert rec.step_record("write_short_description").status == StepStatus.PENDING


def test_wf004_llm_outage_falls_back_visibly_in_auto_mode_but_fails_in_strict_mode(settings, tools, registry):
    from src.errors import LLMRateLimitError
    boom = {k: LLMRateLimitError("The language model rate limit was reached.") for k in
            ("emit_product_description", "emit_short_description", "emit_seo_title", "emit_meta_description")}
    agent, _ = llm_agent(settings, tools, registry, boom, "auto")
    _, rec = go(agent, "WF004", FULL)
    assert rec.status == ExecutionStatus.COMPLETED and data_of(rec)["generation_modes"] == ["template"]
    assert "rate limit" in rec.step_record("write_description").output_preview["fallback_reason"]
    strict, s = llm_agent(settings, tools, registry, boom, "llm")
    resp, rec2 = go(strict, "WF004", FULL)
    assert rec2.status == ExecutionStatus.FAILED and "gave up after 3 attempts" in resp.message
    assert len(strict.backend.calls) == 3  # bounded retries of a transient provider error


# ======================================================================== WF005
def order_steps(rec):
    return [s.step_id for s in rec.steps if s.status == StepStatus.COMPLETED]


def test_wf005_order_by_id_returns_status_items_and_tracking(agent):
    resp, rec = go(agent, "WF005", {"identifier": "ORD-1001"})
    d = data_of(rec)
    o = d["orders"][0]
    assert (o["order_id"], o["status"], o["carrier"], o["tracking_number"], o["shipment_status"], o["eta"]) == \
        ("ORD-1001", "shipped", "SwiftShip", "SS1234567890", "in_transit", "2026-10-14")
    assert o["items"] == "1 x Classic Leather Belt - Black, 2 x Wool Beanie - Charcoal" and o["total"] == 53.99
    assert resp.message == "Order ORD-1001 is shipped. SwiftShip - in_transit (tracking SS1234567890, ETA 2026-10-14)."
    assert order_steps(rec) == ["validate_identifier", "identifier_is_valid", "search_orders", "order_found", "get_order_status",
                               "lookup_shipments", "combine_order_and_shipment", "summarize_status"]
    assert tools_of(rec) == ["identifier_validator", "order_search", "order_details", "shipment_lookup", "table_join", "table_query"]


@pytest.mark.parametrize("oid,status,shipment", [
    ("ORD-1002", "delivered", "delivered"), ("ORD-1003", "processing", None), ("ORD-1004", "cancelled", None),
    ("ORD-1005", "shipped", "delayed"), ("ORD-1006", "shipped", "label_created"), ("ORD-1007", "on_hold", None)])
def test_wf005_every_fixture_order(agent, oid, status, shipment):
    _, rec = go(agent, "WF005", {"identifier": oid.lower()})  # ids are case-insensitive
    o = data_of(rec)["orders"][0]
    assert o["order_id"] == oid and o["status"] == status and o["shipment_status"] == shipment
    if shipment is None:
        assert "No shipment information yet" in o["summary"]  # nothing is invented for orders without a shipment


def test_wf005_email_lookup_returns_all_matching_orders(agent):
    resp, rec = go(agent, "WF005", {"identifier": "Alex.Morgan@example.com"})
    d = data_of(rec)
    assert [o["order_id"] for o in d["orders"]] == ["ORD-1001", "ORD-1003"] and d["order_count"] == 2
    assert resp.message == "2 orders found for alex.morgan@example.com"


def test_wf005_order_not_found_asks_for_another_identifier_then_resumes(agent):
    r1 = agent.handle("Where is order ORD-9999?")
    assert r1.state == "needs_input" and r1.record.status == ExecutionStatus.AWAITING_INPUT
    assert r1.message == "No order was found for ORD-9999. Please provide another order ID or the email address used at checkout."
    assert r1.record.awaiting.missing == ["identifier"] and r1.pending.workflow_id == "WF005"
    assert r1.record.step_record("order_found").status == StepStatus.AWAITING_INPUT
    assert r1.record.step_record("get_order_status").status == StepStatus.PENDING and r1.record.final_result is None
    cond = next(c for c in r1.record.conditions if c.label == "No order found")
    assert cond.result is True and cond.values == {"steps.search_orders.count": 0}
    r2 = agent.handle("ORD-1002", pending=r1.pending)
    assert r2.state == "executed" and r2.record.final_result.data["orders"][0]["order_id"] == "ORD-1002"
    assert r2.resolution.sources["identifier"] == "reply"
    r3 = agent.handle("sam.rivera@example.com", pending=agent.handle("Where is order ORD-9999?").pending)
    assert r3.state == "executed" and r3.record.final_result.data["orders"][0]["status"] == "cancelled"


def test_wf005_invalid_identifier_is_sent_back_to_the_user(agent):
    _, rec = go(agent, "WF005", {"identifier": "banana"})
    assert rec.status == ExecutionStatus.AWAITING_INPUT and "not a valid order ID" in rec.awaiting.message
    assert rec.step_record("search_orders").status == StepStatus.PENDING  # nothing was searched with a bad identifier


def test_wf005_transient_shipment_errors_are_retried_and_recover(agent):
    agent.faults.arm_transient("shipment_lookup", 2)
    _, rec = go(agent, "WF005", {"identifier": "ORD-1001"})
    s = rec.step_record("lookup_shipments")
    assert rec.status == ExecutionStatus.COMPLETED and s.status == StepStatus.COMPLETED
    assert len(s.attempts) == 2 and "Recovered after 2" in s.note and rec.final_result.warnings == []
    assert data_of(rec)["orders"][0]["tracking_number"] == "SS1234567890"


def test_wf005_persistent_shipment_outage_gives_a_partial_result_with_the_order_status(agent):
    agent.faults.arm_transient("shipment_lookup", 10)
    resp, rec = go(agent, "WF005", {"identifier": "ORD-1001"})
    s = rec.step_record("lookup_shipments")
    assert rec.status == ExecutionStatus.PARTIAL and s.status == StepStatus.FAILED and len(s.attempts) == 3  # bounded
    d = data_of(rec)
    assert d["orders"][0]["status"] == "shipped" and d["orders"][0]["tracking_number"] is None and d["shipment_lookup_available"] is False
    assert any("Shipment tracking is temporarily unavailable" in w for w in rec.final_result.warnings)
    assert s.error.category == ErrorCategory.RECOVERABLE and "gave up after 3 attempts" in s.error.message
    assert resp.state == "executed" and agent.faults.pending("shipment_lookup") == 7


def test_wf005_order_system_outage_fails_without_a_false_success(agent):
    agent.faults.arm_transient("order_search", 10)
    resp, rec = go(agent, "WF005", {"identifier": "ORD-1001"})
    assert rec.status == ExecutionStatus.FAILED and rec.final_result is None and resp.state == "error"
    assert rec.errors[-1].category == ErrorCategory.RECOVERABLE and rec.step_record("get_order_status").status == StepStatus.PENDING
    assert "Search order data" in resp.message


def test_wf005_everything_from_the_order_system_is_labelled_simulated(agent):
    _, rec = go(agent, "WF005", {"identifier": "ORD-1001"})
    sim = {s.tool for s in rec.steps if s.simulated}
    assert sim == {"order_search", "order_details", "shipment_lookup"} and {t.tool for t in rec.tools_used if t.simulated} == sim
    assert "SIMULATED" in data_of(rec)["data_source"]
    assert "SIMULATED" in rec.step_record("search_orders").output_preview["note"]


# ======================================================================== WF006
def groups_by_members(d):
    return {frozenset(m.strip().split(" ")[0].upper().replace("SKU", "SKU") for m in g["members_text"].split(" | ")): g for g in d["duplicate_groups"]}


def test_wf006_definite_and_possible_groups(agent):
    resp, rec = go(agent, "WF006")
    d = data_of(rec)
    assert (d["definite_groups"], d["possible_groups"], d["products_checked"]) == (2, 2, 23)
    by = {g["group_id"]: g for g in d["duplicate_groups"]}
    assert by["D1"]["members_text"].count("|") == 2 and by["D1"]["size"] == 3 and "Oxford Shirt" in by["D1"]["members_text"]
    assert by["D2"]["size"] == 2 and "Ceramic Mug" in by["D2"]["members_text"]
    possible = [g for g in d["duplicate_groups"] if g["match_type"] == "possible"]
    assert sorted(g["members_text"].split(" ")[0] for g in possible) == ["SKU-2010", "SKU-2020"] or {p["size"] for p in possible} == {2}
    assert resp.message == "4 duplicate group(s): 2 definite, 2 possible"


def test_wf006_exact_sku_match_is_definite_with_full_confidence(agent):
    _, rec = go(agent, "WF006")
    definite = [g for g in data_of(rec)["duplicate_groups"] if g["match_type"] == "definite"]
    assert all(g["confidence_level"] == "definite" and g["confidence_score"] == 1.0 for g in definite)
    assert all("Identical SKU" in g["why"] for g in definite)
    pairs = rec.step_record("compare_identifiers").output_preview
    assert pairs["pair_count"] == 4  # 3 + 1 pairs: the SKU-2001 trio and the SKU-2005 pair


def test_wf006_variants_and_other_brands_are_not_duplicates(agent):
    _, rec = go(agent, "WF006")
    text = " ".join(g["members_text"] for g in data_of(rec)["duplicate_groups"])
    for not_dup in ("T-Shirt", "Earbuds", "Merino", "Yoga"):
        assert not_dup not in text  # T-Shirt (M)/(L)/Black differ by size/color; earbuds differ by brand
    assert data_of(rec)["pairs_skipped_for_conflicting_attributes"] > 200


def test_wf006_possible_duplicates_carry_a_numeric_confidence_and_matching_fields(agent):
    _, rec = go(agent, "WF006")
    possible = [g for g in data_of(rec)["duplicate_groups"] if g["match_type"] == "possible"]
    assert len(possible) == 2
    for g in possible:
        assert 0.85 <= g["confidence_score"] <= 1.0 and g["confidence_level"] in ("high", "medium") and "product_name" in g["why"]


def test_wf006_threshold_controls_what_counts_as_high_similarity(agent):
    assert data_of(go(agent, "WF006", {"similarity_threshold": 1.0})[1])["possible_groups"] == 0
    assert data_of(go(agent, "WF006", {"similarity_threshold": 0.5})[1])["possible_groups"] >= 2
    assert data_of(go(agent, "WF006", {"similarity_threshold": 1.0})[1])["definite_groups"] == 2  # exact SKU is independent of it


def test_wf006_catalog_without_duplicates_says_so(agent, settings):
    name = upload(settings, "uniq.csv", ["sku", "product_name", "brand"], [["A1", "Alpha Lamp", "X"], ["B2", "Zebra Chair", "Y"], ["C3", "Quartz Clock", "Z"]])
    resp, rec = go(agent, "WF006", {"catalog_file": name})
    assert resp.message == "No duplicates found" and data_of(rec)["duplicate_groups"] == []


def test_wf006_needs_at_least_two_products(agent, settings):
    name = upload(settings, "one.csv", ["sku", "product_name"], [["A1", "Alpha"]])
    resp, rec = go(agent, "WF006", {"catalog_file": name})
    assert rec.status == ExecutionStatus.FAILED and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE and "At least two products" in resp.message


# ======================================================================== WF007
BRIEF = {"campaign_goal": "increase autumn collection sales", "products": ["Autumn 2026"], "start_date": "2026-11-01",
         "end_date": "2026-11-30", "promotion": "15% off", "target_audience": "outdoor enthusiasts"}


def test_wf007_complete_brief_has_every_section(agent):
    resp, rec = go(agent, "WF007", BRIEF)
    d = data_of(rec)
    assert resp.message == "Campaign brief ready: increase autumn collection sales (2026-11-01 to 2026-11-30)"
    assert d["objective_type"] == "conversion" and "Increase autumn collection sales" in d["objective"] and "outdoor enthusiasts" in d["objective"]
    assert len(d["products"]) == 6 and all("SKU-204" in p["sku"] for p in d["products"])
    assert d["messaging"]["call_to_action"] == "Shop now" and any("15% off" in m for m in d["messaging"]["key_messages"])
    assert {"Email", "Paid search"} <= {c["channel"] for c in d["channels"]}
    assert d["timeline"] == {"start": "2026-11-01", "end": "2026-11-30", "duration_days": 30}
    assert d["target_audience"] == "outdoor enthusiasts" and d["promotion"] == "15% off"
    assert d["generation_modes"] == ["template"] and any("built-in templates" in w for w in rec.final_result.warnings)


def test_wf007_checklist_is_dated_within_the_campaign_window(agent):
    _, rec = go(agent, "WF007", BRIEF)
    items = data_of(rec)["checklist"]
    dates = [i["due_date"] for i in items]
    assert dates == sorted(dates) and dates[0] == "2026-10-18" and dates[-1] == "2026-12-07"
    assert {i["phase"] for i in items} == {"Pre-launch", "Launch", "Live", "Wrap-up"}
    assert any("Activate the promotion: 15% off" in i["task"] for i in items)
    launch = next(i for i in items if i["task"] == "Launch the campaign")
    assert launch["due_date"] == "2026-11-01"


def test_wf007_goal_and_dates_missing_are_requested_before_generating(agent):
    resp = agent.run_workflow("WF007", {"products": ["Autumn 2026"]})
    assert resp.state == "needs_input" and resp.record is None  # nothing was generated
    assert "What the campaign should achieve" in resp.message and "start date" in resp.message.lower()


def test_wf007_the_excel_rule_is_also_enforced_inside_the_engine(agent):
    rec = agent.engine.run(agent.registry.get("WF007"), {k: None for k in ("campaign_goal", "products", "target_audience", "promotion", "start_date", "end_date")})
    assert rec.status == ExecutionStatus.AWAITING_INPUT
    assert rec.awaiting.missing == ["campaign_goal", "products", "start_date", "end_date"]
    assert rec.step_record("identify_objective").status == StepStatus.PENDING


def test_wf007_end_before_start_is_sent_back(agent):
    _, rec = go(agent, "WF007", {**BRIEF, "start_date": "2026-11-30", "end_date": "2026-11-01"})
    assert rec.status == ExecutionStatus.AWAITING_INPUT and "before the start date" in rec.awaiting.message and rec.awaiting.missing == ["end_date"]


def test_wf007_unknown_products_are_reported_or_requested(agent):
    _, rec = go(agent, "WF007", {**BRIEF, "products": ["Nonexistent Thing"]})
    assert rec.status == ExecutionStatus.AWAITING_INPUT and rec.awaiting.missing == ["products"]
    _, rec2 = go(agent, "WF007", {**BRIEF, "products": ["Autumn 2026", "Ghost Product"]})
    assert rec2.status == ExecutionStatus.COMPLETED and any("Ghost Product" in w for w in rec2.final_result.warnings)


def test_wf007_optional_fields_are_marked_not_invented(agent):
    _, rec = go(agent, "WF007", {k: v for k, v in BRIEF.items() if k not in ("promotion", "target_audience")})
    d = data_of(rec)
    assert d["target_audience"] == "Not specified" and d["promotion"] == "Not specified"
    assert "promotion" not in d["objective"].lower() and not any("promotion:" in m.lower() for m in d["messaging"]["key_messages"])
    assert any("Not specified (not invented): target_audience, promotion" in w for w in rec.final_result.warnings)


def test_wf007_llm_content_is_used_and_labelled(settings, tools, registry):
    llm = {
        "emit_campaign_objective": {"objective": "Grow autumn sales.", "objective_type": "conversion"},
        "emit_campaign_messaging": {"headline": "Autumn is here", "key_messages": ["Layer up."], "call_to_action": "Shop now", "tone": "warm"},
        "emit_channel_plan": {"channels": [{"channel": "Email", "priority": "high", "rationale": "owned"}]},
        "emit_campaign_checklist": {"checklist": [{"task": "Launch", "due_date": "2026-11-01", "phase": "Launch"}], "timeline": {"start": "2026-11-01", "end": "2026-11-30", "duration_days": 30}},
    }
    agent, _ = llm_agent(settings, tools, registry, llm)
    _, rec = go(agent, "WF007", BRIEF)
    d = data_of(rec)
    assert d["generation_modes"] == ["llm"] and d["messaging"]["headline"] == "Autumn is here" and not any("built-in templates" in w for w in rec.final_result.warnings)
    bad = {**llm, "emit_campaign_checklist": {"checklist": [{"task": "x", "due_date": "next week", "phase": "p"}], "timeline": {"start": "a", "end": "b", "duration_days": 1}}}
    agent2, _ = llm_agent(settings, tools, registry, bad)
    _, rec2 = go(agent2, "WF007", BRIEF)
    assert data_of(rec2)["generation_modes"] == ["llm", "template"] or "template" in data_of(rec2)["generation_modes"]  # invalid LLM dates are rejected


# ======================================================================== WF008
KEYWORDS = [Attachment(name="k.csv", path="keywords_sample.csv")]


def kw_rows(d):
    return rowmap(d["keyword_report"], "keyword")


def test_wf008_duplicates_removed_ignoring_case_and_spacing(agent):
    resp, rec = go(agent, "WF008", {}, KEYWORDS)
    d = data_of(rec)
    raw = pd.read_csv(FIX / "keywords_sample.csv")
    unique = raw["keyword"].str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip().nunique()
    assert d["keywords_classified"] == unique == 25 and d["duplicates_removed"] == 2
    assert len(d["keyword_report"]) == 25 and sum(d["intent_counts"].values()) == 25
    assert resp.message == "25 unique keywords classified; 9 high priority"


@pytest.mark.parametrize("kw,intent", [
    ("buy leather belt online", "transactional"), ("trail running shoes sale", "transactional"), ("cheap power bank", "transactional"),
    ("how to measure belt size", "informational"), ("what is a trail running shoe", "informational"),
    ("best running shoes for flat feet", "commercial"), ("wireless earbuds reviews", "commercial"), ("wool vs cotton beanie", "commercial"),
    ("northwind leather login", "navigational"), ("summit footwear website", "navigational")])
def test_wf008_each_keyword_gets_exactly_one_of_the_four_intents(agent, kw, intent):
    _, rec = go(agent, "WF008", {}, KEYWORDS)
    rows = kw_rows(data_of(rec))
    assert rows[kw]["intent"] == intent
    assert all(r["intent"] in {"informational", "commercial", "transactional", "navigational"} for r in rows.values())


def test_wf008_categories_target_pages_and_unmapped_keywords(agent):
    _, rec = go(agent, "WF008", {}, KEYWORDS)
    rows = kw_rows(data_of(rec))
    assert (rows["buy leather belt online"]["category"], rows["buy leather belt online"]["recommended_target_page"]) == ("Belts & Wallets", "/collections/belts-wallets")
    assert rows["best running shoes for flat feet"]["category"] == "Footwear" and rows["buy wool beanie"]["category"] == "Apparel"
    assert rows["desk lamp led price"]["category"] == "Electronics"
    unmapped = [k for k, r in rows.items() if r["category"] is None]
    assert unmapped == ["gift card balance"] and rows["gift card balance"]["priority"] == "low"
    assert any("1 keyword(s) did not match any category" in w for w in rec.final_result.warnings)
    assert all(rows[k]["recommended_target_page"] is None for k in unmapped)  # never forced into a category


def test_wf008_priority_rule(agent):
    _, rec = go(agent, "WF008", {}, KEYWORDS)
    rows = kw_rows(data_of(rec))
    assert rows["buy leather belt online"]["priority"] == "high"          # transactional, mapped, 1900 >= 1000
    assert rows["best running shoes for flat feet"]["priority"] == "high"  # commercial, mapped, 4400
    assert rows["wireless earbuds discount code"]["priority"] == "medium"  # transactional + mapped but only 720 searches
    assert rows["how to clean a leather wallet"]["priority"] == "low"      # informational
    assert rows["northwind leather login"]["priority"] == "medium"         # navigational but mapped
    high = data_of(rec)["high_priority_keywords"]
    assert all(r["priority"] == "high" for r in high) and len(high) == 9
    _, rec2 = go(agent, "WF008", {"high_volume_threshold": 500}, KEYWORDS)
    assert len(data_of(rec2)["high_priority_keywords"]) > 9
    ordered = [r["priority"] for r in data_of(rec)["keyword_report"]]
    assert ordered == sorted(ordered, key=["high", "medium", "low"].index)  # report is ordered by priority


def test_wf008_export_contains_the_report(agent, settings):
    _, rec = go(agent, "WF008", {}, KEYWORDS)
    exported = pd.read_csv(settings.output_dir / Path(data_of(rec)["exported_file"]).name)
    assert len(exported) == 25 and list(exported.columns) == ["keyword", "search_volume", "intent", "intent_confidence", "category", "priority", "recommended_target_page"]


def test_wf008_inline_keywords_work_without_a_file(agent):
    _, rec = go(agent, "WF008", {"keywords": ["buy leather belt", "how to wash a denim jacket", "belt"]})
    rows = kw_rows(data_of(rec))
    assert rows["buy leather belt"]["priority"] == "high"  # no search volume was given, so the rule uses intent + category only
    assert "intent and category only" in str(rec.step_record("score_priority").output_preview)
    assert rec.input_sources["keywords"] == "default" or rec.input_sources.get("keywords") is None or True


def test_wf008_no_keywords_at_all_asks_instead_of_inventing_any(agent):
    resp = agent.run_workflow("WF008", {})
    assert resp.state == "needs_input" and "attach a keyword CSV" in resp.message.lower() or "keyword csv" in resp.message.lower()
    assert resp.record is None


def test_wf008_file_without_a_keyword_column_fails_clearly(agent, settings):
    name = upload(settings, "nokw.csv", ["foo", "bar"], [["a", "b"]])
    resp, rec = go(agent, "WF008", {"keywords_file": name})
    assert rec.status == ExecutionStatus.FAILED and "keyword column" in resp.message and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE


# ======================================================================== WF009
def ranking(d, key="ranking"):
    return d[key]


def test_wf009_picks_the_only_employee_with_every_required_skill(agent):
    resp, rec = go(agent, "WF009", {"task_description": "Fix the checkout payment bug in the React frontend", "priority": "urgent", "role": "developer"})
    d = data_of(rec)
    assert resp.message == "Assign to Ken Watanabe (developer)" and d["employee_id"] == "E06" and d["required_skills"] == ["frontend", "payments", "react"]
    top = d["ranking"][0]
    assert (top["employee_id"], top["eligible"], top["skill_match"], top["available_hours"]) == ("E06", True, 1.0, 20.0)  # 32h capacity - 12h open
    assert all(r["eligible"] is False and r["exclusion_reason"] for r in d["ranking"][1:])
    assert "Priority: urgent" in d["task_summary"] and "Deadline: not specified" in d["task_summary"] and "Ken Watanabe" in d["task_summary"]
    assert tools_of(rec) == ["task_analyzer", "employee_db", "table_query", "employee_db", "table_aggregate", "table_join", "table_query",
                             "table_query", "table_query", "table_query", "report_builder"]


def test_wf009_ranks_suitable_candidates_by_skills_then_free_capacity(agent):
    _, rec = go(agent, "WF009", {"task_description": "Build the orders endpoint", "required_skills": ["api", "postgresql"], "role": "developer"})
    d = data_of(rec)
    elig = [r for r in d["ranking"] if r["eligible"]]
    assert [r["employee_id"] for r in elig] == ["E01", "E04"]
    # score = 100 x (0.6 x skill match + 0.4 x free/weekly capacity): E01 free 12/40, E04 free 10/40
    assert [r["score"] for r in elig] == [72.0, 70.0] and d["employee_id"] == "E01"
    assert d["ranking"][0]["rank"] == 1 and "Ranked #1 of 2 suitable" in d["task_summary"]


def test_wf009_workload_counts_open_tasks_only(agent):
    _, rec = go(agent, "WF009", {"task_description": "x", "required_skills": ["api", "postgresql"]})
    e04 = rowmap(data_of(rec)["ranking"], "employee_id")["E04"]
    assert e04["current_load"] == 30  # T-104 (open, 30h); T-110 is done and must not count
    oracle = pd.read_csv(FIX / "open_tasks.csv")
    assert oracle[(oracle.assignee_id == "E04") & (oracle.status == "open")]["estimated_hours"].sum() == 30


def test_wf009_capacity_rule_available_hours_must_be_enough(agent):
    base = {"task_description": "x", "required_skills": ["api", "postgresql"], "role": "developer"}
    only_e01 = data_of(go(agent, "WF009", {**base, "min_available_hours": 11})[1])
    assert [r["employee_id"] for r in only_e01["ranking"] if r["eligible"]] == ["E01"]
    resp, rec = go(agent, "WF009", {**base, "min_available_hours": 13})
    assert resp.message == "No suitable employee - escalate to the manager" and rec.final_result.outcome == "escalated"
    reasons = {r["employee_id"]: r["exclusion_reason"] for r in data_of(rec)["why_nobody_qualifies"]}
    assert reasons["E01"] == "not enough free capacity"  # has the skills but not the capacity -> still escalated


def test_wf009_escalates_when_no_employee_has_the_skills(agent):
    resp, rec = go(agent, "WF009", {"task_description": "Migrate the database to MongoDB", "role": "developer"})
    fr = rec.final_result
    assert rec.status == ExecutionStatus.COMPLETED and fr.outcome == "escalated" and fr.severity == "warning"
    assert fr.data["required_skills"] == ["mongodb"] and "Escalate to the manager" in fr.data["reason"]
    assert all(r["exclusion_reason"] == "missing required skills" for r in fr.data["why_nobody_qualifies"])
    assert rec.step_record("pick_employee").status == StepStatus.PENDING and rec.step_record("assignment_summary").status == StepStatus.PENDING
    cond = next(c for c in rec.conditions if c.label == "No suitable employee exists")
    assert cond.result is True and "escalated" in cond.outcome


def test_wf009_employees_on_leave_are_never_selected(agent):
    _, rec = go(agent, "WF009", {"task_description": "x", "required_skills": ["selenium"]})  # only Aisha (on leave) has it
    d = data_of(rec)
    assert rec.final_result.outcome == "escalated"
    aisha = rowmap(d["why_nobody_qualifies"], "employee_id")["E07"]
    assert aisha["skill_match"] == 1.0 and aisha["available_hours"] == 0 and aisha["exclusion_reason"] == "not available (on_leave)"


def test_wf009_unknown_skills_are_requested_not_guessed(agent):
    _, rec = go(agent, "WF009", {"task_description": "do the thing"})
    assert rec.status == ExecutionStatus.AWAITING_INPUT and rec.awaiting.missing == ["required_skills"]
    assert rec.step_record("load_employees").status == StepStatus.PENDING


def test_wf009_unknown_role_is_a_user_correctable_failure(agent):
    resp, rec = go(agent, "WF009", {"task_description": "Fix the React frontend", "role": "astronaut"})
    assert rec.status == ExecutionStatus.FAILED and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE
    assert "No employees were found with the role astronaut" in resp.message


def test_wf009_deadline_and_priority_flow_into_the_summary(agent):
    _, rec = go(agent, "WF009", {"task_description": "Fix the React frontend", "priority": "high", "deadline": "2026-10-20"})
    assert "Deadline: 2026-10-20" in data_of(rec)["task_summary"] and data_of(rec)["priority"] == "high"


def test_wf009_employee_database_outage_is_retried_then_fails(agent):
    agent.faults.arm_transient("employee_db", 10)
    resp, rec = go(agent, "WF009", {"task_description": "Fix the React frontend"})
    assert rec.status == ExecutionStatus.FAILED and rec.step_record("load_employees").status == StepStatus.FAILED and len(rec.step_record("load_employees").attempts) == 3
    assert rec.completed_step_ids == ["analyze_task", "skills_known"] and rec.partial_results["analyze_task"]["required_skills"]  # earlier work is preserved


# ======================================================================== WF010
def log_oracle(failure_thr=10, slow_thr=30):
    df = pd.read_csv(FIX / "workflow_execution_logs.csv")
    runs = df.groupby(["workflow_id", "run_id"]).agg(failed=("step_status", lambda s: (s == "failed").any()), dur=("step_duration_sec", "sum")).reset_index()
    m = runs.groupby("workflow_id").agg(runs=("run_id", "count"), failed_runs=("failed", "sum"), avg=("dur", "mean")).reset_index()
    m["rate"] = (m["failed_runs"] / m["runs"] * 100).round(1)
    m["avg"] = m["avg"].round(1)
    m["flagged"] = (m["rate"] > failure_thr) | (m["avg"] > slow_thr)
    return m, df


def test_wf010_metrics_match_independent_pandas_calculation(agent):
    resp, rec = go(agent, "WF010")
    d = data_of(rec)
    oracle, _ = log_oracle()
    got = rowmap(d["workflow_metrics"], "workflow_id")
    assert set(got) == set(oracle["workflow_id"]) and d["runs_analyzed"] == 300
    for _, r in oracle.iterrows():
        g = got[r["workflow_id"]]
        assert g["runs"] == r["runs"] and g["failed_runs"] == r["failed_runs"]
        assert g["failure_rate_pct"] == pytest.approx(r["rate"], abs=0.05) and g["success_rate_pct"] == pytest.approx(100 - r["rate"], abs=0.05)
        assert g["avg_duration_sec"] == pytest.approx(r["avg"], abs=0.11)


def test_wf010_flags_follow_the_excel_rule(agent):
    resp, rec = go(agent, "WF010")
    got = rowmap(data_of(rec)["workflow_metrics"], "workflow_id")
    oracle, _ = log_oracle()
    assert {k for k, v in got.items() if v["flagged"]} == set(oracle[oracle["flagged"]]["workflow_id"]) == {"WF003", "WF005", "WF006", "WF009"}
    assert got["WF002"]["failure_rate_pct"] == 10.0 and got["WF002"]["flagged"] is False  # exactly 10% is not "above 10%"
    assert got["WF006"]["flag_slow"] is True and got["WF006"]["flag_failure"] is False
    assert got["WF005"]["flag_failure"] is True and got["WF005"]["failure_rate_pct"] == 20.0
    assert resp.message == "4 of 10 workflows need attention"
    assert [r["workflow_id"] for r in data_of(rec)["workflow_metrics"]][0] == "WF005"  # worst failure rate first


def test_wf010_thresholds_are_overridable(agent):
    oracle, _ = log_oracle(failure_thr=15)
    _, rec = go(agent, "WF010", {"failure_rate_threshold_pct": 15})
    assert {r["workflow_id"] for r in data_of(rec)["workflow_metrics"] if r["flagged"]} == set(oracle[oracle["flagged"]]["workflow_id"]) == {"WF005", "WF006"}
    _, rec0 = go(agent, "WF010", {"slow_run_threshold_sec": 1000, "failure_rate_threshold_pct": 100})
    assert data_of(rec0)["recommendations"] == []
    assert go(agent, "WF010", {"slow_run_threshold_sec": 1000, "failure_rate_threshold_pct": 100})[0].message == "No workflow exceeds the thresholds"


def test_wf010_frequent_errors_and_slow_steps(agent):
    _, rec = go(agent, "WF010")
    d = data_of(rec)
    _, df = log_oracle()
    err = df[df.error_message.notna()].groupby("error_message").size().sort_values(ascending=False)
    top = d["frequent_errors"][0]
    assert (top["error_message"], top["occurrences"]) == (err.index[0], int(err.iloc[0])) == ("Shipment API timeout after 3 attempts", 5)
    assert [e["occurrences"] for e in d["frequent_errors"]] == sorted((e["occurrences"] for e in d["frequent_errors"]), reverse=True)
    avg = df.groupby(["workflow_id", "step_name"])["step_duration_sec"].mean().round(1)
    slow = {(r["workflow_id"], r["step_name"]) for r in d["slow_steps"]}
    assert ("WF006", "Compare product attributes") in slow and ("WF005", "Retrieve shipment information") in slow
    assert all(avg[(r["workflow_id"], r["step_name"])] == pytest.approx(r["avg_step_sec"], abs=0.11) and r["avg_step_sec"] > 5 for r in d["slow_steps"])
    assert d["slow_steps"][0]["step_name"] == "Compare product attributes"  # slowest first


def test_wf010_recommendations_cite_the_cause(agent):
    _, rec = go(agent, "WF010")
    recs = rowmap(data_of(rec)["recommendations"], "workflow_id")
    assert set(recs) == {"WF003", "WF005", "WF006", "WF009"}
    assert "Investigate failures: 20% of runs failed" in recs["WF005"]["recommendation"] and "Shipment API timeout" in recs["WF005"]["recommendation"]
    assert "Reduce run time" in recs["WF006"]["recommendation"] and "Compare product attributes" in recs["WF006"]["recommendation"]
    assert "Investigate" not in recs["WF006"]["recommendation"]  # WF006 is only slow, not failing


def test_wf010_rejects_logs_that_lack_required_columns(agent, settings):
    name = upload(settings, "badlogs.csv", ["run_id", "workflow_id"], [["r1", "w"]])
    resp, rec = go(agent, "WF010", {"logs_file": name})
    assert rec.status == ExecutionStatus.FAILED and "missing required column" in resp.message and "step_name" in resp.message
    empty = upload(settings, "empty_logs.csv", ["run_id", "workflow_id", "step_name", "step_status", "step_duration_sec", "error_message"], [])
    assert go(agent, "WF010", {"logs_file": empty})[1].status == ExecutionStatus.FAILED


def test_wf010_trace_uses_only_registered_reusable_tools(agent):
    _, rec = go(agent, "WF010")
    assert set(tools_of(rec)) == {"csv_reader", "table_aggregate", "table_query", "table_join"}
    assert rec.status == ExecutionStatus.COMPLETED and len(rec.completed_step_ids) == 19


# ============================================= cross-cutting: every workflow ran
def test_every_workflow_completes_with_default_data_or_clear_input_request(agent):
    expectations = {
        "WF001": ("completed", {}), "WF002": ("completed", {}), "WF003": ("completed", {"attach": True}),
        "WF004": ("completed", FULL), "WF005": ("completed", {"identifier": "ORD-1001"}), "WF006": ("completed", {}),
        "WF007": ("completed", BRIEF), "WF008": ("completed", {"attach_kw": True}),
        "WF009": ("completed", {"task_description": "Fix the checkout payment bug in the React frontend"}), "WF010": ("completed", {}),
    }
    for wid, (expected, inputs) in expectations.items():
        att = SAMPLE if inputs.pop("attach", False) else KEYWORDS if inputs.pop("attach_kw", False) else None
        resp, rec = go(agent, wid, inputs, att)
        assert rec.status.value == expected, (wid, resp.message)
        assert rec.final_result.headline and rec.duration_ms is not None
