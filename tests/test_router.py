"""Workflow selection: deterministic router, LLM router (mocked) and fallback behaviour."""

from __future__ import annotations

import pytest

from src.agent import Agent
from src.errors import LLMAuthError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, LLMUnavailableError
from src.excel_loader import load_workbook_catalog
from src.config import PROJECT_ROOT
from src.models import Attachment
from src.router import DeterministicRouter, LLMRouter, stem, tokens
from tests.fakes import FakeLLM, route_to

QUESTIONS = load_workbook_catalog(PROJECT_ROOT / "data" / "workflows.xlsx").test_questions


@pytest.fixture
def det(registry):
    return DeterministicRouter(registry)


# ----------------------------------------------------- deterministic router
@pytest.mark.parametrize("q", QUESTIONS, ids=[q.workflow_id for q in QUESTIONS])
def test_each_excel_test_question_selects_its_workflow(det, q):
    d = det.route(q.request)
    assert d.decision == "selected" and d.workflow_id == q.workflow_id, (q.request, d.candidates)
    assert d.mode == "deterministic" and "not an LLM" in d.explanation and d.confidence and d.confidence > 0.5


@pytest.mark.parametrize("request_text,wid", [
    ("Which items are running low and need reordering?", "WF001"),
    ("show products with a low stock level", "WF001"),
    ("Compare vendor prices with ours", "WF002"),
    ("Clean up this supplier spreadsheet and show the bad rows", "WF003"),
    ("Write a description and SEO title for the Trail Water Bottle", "WF004"),
    ("Has my order shipped? alex.morgan@example.com", "WF005"),
    ("Are there duplicate listings in the catalog?", "WF006"),
    ("Make a marketing plan for the autumn launch", "WF007"),
    ("Sort this keyword list by search intent", "WF008"),
    ("who should take this ticket", "WF009"),
    ("What is the failure rate of each workflow?", "WF010"),
])
def test_paraphrases_route_correctly(det, request_text, wid):
    assert det.route(request_text).workflow_id == wid


@pytest.mark.parametrize("text", ["What's the weather in Paris?", "Tell me a joke", "hello", "", "   ", "asdf qwerty", "42"])
def test_unrelated_requests_are_rejected_not_forced_onto_a_workflow(det, text):
    d = det.route(text)
    assert d.decision == "no_match" and d.workflow_id is None and d.confidence == 0.0


def test_vague_requests_trigger_clarification_instead_of_a_guess(det):
    d = det.route("validate my products")
    assert d.decision == "ambiguous" and d.workflow_id is None
    assert len(d.candidates) >= 2 and "Which one" in d.clarification_question
    assert all(c.workflow_id and c.name for c in d.candidates)


def test_explanations_list_matched_words_not_internals(det):
    e = det.route("Where is order ORD-1001?").explanation
    assert "recognised ID/email format" in e and "(?i)" not in e and "pattern:" not in e


def test_attachment_names_help_routing(det):
    d = det.route("process this", [Attachment(name="vendor_file.csv", path="vendor_file.csv")])
    assert d.workflow_id == "WF003"


@pytest.mark.parametrize("text", [
    "give me everything that the uploaded file has", "summarize this file", "what is in this spreadsheet?", "show me the data", "open the report",
])
def test_generic_words_alone_never_select_a_workflow(det, text):
    d = det.route(text)
    assert d.decision == "no_match" and d.workflow_id is None


def test_an_attachments_file_name_is_a_weak_hint_that_cannot_decide_alone(det):
    wf_named = [Attachment(name="AI_Agent_Workflow_Assessment (1).xlsx", path="x.xlsx")]
    for text in ("hello", "give me everything that the uploaded file has", "what is in this spreadsheet?"):
        assert det.route(text, wf_named).decision == "no_match", text
    # ...but a real task in the text still routes, and the hint can help break a tie
    assert det.route("process this vendor spreadsheet", wf_named).workflow_id == "WF003"
    assert det.route("process this", [Attachment(name="vendor_file.csv", path="vendor_file.csv")]).workflow_id == "WF003"


def test_router_only_ever_offers_registered_workflows(det, registry):
    ids = set(registry.ids())
    for q in ["restock", "price", "vendor file", "order", "duplicate", "campaign", "keyword", "assign task", "performance report"]:
        d = det.route(q)
        assert d.workflow_id is None or d.workflow_id in ids
        assert all(c.workflow_id in ids for c in d.candidates)


def test_stemming_and_tokens():
    assert stem("restocking") == stem("restocked") == stem("restock")
    assert stem("duplicates") == stem("duplicated") == stem("duplicate")
    assert tokens("Which products need restocking?") == ["product", "restock"]


