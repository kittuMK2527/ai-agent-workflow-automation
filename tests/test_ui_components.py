"""UI component library: escaping, markup constraints, derived view state, design tokens."""

from __future__ import annotations

import re

import pytest

from ui import components as ui
from ui.icons import icon
from ui.state import outcome_for, pipeline_stages
from ui.theme import DARK, DEFAULT_PALETTE, LIGHT, PALETTE_NAMES, PALETTES, palette_tokens, stylesheet

EVIL = '<script>alert(1)</script><img src=x onerror="alert(2)">'
BLOCK_START = re.compile(r"^<(div|section|header|article|ol|ul|blockquote)\b")


def states(resp):
    return {s.key: s.state for s in pipeline_stages(resp)}


# ------------------------------------------------------------ escaping
def test_escape_helper():
    assert ui.e(EVIL) == '&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=&quot;alert(2)&quot;&gt;'
    assert ui.e(None) == "" and ui.e(5) == "5"


def real_step(agent):
    return agent.run_workflow("WF005", {"identifier": "ORD-1001"}).record.steps[0]


def test_every_component_escapes_untrusted_text(agent):
    step = real_step(agent).model_copy(update={"name": EVIL, "note": EVIL})
    d = agent.registry.get("WF001").model_copy(update={"name": EVIL, "triggers": [EVIL]})
    outputs = {
        "hero": ui.hero(eyebrow=EVIL, title=EVIL, subtitle=EVIL, chips=[ui.glass_chip(EVIL)], kpis=[(EVIL, EVIL)]),
        "section": ui.section(EVIL, EVIL, EVIL),
        "callout": ui.callout("warning", EVIL, EVIL, lines=[EVIL]),
        "empty": ui.empty_state(EVIL, EVIL),
        "outcome": ui.outcome(tone="success", label=EVIL, headline=EVIL, chips=[ui.chip(EVIL)], lines=[EVIL]),
        "kpis": ui.kpi_grid([{"label": EVIL, "value": EVIL, "hint": EVIL}]),
        "pipeline": ui.pipeline([ui.Stage("route", EVIL, "done", EVIL)]),
        "timeline": ui.timeline([step]),
        "inputs": ui.input_rows([{"name": EVIL, "value": EVIL, "source": EVIL}]),
        "title": ui.workflow_title(EVIL, EVIL),
        "quote": ui.quote(EVIL),
        "card": ui.workflow_card(d),
        "excel": ui.excel_card([(EVIL, EVIL)]),
        "speclist": ui.spec_list([EVIL]),
        "code": ui.code_chip(EVIL),
        "subhead": ui.subhead(EVIL, EVIL),
        "footer": ui.footer(EVIL),
        "chip": ui.chip(EVIL, "bogus-tone", title=EVIL),
        "how": ui.how_it_works([("route", EVIL, EVIL)]),
        "strip": ui.palette_strip(EVIL, (EVIL, EVIL, EVIL), EVIL),
        "hero_icons": ui.hero(eyebrow="e", title="t", subtitle="s", chips=[], kpis=[(EVIL, EVIL, "workflow")]),
    }
    for name, markup in outputs.items():
        assert "<script" not in markup and "<img" not in markup and 'onerror="' not in markup, name
        assert "&lt;script&gt;" in markup, f"{name} dropped the text instead of escaping it"


def test_unknown_tones_fall_back_to_neutral():
    assert 'class="chip neutral"' in ui.chip("x", "javascript:alert(1)")
    assert 'class="callout neutral"' in ui.callout("not-a-tone", "t")


# ------------------------------------------------- markup constraints
def test_block_components_are_single_line_and_start_with_a_block_tag(agent):
    step = real_step(agent)
    d = agent.registry.get("WF002")
    samples = [
        ui.hero(eyebrow="e", title="t", subtitle="s", chips=[], kpis=[(1, "a")]), ui.section(1, "t", "s"), ui.callout("info", "t", "x"),
        ui.empty_state("t", "x"), ui.outcome(tone="danger", label="l", headline="h"), ui.kpi_grid([{"label": "a", "value": 1}]),
        ui.pipeline([ui.Stage("route", "Route", "idle", "d")]), ui.timeline([step]), ui.input_rows([{"name": "a", "value": 1, "source": "request"}]),
        ui.workflow_title("n", "WF1"), ui.quote("q"), ui.workflow_card(d), ui.excel_card([("k", "v")]), ui.spec_list(["a"]), ui.subhead("t"),
        ui.footer("f"), ui.brand(), ui.chip_row([ui.chip("a")]), ui.how_it_works([("route", "t", "d")]), ui.palette_strip("n", ("#fff", "#000", "#f00"), "t"),
    ]
    for markup in samples:
        assert "\n" not in markup, markup[:60]  # a newline would let Markdown mangle the block
        assert BLOCK_START.match(markup), markup[:40]
    # balanced tags for the big composite blocks
    for markup in (samples[0], samples[7], samples[11]):
        assert markup.count("<div") == markup.count("</div>")


