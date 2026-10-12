"""Input validation (src.inputs) and parameter extraction (LLM schema + deterministic fallback)."""

from __future__ import annotations

from datetime import date

import pytest

from src.extraction import DeterministicExtractor, LLMExtractor, parse_dates
from src.inputs import assign_attachments, coerce_value, resolve_inputs
from src.models import Attachment, InputSpec
from src.tools.data_access import DataAccess
from tests.fakes import FakeLLM

TODAY = date(2026, 10, 12)


def spec(**kw) -> InputSpec:
    return InputSpec(name=kw.pop("name", "x"), description="d", **kw)


# ============================================================== coerce_value
@pytest.mark.parametrize("kwargs,raw,expected", [
    (dict(type="string"), "  hi  ", "hi"), (dict(type="string"), "", None), (dict(type="string"), None, None), (dict(type="string"), "   ", None),
    (dict(type="number"), "12.5", 12.5), (dict(type="number"), "10%", 10), (dict(type="number"), 7, 7), (dict(type="number"), "1,250", 1250),
    (dict(type="integer"), "5", 5), (dict(type="integer"), 5.0, 5),
    (dict(type="boolean"), "Yes", True), (dict(type="boolean"), "off", False), (dict(type="boolean"), True, True),
    (dict(type="date"), "2026-11-01", "2026-11-01"), (dict(type="date"), date(2026, 11, 1), "2026-11-01"),
    (dict(type="choice", choices=["low", "urgent"]), "URGENT", "urgent"),
    (dict(type="list"), "a, b ; c\nd", ["a", "b", "c", "d"]), (dict(type="list"), ["a", " ", "b"], ["a", "b"]), (dict(type="list"), [], None),
    (dict(type="list", items="number"), ["1", "2.5"], [1, 2.5]),
    (dict(type="file"), " data.csv ", "data.csv"),
])
def test_coerce_valid(kwargs, raw, expected):
    assert coerce_value(spec(**kwargs), raw) == (expected, None)


@pytest.mark.parametrize("kwargs,raw,fragment", [
    (dict(type="number"), "abc", "not a number"), (dict(type="number"), True, "not a number"),
    (dict(type="number", minimum=0), -1, "below the minimum of 0"), (dict(type="number", maximum=100), 101, "above the maximum of 100"),
    (dict(type="integer"), 2.5, "not a whole number"),
    (dict(type="boolean"), "maybe", "not yes/no"),
    (dict(type="date"), "2026-13-45", "not a valid date"), (dict(type="date"), "tomorrow", "YYYY-MM-DD"),
    (dict(type="choice", choices=["low", "urgent"]), "asap", "one of: low, urgent"),
    (dict(type="string", pattern=r"[A-Z]{3}-\d+"), "abc", "does not look right"),
    (dict(type="list", items="number"), ["x"], "not a number"),
])
def test_coerce_invalid(kwargs, raw, fragment):
    value, err = coerce_value(spec(**kwargs), raw)
    assert value is None and fragment in err


def test_input_spec_rejects_inconsistent_definitions():
    with pytest.raises(ValueError, match="both required and have a default"):
        spec(required=True, default=1)
    with pytest.raises(ValueError, match="needs `choices`"):
        spec(type="choice")
    with pytest.raises(ValueError, match="lower_snake_case"):
        spec(name="Bad Name")
    with pytest.raises(ValueError, match="invalid pattern"):
        spec(pattern="(")
    with pytest.raises(ValueError, match="invalid extract_pattern"):
        spec(extract_pattern="[")


# ============================================================= resolve_inputs
@pytest.fixture
def data(settings):
    return DataAccess(settings)


def test_defaults_are_applied_and_their_source_recorded(registry, data):
    r = resolve_inputs(registry.get("WF001"), {}, data=data)
    assert r.complete and r.values["inventory_file"] == "inventory.csv" and r.values["restock_multiplier"] == 2.0
    assert r.sources == {"inventory_file": "default", "restock_multiplier": "default"} and r.values["minimum_stock"] is None


def test_extracted_values_are_coerced_and_attributed(registry, data):
    r = resolve_inputs(registry.get("WF001"), {"minimum_stock": "15", "restock_multiplier": "3"}, data=data)
    assert r.values["minimum_stock"] == 15 and r.values["restock_multiplier"] == 3 and r.sources["minimum_stock"] == "request"


def test_required_inputs_missing_are_reported_with_descriptions(registry, data):
    r = resolve_inputs(registry.get("WF004"), {}, data=data)
    assert not r.complete and [m.name for m in r.missing] == ["product_name"] and "Name of the product" in r.missing[0].description