# --------------------------------------------------------------- LLM router
def llm_router(registry, response):
    llm = FakeLLM({"select_workflow": response})
    return LLMRouter(llm, registry), llm


def test_llm_router_selects_and_validates(registry):
    router, llm = llm_router(registry, route_to("WF005"))
    d = router.route("Where is order ORD-1001?")
    assert d.decision == "selected" and d.workflow_id == "WF005" and d.mode == "llm" and d.model == "fake-model"
    assert d.confidence == 0.93 and d.explanation == "The request is about WF005."


def test_llm_router_prompt_and_schema_contain_the_real_catalog(registry):
    router, llm = llm_router(registry, route_to("WF001"))
    router.route("anything", [Attachment(name="stock.csv", path="stock.csv")])
    call = llm.calls[0]
    for wid in registry.ids():
        assert wid in call["user"]
    assert "Inventory Restock Check" in call["user"] and "stock.csv" in call["user"] and "anything" in call["user"]
    enum = call["parameters"]["properties"]["workflow_id"]["enum"]
    assert enum == [*registry.ids(), "NONE"]  # the model cannot even name an unregistered workflow
    assert call["function"] == "select_workflow" and call["parameters"]["additionalProperties"] is False
    assert "NONE" in call["system"] and "never pick an unrelated workflow" in call["system"]


def test_llm_router_rejects_an_id_the_registry_does_not_know(registry):
    router, _ = llm_router(registry, route_to("WF999"))
    with pytest.raises(LLMResponseError, match="does not exist"):
        router.route("x")
    router2, _ = llm_router(registry, route_to("rm -rf /"))
    with pytest.raises(LLMResponseError):
        router2.route("x")


def test_llm_router_none_means_no_match(registry):
    router, _ = llm_router(registry, route_to("NONE", explanation="This is about the weather."))
    d = router.route("weather?")
    assert d.decision == "no_match" and d.workflow_id is None and d.explanation == "This is about the weather."


def test_llm_router_ambiguity_asks_for_clarification(registry):
    router, _ = llm_router(registry, route_to("WF002", ambiguous=True, alternatives=["WF003", "WF999"], clarification_question="Prices or the file?"))
    d = router.route("check the vendor stuff")
    assert d.decision == "ambiguous" and d.workflow_id is None and d.clarification_question == "Prices or the file?"
    assert [c.workflow_id for c in d.candidates] == ["WF002", "WF003"]  # invalid alternative dropped


def test_llm_router_low_confidence_is_treated_as_ambiguous(registry):
    router, _ = llm_router(registry, route_to("WF002", confidence=0.3))
    d = router.route("hmm")
    assert d.decision == "ambiguous" and d.workflow_id is None and d.clarification_question
    assert [c.workflow_id for c in d.candidates] == ["WF002"] and d.confidence == 0.3


def test_llm_router_tolerates_missing_confidence(registry):
    router, _ = llm_router(registry, route_to("WF001", confidence=None))
    assert router.route("x").confidence is None


# ----------------------------------------------------------- agent-level modes
def make_agent(settings, tools, registry, llm, mode="auto", key="test-key"):
    s = settings.with_overrides(llm_mode=mode, openai_api_key=key)
    return Agent(s, tools=tools, registry=registry, llm=llm, audit=False, sleep=lambda x: None)


def test_agent_uses_the_llm_router_when_available(settings, tools, registry):
    llm = FakeLLM({"select_workflow": route_to("WF010"), "extract_inputs": {}})
    a = make_agent(settings, tools, registry, llm)
    r = a.handle("how are the automations doing")
    assert r.routing.mode == "llm" and r.routing.workflow_id == "WF010" and r.state == "executed"
    assert r.record.selection.mode == "llm" and len(llm.calls_for("select_workflow")) == 1


def test_agent_never_executes_an_id_the_llm_invented(settings, tools, registry):
    llm = FakeLLM({"select_workflow": route_to("WF999")})
    a = make_agent(settings, tools, registry, llm, "auto")
    r = a.handle("x")
    assert r.state == "no_match" and r.record is None  # auto mode falls back to the deterministic router; nothing unknown ran
    assert r.routing.mode == "deterministic" and "chose a workflow that does not exist" in r.routing.fallback_reason
    strict = make_agent(settings, tools, registry, llm, "llm")
    r2 = strict.handle("x")
    assert r2.state == "error" and r2.record is None


@pytest.mark.parametrize("exc", [LLMRateLimitError("The language model rate limit was reached. Wait a moment and try again."),
                                 LLMTimeoutError("The language model took too long to respond. Try again in a moment."),
                                 LLMUnavailableError("Could not reach the language model service. Check your network connection.")])