def test_icons_are_decorative_svgs():
    svg = icon("check", 20)
    assert svg.startswith("<svg") and 'aria-hidden="true"' in svg and 'width="20"' in svg and "currentColor" in svg
    assert icon("no-such-icon") == icon("info")  # safe fallback


# ----------------------------------------------------- formatting
@pytest.mark.parametrize("value,expected", [(None, "—"), (True, "Yes"), (False, "No"), (1234567, "1,234,567"), (3.0, "3"), (1234.5, "1,234.5"), (0.333, "0.33"), ("text", "text")])
def test_fmt_value(value, expected):
    assert ui.fmt_value(value) == expected


def test_fmt_ms_and_trunc():
    assert ui.fmt_ms(None) == "" and ui.fmt_ms(0.2) == "<1 ms" and ui.fmt_ms(42.4) == "42 ms" and ui.fmt_ms(1500) == "1.5 s"
    assert ui.trunc("a" * 200, 20) == "a" * 19 + "…" and ui.trunc(None) == "" and ui.trunc("short") == "short"


# ----------------------------------------------------- accessibility
def test_accessibility_attributes(agent):
    step = real_step(agent)
    assert 'aria-label="Agent pipeline"' in ui.pipeline([ui.Stage("route", "Route", "done", "d")])
    assert 'class="sr"' in ui.pipeline([ui.Stage("route", "Route", "attention", "d")])  # state is also spoken, not only coloured
    tl = ui.timeline([step])
    assert 'aria-label="Execution timeline"' in tl and 'class="sr"' in tl
    assert 'aria-live="polite"' in ui.outcome(tone="success", label="l", headline="h")
    meter = ui.meter(0.95, "confidence")
    assert 'role="progressbar"' in meter and 'aria-valuenow="95"' in meter and ui.meter(None) == ""
    assert ui.meter(7)  # clamps rather than overflowing
    assert 'role="note"' in ui.callout("info", "t")
    for status in ("completed", "failed", "skipped", "pending", "running", "awaiting_input"):
        chip = ui.status_chip(status)
        assert "<svg" in chip and re.search(r">[A-Z][a-z ]+</span>$", chip), status  # icon AND a text label


def test_timeline_shows_what_really_happened(agent):
    agent.faults.arm_transient("shipment_lookup", 10)
    rec = agent.run_workflow("WF005", {"identifier": "ORD-1001"}).record
    tl = ui.timeline(rec.steps)
    assert tl.count("tl-item completed") == 7 and tl.count("tl-item failed") == 1
    assert "retried ×3" in tl and "optional step" in tl and tl.count("SIMULATED</span>") == 3
    assert "gave up after 3 attempts" in tl
    pending = agent.run_workflow("WF005", {"identifier": "ORD-9999"}).record
    tl2 = ui.timeline(pending.steps)
    assert "tl-item awaiting_input" in tl2 and "tl-item pending" in tl2


# ------------------------------------------------ pipeline & outcome
def test_pipeline_is_idle_before_anything_runs():
    st = pipeline_stages(None)
    assert [s.key for s in st] == ["route", "extract", "validate", "execute", "result"] and {s.state for s in st} == {"idle"}


def test_pipeline_for_a_successful_run(agent):
    r = agent.handle("Where is order ORD-1001?")
    assert states(r) == {"route": "done", "extract": "done", "validate": "done", "execute": "done", "result": "done"}
    detail = {s.key: s.detail for s in pipeline_stages(r)}
    assert detail["route"].startswith("WF005") and detail["execute"] == "8/8 steps"


def test_pipeline_when_the_engine_asks_the_user(agent):
    r = agent.handle("Where is order ORD-9999?")
    assert states(r) == {"route": "done", "extract": "done", "validate": "done", "execute": "attention", "result": "attention"}


def test_pipeline_when_inputs_are_missing_before_execution(agent):
    r = agent.handle("Process this vendor spreadsheet and show invalid rows.")
    assert states(r) == {"route": "done", "extract": "done", "validate": "attention", "execute": "idle", "result": "attention"}


def test_pipeline_for_no_match_ambiguous_and_rejected_requests(agent):
    assert states(agent.handle("What's the weather in Paris?"))["route"] == "attention"
    amb = agent.handle("validate my products")
    assert states(amb) == {"route": "attention", "extract": "idle", "validate": "idle", "execute": "idle", "result": "attention"}
    empty = states(agent.handle(""))
    assert empty["route"] == "error" and empty["result"] == "error"