def test_invalid_values_are_reported_separately_from_missing(registry, data):
    r = resolve_inputs(registry.get("WF001"), {"minimum_stock": "lots", "restock_multiplier": 99}, data=data)
    assert {i.name for i in r.invalid} == {"minimum_stock", "restock_multiplier"} and r.missing == []
    assert r.values["minimum_stock"] is None


def test_input_groups_require_at_least_one(registry, data):
    d = registry.get("WF008")
    none = resolve_inputs(d, {}, data=data)
    assert [m.name for m in none.missing] == ["keywords_file"] and "keyword CSV" in none.missing[0].description
    assert resolve_inputs(d, {"keywords": ["a"]}, data=data).complete
    assert resolve_inputs(d, {"keywords_file": "keywords_sample.csv"}, data=data).complete


def test_unknown_input_names_are_ignored_with_a_warning(registry, data):
    r = resolve_inputs(registry.get("WF001"), {"nonsense": 1}, data=data)
    assert r.complete and any("Ignored unknown input 'nonsense'" in w for w in r.warnings) and "nonsense" not in r.values


def test_attachments_map_to_file_inputs_user_provided_first(registry):
    d = registry.get("WF002")  # two defaulted file inputs
    att = [Attachment(name="a", path="mine.csv"), Attachment(name="b", path="vendor.csv"), Attachment(name="c", path="ignored.pdf")]
    assert assign_attachments(d, att) == {"products_file": "mine.csv", "vendor_prices_file": "vendor.csv"}
    assert assign_attachments(registry.get("WF003"), att[:1]) == {"vendor_file": "mine.csv"}
    assert assign_attachments(registry.get("WF004"), att) == {}  # no file input


def test_an_attachment_overrides_a_default_file_and_beats_a_name_in_the_text(registry, data):
    d = registry.get("WF001")
    r = resolve_inputs(d, {}, attachments=[Attachment(name="mine", path="vendor_prices.csv")], data=data)
    assert r.values["inventory_file"] == "vendor_prices.csv" and r.sources["inventory_file"] == "attachment"
    r2 = resolve_inputs(d, {"inventory_file": "products.csv"}, attachments=[Attachment(name="mine", path="vendor_prices.csv")], data=data)
    assert r2.values["inventory_file"] == "vendor_prices.csv"


def test_file_inputs_are_checked_against_the_sandbox_early(registry, data):
    for bad in ("../../etc/passwd", "/etc/passwd", "nope.csv"):
        r = resolve_inputs(registry.get("WF003"), {"vendor_file": bad}, data=data)
        assert not r.complete and r.invalid[0].name == "vendor_file" and "not found" in r.invalid[0].reason


def test_grounded_inputs_must_appear_in_the_request(registry, data):
    d = registry.get("WF005")
    ok = resolve_inputs(d, {"identifier": "ORD-1001"}, request_text="Where is order ORD-1001?", data=data)
    assert ok.complete
    hallucinated = resolve_inputs(d, {"identifier": "ORD-7777"}, request_text="Where is my order?", data=data)
    assert [m.name for m in hallucinated.missing] == ["identifier"] and any("does not appear in the request" in w for w in hallucinated.warnings)
    sloppy = resolve_inputs(d, {"identifier": "ord 1001"}, request_text="where is ORD-1001", data=data)  # case/punctuation tolerant
    assert sloppy.complete


def test_values_from_a_reply_are_not_checked_against_the_original_text(registry, data):
    r = resolve_inputs(registry.get("WF005"), {"identifier": "ORD-1002"}, sources={"identifier": "reply"}, request_text="Where is order ORD-9999?", data=data)
    assert r.complete


# ============================================================ date parsing
def test_parse_dates_iso_and_month_names():
    assert [d for _, d in parse_dates("from 2026-11-01 to 2026-11-30", TODAY)] == ["2026-11-01", "2026-11-30"]
    assert [d for _, d in parse_dates("Nov 1 to Nov 30", TODAY)] == ["2026-11-01", "2026-11-30"]
    assert [d for _, d in parse_dates("1 December 2026", TODAY)] == ["2026-12-01"]
    assert [d for _, d in parse_dates("March 5th", TODAY)] == ["2027-03-05"]  # already past this year -> next occurrence
    assert parse_dates("2026-13-45 and Feb 30", TODAY) == []
    assert parse_dates("no dates here", TODAY) == []


# ======================================================= deterministic extractor
@pytest.fixture
def extract(registry):
    ex = DeterministicExtractor()
    return lambda wid, text: ex.extract(registry.get(wid), text, today=TODAY).values