def test_auto_mode_falls_back_visibly_when_the_provider_fails(settings, tools, registry, exc):
    a = make_agent(settings, tools, registry, FakeLLM({"select_workflow": exc, "extract_inputs": exc}))
    r = a.handle("Which products need restocking?")
    assert r.state == "executed" and r.routing.workflow_id == "WF001"
    assert r.routing.mode == "deterministic" and "deterministic fallback router instead (not an LLM)" in r.routing.fallback_reason
    assert exc.user_message in r.routing.fallback_reason
    assert r.resolution.extraction_mode == "deterministic" and "fallback extractor" in r.resolution.extraction_note


@pytest.mark.parametrize("exc,expected", [(LLMRateLimitError("rate limited"), "rate limited"), (LLMAuthError("The OpenAI credentials were rejected."), "credentials were rejected"),
                                           (LLMTimeoutError("timed out"), "timed out")])
def test_strict_llm_mode_reports_provider_errors_without_falling_back(settings, tools, registry, exc, expected):
    a = make_agent(settings, tools, registry, FakeLLM({"select_workflow": exc}), "llm")
    r = a.handle("Which products need restocking?")
    assert r.state == "error" and expected in r.message and r.record is None and r.error.category == exc.category


def test_llm_mode_without_a_key_is_a_clear_error_not_a_silent_fallback(settings, tools, registry):
    a = Agent(settings.with_overrides(llm_mode="llm", openai_api_key=""), tools=tools, registry=registry, audit=False)
    r = a.handle("Which products need restocking?")
    assert r.state == "error" and "OPENAI_API_KEY" in r.message
    assert a.mode_info()["effective_mode"] == "unavailable"


def test_missing_key_in_auto_mode_is_labelled_deterministic(settings, tools, registry):
    a = Agent(settings.with_overrides(llm_mode="auto", openai_api_key=""), tools=tools, registry=registry, audit=False)
    info = a.mode_info()
    assert info["effective_mode"] == "deterministic" and "No OPENAI_API_KEY" in info["reason"]
    r = a.handle("Which products need restocking?")
    assert r.routing.mode == "deterministic" and "No OPENAI_API_KEY" in r.routing.fallback_reason


def test_status_names_the_model_and_only_the_host_of_a_custom_endpoint(settings, tools, registry):
    """Works for any OpenAI-compatible provider: show model + host, never a path, credentials or the key."""
    secret = "AQ.not-a-real-key-0123456789"
    url = "https://user:pw@generativelanguage.googleapis.com/v1beta/openai/?key=" + secret
    a = Agent(settings.with_overrides(llm_mode="auto", openai_api_key=secret, openai_model="gemini-x", openai_base_url=url), tools=tools, registry=registry, audit=False)
    info = a.mode_info()
    assert info["effective_mode"] == "llm" and "gemini-x" in info["reason"] and "generativelanguage.googleapis.com" in info["reason"]
    assert secret not in str(info) and "pw" not in info["reason"] and "v1beta" not in info["reason"]
    plain = Agent(settings.with_overrides(llm_mode="auto", openai_api_key="sk-x", openai_model="m", openai_base_url=""), tools=tools, registry=registry, audit=False)
    assert plain.mode_info()["reason"] == "Using model 'm'."


def test_deterministic_mode_never_calls_the_llm(settings, tools, registry):
    llm = FakeLLM({"select_workflow": AssertionError("must not be called")})
    a = make_agent(settings, tools, registry, llm, "deterministic")
    assert a.handle("Which products need restocking?").state == "executed" and llm.calls == []
    assert a.mode_info()["effective_mode"] == "deterministic"


def test_ambiguous_and_unmatched_requests_do_not_execute_anything(agent):
    amb = agent.handle("validate my products")
    assert amb.state == "ambiguous" and amb.record is None and amb.message
    none = agent.handle("What's the weather in Paris?")
    assert none.state == "no_match" and none.record is None and "I did not run anything" in none.message
    assert "Inventory Restock Check" in none.message  # tells the user what it can do


def test_user_can_resolve_an_ambiguity_by_choosing(agent):
    amb = agent.handle("validate my products")
    pick = amb.routing.candidates[0].workflow_id
    r = agent.handle("validate my products", workflow_id=pick)
    assert r.routing.mode == "direct" and r.routing.workflow_id == pick and r.state in ("executed", "needs_input")
    bad = agent.handle("x", workflow_id="WF999")
    assert bad.state == "error" and "not a registered workflow" in bad.message