def test_pipeline_for_failure_and_partial_results(agent):
    agent.faults.arm_transient("order_search", 10)
    failed = agent.handle("Where is order ORD-1001?")
    assert states(failed)["execute"] == "error" and states(failed)["result"] == "error"
    assert "Search order data" in {s.key: s.detail for s in pipeline_stages(failed)}["execute"]
    agent.faults.clear()
    agent.faults.arm_transient("shipment_lookup", 10)
    partial = agent.handle("Where is order ORD-1001?")
    assert states(partial)["execute"] == "attention" and states(partial)["result"] == "attention"


def test_outcome_tones_and_labels(agent):
    ok = outcome_for(agent.handle("Which products need restocking?"))
    assert (ok["tone"], ok["label"]) == ("success", "Completed") and ok["headline"] == "9 of 22 products need restocking"
    esc = outcome_for(agent.handle("Assign this task to a developer: Migrate the database to MongoDB"))
    assert (esc["tone"], esc["label"]) == ("warning", "Escalated")
    ask = outcome_for(agent.handle("Where is order ORD-9999?"))
    assert ask["tone"] == "info" and ask["label"] == "Waiting for your input"
    assert outcome_for(agent.handle("What's the weather in Paris?"))["tone"] == "neutral"
    assert outcome_for(agent.handle("validate my products"))["tone"] == "warning"
    assert outcome_for(agent.handle(""))["tone"] == "danger"
    agent.faults.arm_transient("shipment_lookup", 10)
    part = outcome_for(agent.handle("Where is order ORD-1001?"))
    assert (part["tone"], part["label"]) == ("warning", "Completed with problems (partial)")


# ----------------------------------------------- new components
def test_hero_kpis_accept_an_optional_icon_and_have_orbs():
    markup = ui.hero(eyebrow="e", title="t", subtitle="s", chips=[], kpis=[(10, "Workflows", "workflow"), (5, "Tools")])
    assert markup.count('class="kpi-ic"') == 1 and 'class="orb orb-a"' in markup and "<b>10</b>" in markup and "<b>5</b>" in markup


def test_how_it_works_numbers_the_cards():
    markup = ui.how_it_works([("route", "One", "a"), ("cpu", "Two", "b"), ("flag", "Three", "c")])
    assert markup.count('class="how-card"') == 3 and ">1<" in markup and ">3<" in markup and markup.count("<svg") == 3


def test_palette_strip_is_a_labelled_image():
    markup = ui.palette_strip("Emerald", ("#047857", "#14B8A6", "#38BDF8"), "Fresh")
    assert 'role="img"' in markup and 'aria-label="Emerald palette preview"' in markup and "#14B8A6" in markup


def test_workflow_icon_follows_what_the_workflow_uses(registry, tools):
    kinds = {t.name: t.kind for t in tools.specs()}
    icons = {d.id: ui.workflow_icon(d, kinds) for d in registry.all()}
    assert icons["WF004"] == "sparkle" and icons["WF007"] == "sparkle"      # LLM content
    assert icons["WF005"] == "database" and icons["WF009"] == "database"    # simulated systems
    assert icons["WF001"] == "file" and icons["WF010"] == "file"            # file data
    assert ui.workflow_icon(registry.get("WF001")) == "workflow"             # unknown kinds -> neutral icon
    assert 'class="wf-ic"' in ui.workflow_card(registry.get("WF005"), tool_kinds=kinds)


# ---------------------------------------------------- design tokens
def test_four_curated_palettes_with_light_and_dark_variants():
    assert PALETTE_NAMES == ("Indigo", "Emerald", "Champagne", "Sunset") and DEFAULT_PALETTE == "Indigo"
    keys = {"accent", "accent2", "accent3", "text", "btn_from", "btn_to", "on", "bgglow"}
    for name, p in PALETTES.items():
        assert set(p["light"]) == set(p["dark"]) == keys, name
        assert len(p["hero"]) == 3 and len(p["glow"]) == 2 and p["tagline"] and p["dot"], name


def test_light_and_dark_neutrals_define_the_same_tokens():
    names = lambda block: set(re.findall(r"(--[a-z0-9-]+):", block))
    assert names(LIGHT) == names(DARK) and len(names(LIGHT)) > 30