@pytest.mark.parametrize("wid,text,expected", [
    ("WF001", "Which products are below 15 units?", {"minimum_stock": 15}),
    ("WF001", "Which products need restocking?", {}),
    ("WF002", "vendor price differs by more than 5%", {"threshold_pct": 5}),
    ("WF002", "differs by more than 12.5 percent", {"threshold_pct": 12.5}),
    ("WF005", "Where is order ORD-1001?", {"identifier": "ORD-1001"}),
    ("WF005", "status for Alex.Morgan@example.com please", {"identifier": "Alex.Morgan@example.com"}),
    ("WF005", "Where is my order?", {}),
    ("WF009", "Assign this urgent task to the best available developer.", {"priority": "urgent", "role": "developer"}),
    ("WF009", "Assign this urgent task to a developer: Fix the React bug", {"priority": "urgent", "role": "developer", "task_description": "Fix the React bug"}),
    ("WF009", "task: Update docs; skills: python, sql; deadline: 2026-12-01", {"task_description": "Update docs", "required_skills": "python, sql", "deadline": "2026-12-01"}),
    ("WF008", "Classify keywords from my_words.csv", {"keywords_file": "my_words.csv"}),
    ("WF008", "Classify these. Keywords: buy shoes, how to lace", {"keywords": "buy shoes, how to lace"}),
])
def test_deterministic_extraction(extract, wid, text, expected):
    assert extract(wid, text) == expected


def test_deterministic_extraction_of_a_full_campaign_request(extract):
    v = extract("WF007", "Create a campaign brief for the Autumn 2026 collection. Goal: increase autumn sales; audience: runners; "
                         "promotion: 15% off; from 2026-11-01 to 2026-11-30")
    assert v == {"campaign_goal": "increase autumn sales", "target_audience": "runners", "promotion": "15% off",
                 "products": ["Autumn 2026"], "start_date": "2026-11-01", "end_date": "2026-11-30"}


def test_campaign_goal_pattern_and_month_name_dates(extract):
    v = extract("WF007", "Plan a brief to increase autumn sales for SKU-2040 and SKU-2041 from Nov 1 to Nov 30")
    assert v["campaign_goal"] == "increase autumn sales for SKU-2040 and SKU-2041" or v["campaign_goal"].startswith("increase autumn sales")
    assert v["products"] == ["SKU-2040", "SKU-2041"] and (v["start_date"], v["end_date"]) == ("2026-11-01", "2026-11-30")


def test_generic_words_are_never_taken_as_a_product_name(extract):
    assert extract("WF004", "Generate SEO content for this product.") == {}
    assert extract("WF004", "Generate SEO content for the Trail Water Bottle. Category: Outdoor; color: green") == \
        {"product_name": "Trail Water Bottle", "category": "Outdoor", "color": "green"}


def test_extractor_notes_that_it_is_not_an_llm(registry):
    res = DeterministicExtractor().extract(registry.get("WF001"), "x")
    assert res.mode == "deterministic" and "not an LLM" in res.note


# ============================================================== LLM extractor
def test_llm_schema_is_built_from_the_workflows_real_inputs(registry):
    llm = FakeLLM({"extract_inputs": {}})
    d = registry.get("WF009")
    LLMExtractor(llm).extract(d, "Assign the task", today=TODAY)
    schema = llm.calls[0]["parameters"]
    assert list(schema["properties"]) == [i.name for i in d.exposed_inputs]
    assert schema["required"] == list(schema["properties"]) and schema["additionalProperties"] is False
    p = schema["properties"]
    assert p["priority"]["enum"] == ["low", "medium", "high", "urgent", None] and p["required_skills"]["type"] == ["array", "null"]
    assert p["deadline"]["type"] == ["string", "null"] and p["min_available_hours"]["type"] == ["number", "null"]
    user = llm.calls[0]["user"]
    assert "2026-10-12" in user and "Employee Task Assignment" in user and "Never guess" in llm.calls[0]["system"]


def test_llm_extractor_drops_nulls_and_unknown_fields(registry):
    llm = FakeLLM({"extract_inputs": {"minimum_stock": 15, "restock_multiplier": None, "inventory_file": "", "hacker_field": "x"}})
    res = LLMExtractor(llm).extract(registry.get("WF001"), "below 15", today=TODAY)
    assert res.values == {"minimum_stock": 15} and res.mode == "llm" and res.model == "fake-model"


def test_llm_extractor_includes_conversation_context(registry):
    llm = FakeLLM({"extract_inputs": {}})
    LLMExtractor(llm).extract(registry.get("WF007"), "Nov 30", known={"campaign_goal": "grow sales"}, today=TODAY)
    assert "campaign_goal='grow sales'" in llm.calls[0]["user"]
