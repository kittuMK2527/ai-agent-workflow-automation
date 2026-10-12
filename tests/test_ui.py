"""Streamlit UI smoke and flow tests (headless, using Streamlit's AppTest)."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from src.config import PROJECT_ROOT

APP = str(PROJECT_ROOT / "app.py")


@pytest.fixture
def app(monkeypatch, tmp_path):
    # No key, deterministic mode, private output folders: the UI test never touches the network.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODE", "deterministic")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    return AppTest.from_file(APP, default_timeout=60).run()


def texts(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(x.value for x in at.caption)


def button(at, prefix):
    return next(b for b in at.button if b.label.startswith(prefix))


def test_app_loads_without_errors_and_shows_status(app):
    assert not app.exception
    t = texts(app)
    assert "AI Agent Workflow Automation" in t and "Deterministic fallback" in t and "10 workflows" in t and "simulated" in t.lower()
    assert {"Run a workflow", "Workflow catalog", "Developer view"} <= {x.label for x in app.tabs}


def test_examples_come_from_the_real_catalog(app):
    opts = app.selectbox(key="example_choice").options
    assert len(opts) >= 22 and any("Where is order ORD-1001?" in o for o in opts) and any(o.startswith("WF010") for o in opts)


def test_empty_request_is_rejected_politely(app):
    button(app, "▶").click().run()
    assert not app.exception and "Please type a request first" in texts(app) + " ".join(e.value for e in app.error)


def test_executing_an_example_shows_selection_trace_and_result(app):
    sel = app.selectbox(key="example_choice")
    sel.select(next(o for o in sel.options if "Which products need restocking?" in o)).run()
    assert app.text_area(key="request_text").value == "Which products need restocking?"
    button(app, "▶").click().run()
    assert not app.exception
    t = texts(app)
    assert "9 of 22 products need restocking" in t and "Customer" not in t.split("Inventory Restock Check")[0]
    assert "Inventory Restock Check" in t and "Deterministic fallback" in t
    assert any("csv_reader" in e.label for e in app.expander) and any("Identify low-stock products" in e.label for e in app.expander)


def test_clarification_flow_regression_widget_state(app):
    """Regression: assigning to the text-area key after it was created used to crash the page."""
    app.text_area(key="request_text").set_value("Where is order ORD-9999?").run()
    button(app, "▶").click().run()
    assert not app.exception, [e.value for e in app.exception]
    assert "waiting for your answer" in texts(app) and button(app, "▶").label == "▶ Send reply"
    assert app.text_area(key="request_text").value == ""  # cleared for the reply, without touching a live widget
    app.text_area(key="request_text").set_value("ORD-1002").run()
    button(app, "▶").click().run()
    assert not app.exception
    assert "ORD-1002 is delivered" in texts(app) and button(app, "▶").label == "▶ Execute Workflow"


def test_reset_clears_the_conversation(app):
    app.text_area(key="request_text").set_value("Where is order ORD-9999?").run()
    button(app, "▶").click().run()
    next(b for b in app.button if b.label == "Reset").click().run()
    assert not app.exception and button(app, "▶").label == "▶ Execute Workflow" and app.text_area(key="request_text").value == ""
    assert "waiting for your answer" not in texts(app)


def test_unrelated_request_shows_no_match_and_runs_nothing(app):
    app.text_area(key="request_text").set_value("What's the weather in Paris?").run()
    button(app, "▶").click().run()
    assert not app.exception
    assert "did not run anything" in texts(app) and "No matching workflow" in texts(app)


def test_catalog_and_developer_tabs_render_real_data(app):
    t = texts(app)
    assert "Catalog validation: OK - 10/10 required workflows registered" in t
    for name in ("Inventory Restock Check", "Workflow Performance Report", "Customer Order Status"):
        assert name in t, name  # one catalog card per workflow in the Excel
    assert all(f">WF{n:03d}<" in t for n in range(1, 11))
    assert "Load the demo extension" not in t and "Excel definition" in {x.label for x in app.tabs}
    assert any("order_search" in str(df.value.to_dict("list")) for df in app.dataframe)  # developer tool registry


def test_pipeline_reflects_the_real_response(app):
    idle = " ".join(m.value for m in app.markdown if "pipe-row" in m.value)
    assert idle.count("pipe-row idle") == 5
    app.text_area(key="request_text").set_value("Where is order ORD-1001?").run()
    button(app, "▶").click().run()
    done = " ".join(m.value for m in app.markdown if "pipe-row" in m.value)
    assert done.count("pipe-row done") == 5 and "8/8 steps" in done and "WF005" in done
    app.text_area(key="request_text").set_value("Where is order ORD-9999?").run()
    button(app, "▶").click().run()
    waiting = " ".join(m.value for m in app.markdown if "pipe-row" in m.value)
    assert "pipe-row attention" in waiting and "Paused for your input" in waiting


def test_user_supplied_text_is_escaped_wherever_it_is_displayed(app):
    """A hostile product name is extracted, shown in the headline, the inputs list and the results: it must stay inert text."""
    app.text_area(key="request_text").set_value("Generate SEO content for the Mug <img src=x onerror=alert(1)>. Category: Home").run()
    button(app, "▶").click().run()
    assert not app.exception
    page = " ".join(m.value for m in app.markdown)
    assert "Mug" in page and "&lt;img src=x onerror=alert(1)&gt;" in page   # displayed, but as text
    assert "<img src=x" not in page and "<script" not in page.replace("<style", "")


def test_failure_simulation_shows_partial_result(app):
    app.selectbox(key="fault_choice").select("Shipment API: persistent outage (partial-result demo)").run()
    app.text_area(key="request_text").set_value("Where is order ORD-1001?").run()
    button(app, "▶").click().run()
    assert not app.exception
    t = texts(app) + " ".join(w.value for w in app.warning)
    assert "Completed with problems (partial)" in t and "Shipment tracking is temporarily unavailable" in t
    assert "retried ×3" in t  # the timeline shows the bounded retries


def test_palette_picker_restyles_the_page(app):
    radio = app.radio(key="palette")
    assert [o.split()[-1] for o in radio.options] == ["Indigo", "Emerald", "Champagne", "Sunset"] and radio.value == "Indigo"
    heroes = {}
    for name in ("Indigo", "Emerald", "Champagne", "Sunset"):
        app.radio(key="palette").set_value(name).run()
        assert not app.exception, name
        css = next(m.value for m in app.markdown if m.value.startswith("<style>"))
        heroes[name] = re.search(r"--hero-2:(#[0-9A-Fa-f]{6})", css).group(1)
        assert "pstrip" in " ".join(m.value for m in app.markdown)  # the preview strip is shown
    assert len(set(heroes.values())) == 4


def test_how_it_works_is_shown_until_something_runs(app):
    page = " ".join(m.value for m in app.markdown)
    assert "How it works" in page and page.count("how-card") >= 3
    app.text_area(key="request_text").set_value("Where is order ORD-1001?").run()
    button(app, "▶").click().run()
    assert 'class="how-card"' not in " ".join(m.value for m in app.markdown)


# ------------------------------------------------------------- the LLM cannot be reached
@pytest.fixture
def unreachable_llm(monkeypatch, tmp_path):
    """A key and an endpoint where nothing listens: a genuine connection failure, no mocks, nothing leaves the machine."""
    import streamlit as st

    def make(mode: str):
        st.cache_resource.clear()  # get_agent is cached per mode; start from fresh agents
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-123456")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
        monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")
        monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "3")
        monkeypatch.setenv("LLM_MODE", mode)
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
        return AppTest.from_file(APP, default_timeout=60).run()

    yield make
    st.cache_resource.clear()


def test_auto_mode_still_answers_when_the_llm_cannot_be_reached(unreachable_llm):
    at = unreachable_llm("auto")
    assert "LLM · " in texts(at)  # configured and assumed fine until it fails
    at.text_area(key="request_text").set_value("Where is order ORD-1001?").run()
    button(at, "▶").click().run()
    assert not at.exception
    t = texts(at)
    assert "Order ORD-1001" in t and "Fallback router" in t and "not an LLM" in t  # answered, and labelled as the fallback
    assert "LLM paused · offline fallback active" in t and "paused for about" in t  # header chip + sidebar explain it
    at.text_area(key="request_text").set_value("Which products need restocking?").run()
    button(at, "▶").click().run()
    assert not at.exception and "products need restocking" in texts(at)  # the next request is instant: no second wait


def test_llm_only_mode_error_offers_a_one_click_switch_to_auto(unreachable_llm):
    at = unreachable_llm("llm")
    at.text_area(key="request_text").set_value("Where is order ORD-1001?").run()
    button(at, "▶").click().run()
    assert not at.exception and "Switch to Auto and retry" in " ".join(b.label for b in at.button)
    next(b for b in at.button if "Switch to Auto" in b.label).click().run()
    assert not at.exception and at.radio(key="llm_mode_choice").value == "auto"
    t = texts(at)
    assert "Order ORD-1001" in t and "Fallback router" in t  # the same request ran again, now with the fallback