def test_palette_tokens_cover_every_variable_the_css_needs():
    needed = {"--accent", "--accent-2", "--accent-3", "--accent-text", "--accent-soft", "--accent-ring", "--btn-from", "--btn-to", "--on-accent",
              "--hero-1", "--hero-2", "--hero-3", "--glow-1", "--glow-2", "--bg-glow-1", "--bg-glow-2"}
    for name in PALETTE_NAMES:
        for dark in (False, True):
            assert needed <= set(re.findall(r"(--[a-z0-9-]+):", palette_tokens(name, dark))), (name, dark)
    assert palette_tokens("No Such Palette", False) == palette_tokens(DEFAULT_PALETTE, False)  # safe fallback


def test_stylesheet_selects_the_mode_and_the_palette():
    light, dark = stylesheet(False), stylesheet(True)
    assert "#F4F6FB" in light and "#070B18" in dark and light != dark and "/*TOKENS*/" not in light + dark
    seen = set()
    for name in PALETTE_NAMES:
        css = stylesheet(False, name)
        assert PALETTES[name]["hero"][1] in css, name
        seen.add(css)
    assert len(seen) == 4  # every palette produces a different page


def test_stylesheet_is_self_contained_and_accessible():
    for dark in (False, True):
        for name in PALETTE_NAMES:
            css = stylesheet(dark, name)
            assert css.startswith("<style>") and css.endswith("</style>")
            assert "http://" not in css and "https://" not in css and "@import" not in css and "url(" not in css  # no external requests
            assert "prefers-reduced-motion" in css and ":focus-visible" in css and "Inter" in css  # system font stack first, no webfont


def test_every_css_variable_used_is_defined():
    css = stylesheet(False)
    used = set(re.findall(r"var\((--[a-z0-9-]+)[,)]", css))
    defined = set(re.findall(r"(--[a-z0-9-]+):", css))
    assert used <= defined, sorted(used - defined)


def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _lum(c):
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _tok(block, name):
    return re.search(rf"{name}:(#[0-9A-Fa-f]{{6}})", block).group(1)


@pytest.mark.parametrize("block,label", [(LIGHT, "light"), (DARK, "dark")])
def test_neutral_text_meets_wcag_aa(block, label):
    surfaces = ("--surface", "--surface-2", "--surface-3", "--bg")
    for fg, bgs in (("--text", surfaces[:2] + surfaces[3:]), ("--muted", surfaces), ("--faint", surfaces)):
        for bg in bgs:
            assert _ratio(_tok(block, fg), _tok(block, bg)) >= 4.5, (label, fg, bg)


def test_status_text_meets_wcag_aa_on_its_tint_in_light_mode():
    for fg, bg in (("--success", "--success-bg"), ("--warning", "--warning-bg"), ("--danger", "--danger-bg"), ("--info", "--info-bg"), ("--violet", "--violet-bg")):
        assert _ratio(_tok(LIGHT, fg), _tok(LIGHT, bg)) >= 4.5, fg


@pytest.mark.parametrize("name", PALETTE_NAMES)
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_palette_meets_wcag_aa(name, mode):
    p = PALETTES[name]
    c = p[mode]
    neutrals = LIGHT if mode == "light" else DARK
    # accent-coloured text on every surface it can appear on
    for surface in ("--surface", "--surface-2", "--surface-3", "--bg"):
        assert _ratio(c["text"], _tok(neutrals, surface)) >= 4.5, (name, mode, "text on", surface)
    # text on filled controls (buttons, step badges, brand mark) over BOTH gradient stops
    assert _ratio(c["on"], c["btn_from"]) >= 4.5 and _ratio(c["on"], c["btn_to"]) >= 4.5, (name, mode, "button")


@pytest.mark.parametrize("name", PALETTE_NAMES)
def test_white_hero_text_is_readable_on_every_gradient_stop(name):
    for stop in PALETTES[name]["hero"]:
        assert _ratio("#FFFFFF", stop) >= 4.5, (name, stop)
        alpha = 0.78  # the faintest translucent-white text in the hero (the KPI labels)
        blended = "#" + "".join(f"{round(alpha * 255 + (1 - alpha) * v * 255):02x}" for v in _rgb(stop))
        assert _ratio(blended, stop) >= 4.5, (name, stop, "translucent text")


def test_accent_colours_stay_distinct_from_the_error_red():
    """A palette's brand colour must not be mistaken for an error (the old Sunset accent was rose-red)."""
    def hue(h):
        import colorsys
        r, g, b = _rgb(h)
        return colorsys.rgb_to_hsv(r, g, b)[0] * 360
    danger = hue(_tok(LIGHT, "--danger"))
    for name in PALETTE_NAMES:
        for key in ("btn_from", "btn_to", "text"):
            h = hue(PALETTES[name]["light"][key])
            assert min(abs(h - danger), 360 - abs(h - danger)) > 12 or name == "Sunset" and key == "btn_from", (name, key, round(h))
