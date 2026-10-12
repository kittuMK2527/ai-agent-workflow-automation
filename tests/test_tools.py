"""Tool registry mechanics and every tool: valid input, invalid input, empty results, failures."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.errors import (
    ToolExecutionError, ToolInputError, ToolParameterError, TransientToolError, UnexpectedToolOutput, UnknownToolError,
)
from src.tool_registry import FaultInjector, ToolContext, ToolRegistry, ToolSpec, walk_params
from src.tools import build_default_registry
from src.tools.common import In, Out
from src.tools.data_access import DataAccess, to_number
from tests.fakes import FakeLLM


@pytest.fixture
def ctx(settings):
    return ToolContext(settings=settings, data=DataAccess(settings), faults=FaultInjector())


@pytest.fixture
def run(tools, ctx):
    def _run(_tool, **params):
        return tools.execute(_tool, params, ctx)

    return _run


def write_csv(path: Path, header: list[str], rows: list[list]):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


# ===================================================================== registry
def test_registry_lists_every_tool_with_schemas(tools):
    names = tools.names()
    assert len(names) == len(set(names)) >= 25
    for info in tools.infos():
        assert info.description and info.input_schema["type"] == "object" and info.output_schema["type"] == "object"


def test_simulated_tools_are_flagged_and_labelled(tools):
    sim = {t.name for t in tools.specs() if t.simulated}
    assert sim == {"order_search", "order_details", "shipment_lookup", "employee_db"}
    assert all(tools.get(n).kind == "simulated_api" for n in sim)
    assert all("SIMULATED" in tools.get(n).description for n in sim)


def test_unknown_tool_and_duplicate_registration(tools):
    with pytest.raises(UnknownToolError, match="not registered"):
        tools.get("rm_rf")
    reg = ToolRegistry()
    spec = tools.get("csv_reader")
    reg.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(spec)


def test_invalid_parameters_are_rejected_before_the_tool_runs(tools, ctx):
    with pytest.raises(ToolParameterError, match="invalid parameters"):
        tools.execute("csv_reader", {"nonsense": 1}, ctx)
    with pytest.raises(ToolParameterError):
        tools.execute("csv_reader", {}, ctx)  # missing 'source'
    with pytest.raises(ToolParameterError):
        tools.execute("table_query", {"rows": "not a list"}, ctx)


def test_unexpected_tool_output_is_detected(ctx):
    class P(In):
        x: int = 1

    class O(Out):
        total: int

    reg = ToolRegistry()
    reg.register(ToolSpec("wrong_shape", "d", P, O, lambda p, c: {"total": "abc"}))
    reg.register(ToolSpec("extra_field", "d", P, O, lambda p, c: {"total": 1, "surprise": 2}))
    reg.register(ToolSpec("not_a_dict", "d", P, O, lambda p, c: [1, 2]))
    for name in ("wrong_shape", "extra_field", "not_a_dict"):
        with pytest.raises(UnexpectedToolOutput):
            reg.execute(name, {}, ctx)


def test_retry_is_only_allowed_for_safe_tools(tools):
    assert tools.get("order_search").retry_allowed
    assert tools.get("dataset_exporter").retry_allowed  # side effect but idempotent (content-addressed)
    spec = ToolSpec("pay", "d", In, Out, lambda p, c: {}, side_effect=True, idempotent=False)
    assert not spec.retry_allowed


def test_walk_params_paths():
    assert dict(walk_params({"a": {"b": 1}, "c": [{"d": 2}, 3]})) == {"a.b": 1, "c.0.d": 2, "c.1": 3}


# =================================================================== data access
def test_sandbox_blocks_traversal_and_foreign_files(ctx, tmp_path):
    for bad in ["../../etc/passwd", "/etc/passwd", "..", "../src/config.py", "data/specs/WF001.yaml", "\x00", ""]:
        with pytest.raises(ToolInputError):
            ctx.data.resolve(bad)
    outside = tmp_path / "secret.csv"
    outside.write_text("a\n1")
    with pytest.raises(ToolInputError, match="not found"):
        ctx.data.resolve(str(outside))


def test_symlink_escape_is_blocked(settings, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1")
    link = settings.upload_dir / "link.csv"
    settings.upload_dir.mkdir(parents=True)
    link.symlink_to(secret)
    with pytest.raises(ToolInputError):
        DataAccess(settings).resolve("link.csv")


def test_unsupported_file_type_and_helpful_not_found(settings, ctx):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.upload_dir / "run.sh").write_text("echo hi")
    with pytest.raises(ToolInputError, match="Unsupported file type"):
        ctx.data.resolve("run.sh")
    with pytest.raises(ToolInputError, match="inventory.csv"):  # lists available files
        ctx.data.resolve("does_not_exist.csv")


def test_to_number_parsing():
    assert to_number("1,250") == 1250 and to_number("$12.50") == 12.5 and to_number("10%") == 10
    assert to_number("abc") is None and to_number(None) is None and to_number(True) is None and to_number("") is None


# ====================================================================== readers
def test_csv_reader_normalises_headers_and_converts_numbers(run):
    out = run("csv_reader", source="inventory.csv", numeric_columns=["current_stock"], required_columns=["sku"])
    assert out["row_count"] == 22 and out["columns"][0] == "sku"
    assert out["rows"][0]["current_stock"] == 12 and isinstance(out["rows"][0]["current_stock"], int)
    assert "_source_row" in out["rows"][0]


def test_csv_reader_missing_columns_and_bad_numbers(run, settings):
    with pytest.raises(ToolInputError, match="missing required column") as e:
        run("csv_reader", source="inventory.csv", required_columns=["colour"])
    assert e.value.code == "missing_columns"
    write_csv(settings.upload_dir.mkdir(parents=True, exist_ok=True) or settings.upload_dir / "n.csv", ["Unit Price ($)", "x"], [["12", "a"], ["abc", "b"], ["", "c"]])
    out = run("csv_reader", source="n.csv", numeric_columns=["unit_price"])
    assert [r["unit_price"] for r in out["rows"]] == [12, None, None]
    assert any("1 non-numeric value" in w for w in out["warnings"])


def test_readers_handle_empty_header_only_and_encodings(run, settings):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.upload_dir / "empty.csv").write_text("")
    (settings.upload_dir / "header.csv").write_text("a,b\n")
    (settings.upload_dir / "latin.csv").write_bytes("name,price\nCaf\xe9,1\n".encode("latin-1"))
    (settings.upload_dir / "semi.csv").write_text("a;b\n1;2\n")
    with pytest.raises(ToolInputError, match="empty"):
        run("file_parser", source="empty.csv")
    assert run("file_parser", source="header.csv")["row_count"] == 0
    latin = run("file_parser", source="latin.csv")
    assert latin["rows"][0]["name"] == "Café" and any("latin-1" in w for w in latin["warnings"])
    assert run("file_parser", source="semi.csv")["rows"][0]["b"] == "2"


def test_file_parser_xlsx_keeps_raw_headers_and_row_numbers(run):
    out = run("file_parser", source="vendor_file_sample.xlsx")
    assert out["format"] == "xlsx" and out["columns"] == ["Part Number", "Product Name", "Wholesale Price", "Stock"]
    assert out["row_count"] == 6 and out["rows"][2]["Part Number"] is None
    assert [r["_source_row"] for r in out["rows"]][:3] == [2, 3, 4]


def test_row_limit_is_enforced(run, settings):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    write_csv(settings.upload_dir / "big.csv", ["a"], [[i] for i in range(20)])
    import dataclasses
    small = dataclasses.replace(settings, max_table_rows=10)
    c = ToolContext(settings=small, data=DataAccess(small))
    with pytest.raises(ToolInputError, match="over the limit"):
        build_default_registry().execute("file_parser", {"source": "big.csv"}, c)


def test_keyword_reader_file_inline_and_errors(run, settings):
    f = run("keyword_reader", source="keywords_sample.csv")
    assert f["input_mode"] == "file" and f["columns"] == ["keyword", "search_volume"] and f["rows"][0]["search_volume"] == 1900
    i = run("keyword_reader", keywords=[" a ", "", "b"])
    assert [r["keyword"] for r in i["rows"]] == ["a", "b"] and i["input_mode"] == "inline"
    with pytest.raises(ToolInputError, match="No keywords"):
        run("keyword_reader")
    with pytest.raises(ToolInputError, match="empty"):
        run("keyword_reader", keywords=["", " "])
    write_csv(settings.upload_dir.mkdir(parents=True, exist_ok=True) or settings.upload_dir / "two.csv", ["foo", "bar"], [["a", "b"]])
    with pytest.raises(ToolInputError, match="keyword column"):
        run("keyword_reader", source="two.csv")


def test_product_data_reader_matches_sku_name_and_collection(run):
    by_coll = run("product_data_reader", selectors=["Autumn 2026"])
    assert by_coll["count"] == 6 and by_coll["unmatched_selectors"] == []
    by_sku = run("product_data_reader", selectors=["sku 2010", "Wireless earbuds", "nothing here"])
    assert {p["sku"] for p in by_sku["products"]} == {"SKU-2010", "SKU-2033", "SKU-2034"}
    assert by_sku["unmatched_selectors"] == ["nothing here"]
    assert run("product_data_reader", selectors=["zzz"])["count"] == 0  # empty result is valid


def test_product_data_reader_ignores_filler_words_around_a_collection_name(run):
    """A language model (or a person) says "the Autumn 2026 collection"; the catalog says "Autumn 2026"."""
    for phrase in ("Autumn 2026 collection", "the Autumn 2026 collection", "our new Autumn 2026 range"):
        out = run("product_data_reader", selectors=[phrase])
        assert out["count"] == 6 and out["unmatched_selectors"] == [] and out["matched_selectors"] == [phrase], phrase
    # an exact match still wins, and filler alone must never select the whole catalog
    assert run("product_data_reader", selectors=["Autumn 2026"])["count"] == 6
    for only_filler in ("collection", "the new range", "products"):
        out = run("product_data_reader", selectors=[only_filler])
        assert out["count"] == 0 and out["unmatched_selectors"] == [only_filler], only_filler
    assert run("product_data_reader", selectors=["Winter 2031 collection"])["count"] == 0  # unknown names stay unknown


def test_dataset_exporter_is_idempotent_and_neutralises_formulas(run, settings):
    rows = [{"sku": "A1", "name": "=HYPERLINK(\"http://x\")"}, {"sku": "A2", "name": "+cmd"}, {"sku": "A3", "name": "-5"}, {"sku": "A4", "name": None}]
    a = run("dataset_exporter", rows=rows, name="my export!!")
    b = run("dataset_exporter", rows=rows, name="my export!!")
    assert a["path"] == b["path"] and a["file_name"].startswith("my_export_")
    text = (settings.output_dir / a["file_name"]).read_text()
    assert "'=HYPERLINK" in text and "'+cmd" in text and ",-5" in text  # numbers untouched, formulas defused
    assert len(list(settings.output_dir.glob("my_export_*"))) == 1
    assert run("dataset_exporter", rows=[], name="e", columns=["a"])["row_count"] == 0


# ======================================================================= tables
ROWS = [{"id": 1, "v": 5, "g": "a"}, {"id": 2, "v": 20, "g": "a"}, {"id": 3, "v": None, "g": "b"}, {"id": 4, "v": 7, "g": "b"}]


def test_table_query_filter_derive_sort_rank_limit_select(run):
    out = run("table_query", rows=ROWS, derive={"double": "row.v * 2"}, where="row.v > 5", sort_by=["-double"], rank_field="rk", select=["rk", "id", "double"])
    assert [r["id"] for r in out["rows"]] == [2, 4] and [r["rk"] for r in out["rows"]] == [1, 2]
    assert out["error_count"] == 1 and out["errors"][0]["row"] == 3  # the null row is reported, not dropped silently
    assert out["input_count"] == 4 and out["count"] == 2
    assert run("table_query", rows=ROWS, limit=1)["count"] == 1


def test_table_query_never_treats_missing_values_as_passing(run):
    out = run("table_query", rows=ROWS, where="row.v < 100")
    assert [r["id"] for r in out["rows"]] == [1, 2, 4] and out["error_count"] == 1


def test_table_query_reports_condition_to_the_trace(tools, settings):
    seen = []
    c = ToolContext(settings=settings, data=DataAccess(settings), condition_sink=seen.append)
    tools.execute("table_query", {"rows": ROWS, "where": "row.v > 5", "label": "big"}, c)
    assert seen[0]["rows_evaluated"] == 3 and seen[0]["rows_matched"] == 2 and seen[0]["label"] == "big"
    assert seen[0]["samples"] and "values" in seen[0]["samples"][0]


def test_table_query_errors(run):
    with pytest.raises(ToolInputError, match="unknown column"):
        run("table_query", rows=ROWS, select=["nope"])
    with pytest.raises(ToolInputError, match="mixes incompatible"):
        run("table_query", rows=[{"a": 1}, {"a": "x"}], sort_by=["a"])
    assert run("table_query", rows=[])["count"] == 0


def test_table_aggregate_all_ops(run):
    out = run("table_aggregate", rows=ROWS, group_by=["g"], aggregations={
        "n": {"op": "count"}, "total": {"op": "sum", "field": "v"}, "mean": {"op": "avg", "field": "v"}, "lo": {"op": "min", "field": "v"},
        "hi": {"op": "max", "field": "v"}, "first_id": {"op": "first", "field": "id"}, "big": {"op": "count_if", "where": "row.id > 1"},
        "kinds": {"op": "distinct_count", "field": "g"}})
    a, b = out["rows"]
    assert (a["n"], a["total"], a["mean"], a["lo"], a["hi"], a["first_id"], a["big"], a["kinds"]) == (2, 25, 12.5, 5, 20, 1, 1, 1)
    assert (b["total"], b["mean"], b["big"]) == (7, 7.0, 2)  # None is ignored by sum/avg
    assert isinstance(a["total"], int)


def test_table_aggregate_empty_and_invalid(run):
    assert run("table_aggregate", rows=[], group_by=["g"], aggregations={"n": {"op": "count"}})["rows"] == []
    with pytest.raises(ToolInputError, match="count_if"):
        run("table_aggregate", rows=ROWS, aggregations={"x": {"op": "count_if"}})
    with pytest.raises(ToolInputError, match="needs a `field`"):
        run("table_aggregate", rows=ROWS, aggregations={"x": {"op": "sum"}})
    with pytest.raises(ToolInputError, match="non-numeric"):
        run("table_aggregate", rows=ROWS, aggregations={"x": {"op": "sum", "field": "g"}})
    with pytest.raises(ToolInputError, match="unknown column"):
        run("table_aggregate", rows=ROWS, group_by=["zzz"], aggregations={"n": {"op": "count"}})


def test_table_join_normalises_keys_and_reports_unmatched(run):
    left = [{"sku": "SKU-1", "a": 1}, {"sku": "sku 2", "a": 2}, {"sku": "X9", "a": 3}]
    right = [{"sku": "sku1", "b": 10}, {"sku": "SKU-2", "b": 20}, {"sku": "Z", "b": 30}, {"sku": "sku1", "b": 99}]
    inner = run("table_join", left=left, right=right, left_key="sku", right_key="sku")
    assert [(r["a"], r["b"]) for r in inner["rows"]] == [(1, 10), (2, 20)]
    assert inner["unmatched_left_count"] == 1 and inner["unmatched_right_count"] == 1 and inner["duplicate_right_keys"] == 1
    lj = run("table_join", left=left, right=right, left_key="sku", right_key="sku", how="left")
    assert lj["count"] == 3 and lj["rows"][2]["b"] is None
    clash = run("table_join", left=[{"k": 1, "n": "L"}], right=[{"k": 1, "n": "R"}], left_key="k", right_key="k", right_suffix="vendor")
    assert clash["rows"][0] == {"k": 1, "n": "L", "n_vendor": "R"}
    with pytest.raises(ToolInputError):
        run("table_join", left=left, right=right, left_key="nope", right_key="sku")
    assert run("table_join", left=left, right=[], left_key="sku", right_key="sku", how="left")["count"] == 3


def test_table_dedupe_keeps_best_row(run):
    rows = [{"k": "Buy Belt", "vol": 10}, {"k": "buy  belt!", "vol": 50}, {"k": "other", "vol": 1}]
    out = run("table_dedupe", rows=rows, key="norm_text(row.k)", keep_max="vol")
    assert out["count"] == 2 and out["removed_count"] == 1 and out["rows"][0]["vol"] == 50
    assert run("table_dedupe", rows=[], key="row.k")["count"] == 0


# =================================================================== validation
def test_field_validator(run):
    out = run("field_validator", values={"a": "x", "b": "  ", "c": None, "d": []}, required=["a", "b"], expected=["c", "d", "e"])
    assert out["present"] == {"a": "x"} and out["missing_required"] == ["b"] and out["missing_expected"] == ["c", "d", "e"] and not out["is_valid"]


@pytest.mark.parametrize("ident,kind,norm", [("ORD-1001", "order_id", "ORD-1001"), ("ord 1002", "order_id", "ORD-1002"), ("ORD1003", "order_id", "ORD-1003"),
                                              ("Alex.Morgan@Example.com", "email", "alex.morgan@example.com")])
def test_identifier_validator_accepts(run, ident, kind, norm):
    out = run("identifier_validator", identifier=ident)
    assert out["valid"] and out["kind"] == kind and out["normalized"] == norm


@pytest.mark.parametrize("ident", ["", "hello", "ORD-", "ORD-12", "a@b", "1001", "ORD-1001; DROP TABLE"])
def test_identifier_validator_rejects(run, ident):
    assert not run("identifier_validator", identifier=ident)["valid"]


def test_detect_and_normalize_columns(run):
    det = run("detect_columns", headers=["Item Code", "Product Title", "Qty", "Mystery", "SKU"],
              targets={"sku": ["item code"], "product_name": ["product title"], "quantity": ["qty"], "price": ["cost"]})
    assert det["detected"] == {"sku": "Item Code", "product_name": "Product Title", "quantity": "Qty"}
    assert det["missing_targets"] == ["price"] and set(det["unmapped_headers"]) == {"Mystery", "SKU"}
    assert any("Both 'Item Code' and 'SKU'" in w for w in det["warnings"])
    norm = run("normalize_columns", rows=[{"Item Code": "  A1  ", "Mystery Col": " x  y ", "_source_row": 2}], mapping=det["mapping"])
    assert norm["rows"][0] == {"sku": "A1", "mystery_col": "x y", "_source_row": 2}


def test_data_validator_rules_and_missing_columns(run):
    rows = [{"sku": "A", "name": "x"}, {"sku": None, "name": "y"}, {"sku": "C", "name": " "}, {"sku": "", "name": None}]
    out = run("data_validator", rows=rows, required_columns=["sku", "zzz"], rules=[
        {"id": "s", "description": "SKU missing", "invalid_when": "is_blank(row.sku)"},
        {"id": "n", "description": "Name missing", "invalid_when": "is_blank(row.name)"}])
    assert out["missing_columns"] == ["zzz"] and out["valid_count"] == 1 and out["invalid_count"] == 3
    assert out["by_rule"] == {"s": 2, "n": 2}
    assert out["invalid_rows"][2]["reasons"] == ["SKU missing", "Name missing"]
    assert run("data_validator", rows=[])["total"] == 0


def test_text_validator_checks_length_terms_and_invented_details(run):
    facts = {"product_name": "Trail Bottle", "color": "green"}
    ok = run("text_validator", text="Trail Bottle in green.", facts=facts, max_length=40, required_terms=["trail bottle"])
    assert ok["valid"]
    bad = run("text_validator", text="Trail Bottle in red leather, durable.", facts=facts, max_length=20, required_terms=["Canteen"])
    assert not bad["valid"] and bad["unsupported_terms"] == ["leather", "red"]
    assert any("longer than 20" in i for i in bad["issues"]) and any("'Canteen'" in i for i in bad["issues"])
    assert not run("text_validator", text="  ", facts=facts)["valid"]


# ======================================================== simulated systems & faults
def test_order_tools_success_and_empty_results(run):
    s = run("order_search", kind="email", value="alex.morgan@example.com")
    assert s["order_ids"] == ["ORD-1001", "ORD-1003"] and s["simulated"] is True and "SIMULATED" in s["note"]
    assert run("order_search", kind="order_id", value="ord-1002")["count"] == 1
    assert run("order_search", kind="order_id", value="ORD-9999")["count"] == 0  # empty result is a valid answer
    d = run("order_details", order_ids=["ORD-1001", "ORD-0000"])
    assert d["count"] == 1 and d["not_found"] == ["ORD-0000"] and d["orders"][0]["item_count"] == 3
    sh = run("shipment_lookup", order_ids=["ORD-1001", "ORD-1003"])
    assert sh["count"] == 1 and sh["without_shipment"] == ["ORD-1003"] and sh["simulated"]
    with pytest.raises(ToolInputError):
        run("order_search", kind="order_id", value="  ")
    with pytest.raises(ToolParameterError):
        run("order_search", kind="phone", value="1")


def test_fault_injection_raises_in_order_then_recovers(tools, ctx):
    ctx.faults.arm_transient("shipment_lookup", 2)
    for _ in range(2):
        with pytest.raises(TransientToolError):
            tools.execute("shipment_lookup", {"order_ids": ["ORD-1001"]}, ctx)
    assert tools.execute("shipment_lookup", {"order_ids": ["ORD-1001"]}, ctx)["count"] == 1
    ctx.faults.arm_terminal("order_search")
    with pytest.raises(ToolExecutionError):
        tools.execute("order_search", {"kind": "order_id", "value": "ORD-1001"}, ctx)
    assert ctx.faults.pending() == 0 and len(ctx.faults.history) == 3


def test_corrupt_simulated_source_fails_cleanly(tools, settings, tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "orders.json").write_text("{not json")
    s = settings.with_overrides(fixtures_dir=fx)
    with pytest.raises(ToolExecutionError, match="corrupt"):
        tools.execute("order_search", {"kind": "order_id", "value": "ORD-1001"}, ToolContext(settings=s, data=DataAccess(s)))
    (fx / "orders.json").unlink()
    with pytest.raises(ToolExecutionError, match="unavailable"):
        tools.execute("order_search", {"kind": "order_id", "value": "ORD-1001"}, ToolContext(settings=s, data=DataAccess(s)))


def test_employee_db(run):
    emp = run("employee_db", entity="employees", filters={"role": "Developer"})
    assert emp["count"] == 5 and all(isinstance(e["skills"], list) for e in emp["rows"]) and emp["simulated"]
    assert run("employee_db", entity="employees", filters={"role": None})["count"] == 9  # blank filter ignored
    assert run("employee_db", entity="employees", filters={"role": "astronaut"})["count"] == 0
    tasks = run("employee_db", entity="open_tasks")
    assert tasks["count"] == 9 and all(t["status"] == "open" for t in tasks["rows"])  # 'done' tasks excluded
    with pytest.raises(ToolInputError, match="Unknown filter"):
        run("employee_db", entity="employees", filters={"shoe_size": "9"})


# ================================================================ matching & ML-free
def test_record_matcher_exact_and_similar_with_blockers(run):
    rows = [
        {"sku": "A-1", "n": "Classic Belt Black", "brand": "X", "color": "black"},
        {"sku": "a1", "n": "Classic Belt Black", "brand": "X", "color": "black"},
        {"sku": "B", "n": "Classic Belt - Black", "brand": "X", "color": "black"},
        {"sku": "C", "n": "Classic Belt Brown", "brand": "X", "color": "brown"},
        {"sku": "D", "n": "Classic Belt Black", "brand": "Y", "color": "black"},
    ]
    ex = run("record_matcher", rows=[{**r, "k": r["sku"].lower().replace("-", "")} for r in rows], mode="exact", exact_field="k")
    assert [(p["left_index"], p["right_index"]) for p in ex["pairs"]] == [(0, 1)]
    sim = run("record_matcher", rows=rows, mode="similar", fields=[{"name": "n", "method": "text"}], blockers=["brand", "color"], threshold=0.9)
    pairs = {(p["left_index"], p["right_index"]) for p in sim["pairs"]}
    assert pairs == {(0, 1), (0, 2), (1, 2)}  # brown variant (color) and other-brand (brand) are blocked
    assert sim["blocked_pairs"] >= 4
    with pytest.raises(ToolInputError):
        run("record_matcher", rows=rows, mode="similar")
    with pytest.raises(ToolInputError):
        run("record_matcher", rows=rows, mode="exact")


def test_group_matches_builds_definite_and_possible_groups(run):
    rows = [{"sku": f"S{i}", "product_name": f"P{i}"} for i in range(6)]
    pairs = [{"left_index": 0, "right_index": 1, "match_type": "exact", "score": 1.0, "matching_fields": ["k"]},
             {"left_index": 1, "right_index": 2, "match_type": "exact", "score": 1.0, "matching_fields": ["k"]},
             {"left_index": 3, "right_index": 4, "match_type": "similar", "score": 0.9, "matching_fields": ["product_name", "category"]},
             {"left_index": 4, "right_index": 5, "match_type": "similar", "score": 0.88, "matching_fields": ["product_name"]}]
    out = run("group_matches", rows=rows, pairs=pairs)
    assert (out["definite_count"], out["possible_count"]) == (1, 1)
    d, p = out["groups"]
    assert d["size"] == 3 and d["match_type"] == "definite" and p["size"] == 3 and p["min_score"] == 0.88 and p["matching_fields"] == ["product_name"]
    assert run("group_matches", rows=rows, pairs=[])["groups"] == []


def test_record_matcher_row_limit(run):
    with pytest.raises(ToolInputError, match="Too many rows"):
        run("record_matcher", rows=[{"a": i} for i in range(2001)], mode="exact", exact_field="a")


def test_keyword_classifier_and_category_mapper(run):
    kws = ["buy leather belt", "how to clean a wallet", "best running shoes", "northwind leather login", "belt", "denim jacket sale"]
    out = run("keyword_classifier", rows=[{"keyword": k} for k in kws])
    assert [r["intent"] for r in out["rows"]] == ["transactional", "informational", "commercial", "navigational", "commercial", "transactional"]
    assert out["rows"][4]["intent_confidence"] == "low" and out["defaulted_count"] == 1
    assert set(out["intent_counts"]) == {"informational", "commercial", "transactional", "navigational"}
    cats = [{"category": "Belts", "target_page": "/belts", "terms": "belt;wallet"}, {"category": "Apparel", "target_page": "/apparel", "terms": "denim;jacket"}]
    mapped = run("category_mapper", rows=out["rows"], categories=cats)
    assert [r["category"] for r in mapped["rows"]] == ["Belts", "Belts", None, None, "Belts", "Apparel"]
    assert mapped["unmapped_count"] == 2 and mapped["rows"][2]["target_page"] is None  # unmapped stays unmapped
    with pytest.raises(ToolInputError):
        run("keyword_classifier", rows=[{"x": 1}])
    with pytest.raises(ToolInputError):
        run("category_mapper", rows=[{"keyword": "a"}], categories=[{"category": "x"}])


def test_task_analyzer(run):
    out = run("task_analyzer", task_description="Fix the checkout payment bug in the React frontend")
    assert out["required_skills"] == ["frontend", "payments", "react"] and out["source"] == "lexicon"
    assert run("task_analyzer", task_description="do the thing")["source"] == "none"
    explicit = run("task_analyzer", task_description="x", explicit_skills=["Python", " SQL "])
    assert explicit["required_skills"] == ["python", "sql"] and explicit["source"] == "explicit"


def test_report_builder(run):
    out = run("report_builder", title="T", sections=[{"heading": "A", "body": "text"}, {"heading": "B", "body": ["x", "y"]},
                                                      {"heading": "C", "body": {"k": 1}}, {"heading": "D", "body": []}, {"heading": "E"}])
    assert out["text"].startswith("# T") and "- x\n- y" in out["text"] and "- k: 1" in out["text"] and "- (none)" in out["text"] and out["section_count"] == 5


# ========================================================================= content
def test_content_generator_template_mode_is_labelled(run):
    out = run("content_generator", task="product_description", facts={"product_name": "Trail Bottle", "color": "green"})
    assert out["mode"] == "template" and out["model"] is None and "TEMPLATE" in out["notes"][0]
    assert "green" in out["content"]["text"] and "leather" not in out["content"]["text"]  # nothing invented
    seo = run("content_generator", task="seo_title", facts={"product_name": "X" * 80, "category": "Cat"})
    assert len(seo["content"]["text"]) <= 60
    with pytest.raises(ToolInputError, match="'product_name' is missing"):
        run("content_generator", task="seo_title", facts={})
    with pytest.raises(ToolInputError, match="ISO dates"):
        run("content_generator", task="campaign_checklist", facts={"start_date": "soon", "end_date": "later"})
    with pytest.raises(ToolInputError, match="before its start"):
        run("content_generator", task="campaign_checklist", facts={"start_date": "2026-11-30", "end_date": "2026-11-01"})


def test_content_generator_checklist_dates_cover_the_window(run):
    out = run("content_generator", task="campaign_checklist", facts={"start_date": "2026-11-01", "end_date": "2026-11-30", "promotion": "15% off", "channels": ["Email"]})
    dates = [i["due_date"] for i in out["content"]["checklist"]]
    assert dates == sorted(dates) and dates[0] == "2026-10-18" and "2026-11-30" in dates
    assert out["content"]["timeline"] == {"start": "2026-11-01", "end": "2026-11-30", "duration_days": 30}
    assert any("15% off" in i["task"] for i in out["content"]["checklist"])


def _llm_ctx(settings, llm, mode="auto"):
    s = settings.with_overrides(llm_mode=mode)
    return ToolContext(settings=s, data=DataAccess(s), llm=llm)


def test_content_generator_uses_the_llm_when_available(tools, settings):
    llm = FakeLLM({"emit_seo_title": {"text": "Trail Bottle | Outdoor"}})
    out = tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "Trail Bottle"}}, _llm_ctx(settings, llm))
    assert out["mode"] == "llm" and out["model"] == "fake-model" and out["content"]["text"] == "Trail Bottle | Outdoor"
    call = llm.calls[0]
    assert "NEVER invent" in call["system"] and '"product_name": "Trail Bottle"' in call["user"]
    assert call["parameters"]["additionalProperties"] is False  # strict function schema


def test_llm_failure_falls_back_to_template_in_auto_mode_and_says_so(tools, settings):
    from src.errors import LLMRateLimitError
    llm = FakeLLM({"emit_seo_title": LLMRateLimitError("The language model rate limit was reached.")})
    out = tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "Trail Bottle"}}, _llm_ctx(settings, llm))
    assert out["mode"] == "template" and "rate limit" in out["fallback_reason"] and any("Language model unavailable" in n for n in out["notes"])


def test_llm_failure_in_strict_mode_is_an_error_not_a_silent_fallback(tools, settings):
    from src.errors import LLMAuthError, LLMRateLimitError
    for exc, expected in [(LLMRateLimitError("slow down"), TransientToolError), (LLMAuthError("bad key"), ToolExecutionError)]:
        llm = FakeLLM({"emit_seo_title": exc})
        with pytest.raises(expected):
            tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "T"}}, _llm_ctx(settings, llm, "llm"))


def test_a_paused_llm_is_not_retried_by_the_engine_and_falls_back_in_auto_mode(tools, settings):
    from src.errors import LLMPausedError
    exc = LLMPausedError("The language model is paused for about 42 s because its rate limit or quota was reached.")
    with pytest.raises(ToolExecutionError) as e:  # strict: a retry back-off cannot outlast a pause, so fail now, not Transient
        tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "T"}}, _llm_ctx(settings, FakeLLM({"emit_seo_title": exc}), "llm"))
    assert not isinstance(e.value, TransientToolError)
    out = tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "T"}}, _llm_ctx(settings, FakeLLM({"emit_seo_title": exc})))
    assert out["mode"] == "template" and "paused" in out["fallback_reason"]


def test_invalid_llm_output_is_not_accepted(tools, settings):
    llm = FakeLLM({"emit_seo_title": {"text": "x" * 200}})  # violates the 60-character limit
    out = tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "T"}}, _llm_ctx(settings, llm))
    assert out["mode"] == "template" and "did not pass validation" in out["fallback_reason"]
    with pytest.raises(ToolExecutionError):
        tools.execute("content_generator", {"task": "seo_title", "facts": {"product_name": "T"}}, _llm_ctx(settings, llm, "llm"))
