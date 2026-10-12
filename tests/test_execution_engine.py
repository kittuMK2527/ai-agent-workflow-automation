"""The generic execution engine, tested with purpose-built tools so each behaviour is isolated."""

from __future__ import annotations

import logging

import pytest

from src.errors import ErrorCategory, ToolExecutionError, ToolInputError, TransientToolError
from src.execution_engine import ExecutionEngine, preview
from src.models import (
    Action, DecisionCase, Ensure, ExecutionStatus, FinalOutcome, FinalSpec, InputSpec, RetryPolicy, StepDef, StepStatus,
    WarningRule, WorkflowDefinition,
)
from src.tool_registry import ToolRegistry, ToolSpec
from src.tools.common import In, Out


class Num(In):
    a: int = 0
    b: int = 0


class Total(Out):
    total: int


class Anything(In):
    value: object = None


class Echoed(Out):
    value: object = None


class Calls:
    """Mutable counters shared with the test tools."""

    def __init__(self):
        self.flaky = 0
        self.write = 0
        self.slow = 0


@pytest.fixture
def calls():
    return Calls()


@pytest.fixture
def mini_tools(calls):
    reg = ToolRegistry()

    def flaky(p, ctx):
        calls.flaky += 1
        if calls.flaky <= p.a:
            raise TransientToolError(f"timeout #{calls.flaky}")
        return {"total": calls.flaky}

    def write(p, ctx):
        calls.write += 1
        if calls.write <= p.a:
            raise TransientToolError("write timeout")
        return {"total": calls.write}

    def boom(p, ctx):
        raise ValueError("SECRET internal detail: sk-abcdefghijklmnop")

    reg.register(ToolSpec("add", "adds", Num, Total, lambda p, c: {"total": p.a + p.b}))
    reg.register(ToolSpec("echo", "echoes", Anything, Echoed, lambda p, c: {"value": p.value}))
    reg.register(ToolSpec("flaky", "fails a times then works", Num, Total, flaky))
    reg.register(ToolSpec("write", "non-idempotent side effect", Num, Total, write, side_effect=True, idempotent=False))
    reg.register(ToolSpec("safe_write", "idempotent side effect", Num, Total, write, side_effect=True, idempotent=True))
    reg.register(ToolSpec("boom", "raises a bug", Num, Total, boom))
    reg.register(ToolSpec("bad_input", "raises a user problem", Num, Total, lambda p, c: (_ for _ in ()).throw(ToolInputError("Your file is broken."))))
    reg.register(ToolSpec("terminal", "terminal error", Num, Total, lambda p, c: (_ for _ in ()).throw(ToolExecutionError("The service rejected this."))))
    reg.register(ToolSpec("wrong_shape", "bad output", Num, Total, lambda p, c: {"total": "NaN"}))
    return reg


@pytest.fixture
def engine(mini_tools, settings):
    return ExecutionEngine(mini_tools, settings, sleep=lambda s: None)


def wf(steps, *, inputs=None, final=None, tool_map=None) -> WorkflowDefinition:
    final = final or FinalSpec(outcomes={"completed": FinalOutcome(headline="done")})
    return WorkflowDefinition(
        id="T1", name="Test workflow", description="d", triggers=["t"], inputs=inputs or [InputSpec(name="x", type="number", description="x")],
        steps=steps, decision_logic="", tools_required=[], tool_map=tool_map or {}, allowed_tools=[], success_output="o", final=final, error_handling=[])


def step(id_, tool, **kw) -> StepDef:
    return StepDef(id=id_, name=id_.replace("_", " ").title(), excel_step=kw.pop("excel_step", "s"), tool=tool, **kw)


def decision(id_, cases, **kw) -> StepDef:
    return StepDef(id=id_, name=id_, excel_step="s", kind="decision", cases=cases, **kw)


def statuses(rec):
    return {s.step_id: s.status for s in rec.steps}


# ================================================================== sequencing
def test_sequential_execution_with_input_and_output_mapping(engine):
    d = wf([
        step("first", "add", params={"a": "=inputs.x", "b": 5}),
        step("second", "add", params={"a": "=steps.first.total", "b": "=inputs.x"}),
        step("third", "echo", params={"value": "{{ steps.second.total }} units"}),  # template interpolation
    ], final=FinalSpec(outcomes={"completed": FinalOutcome(headline="Total {{ steps.second.total }}", result={"msg": "=steps.third.value", "n": "=steps.first.total"})}))
    rec = engine.run(d, {"x": 10}, request="r")
    assert rec.status == ExecutionStatus.COMPLETED
    assert [s.step_id for s in rec.steps if s.status == StepStatus.COMPLETED] == ["first", "second", "third"]
    assert rec.final_result.headline == "Total 25"
    assert rec.final_result.data == {"msg": "25 units", "n": 15}
    assert rec.inputs == {"x": 10} and rec.request == "r"
    assert rec.duration_ms is not None and rec.finished_at >= rec.started_at
    assert [t.tool for t in rec.tools_used] == ["add", "echo"] and rec.tools_used[0].calls == 2


def test_step_record_captures_params_and_output_preview(engine):
    rec = engine.run(wf([step("s", "add", params={"a": 2, "b": 3})]), {"x": 1})
    s = rec.steps[0]
    assert s.params == {"a": 2, "b": 3} and s.output_summary == "total: 5" and s.output_preview == {"total": 5}
    assert s.tool == "add" and s.status == StepStatus.COMPLETED and s.duration_ms is not None and s.started_at


def test_every_run_has_a_unique_id_and_runs_are_independent(engine):
    d = wf([step("s", "add", params={"a": "=inputs.x"})])
    r1, r2 = engine.run(d, {"x": 1}), engine.run(d, {"x": 2})
    assert r1.run_id != r2.run_id and r1.steps[0].output_preview == {"total": 1} and r2.steps[0].output_preview == {"total": 2}


# ==================================================================== guards
def test_guard_skips_a_step_and_records_the_condition(engine):
    d = wf([
        step("a", "add", params={"a": 1}),
        step("maybe", "add", when="inputs.x > 5", params={"a": 100}),
        step("b", "add", params={"a": "=steps.a.total"}),
    ])
    rec = engine.run(d, {"x": 1})
    assert statuses(rec) == {"a": StepStatus.COMPLETED, "maybe": StepStatus.SKIPPED, "b": StepStatus.COMPLETED}
    cond = rec.conditions[0]
    assert (cond.kind, cond.expression, cond.result, cond.next_step) == ("guard", "inputs.x > 5", False, "b")
    assert cond.values == {"inputs.x": 1} and "skipped" in cond.outcome.lower()
    assert rec.steps[1].skipped_reason.startswith("Condition false")
    assert engine.run(d, {"x": 9}).steps[1].status == StepStatus.COMPLETED


def test_guard_that_cannot_be_evaluated_fails_the_step_not_the_process(engine):
    d = wf([step("g", "add", when="inputs.x > 1", params={})])
    rec = engine.run(d, {"x": None})
    assert rec.status == ExecutionStatus.FAILED and rec.steps[0].status == StepStatus.FAILED
    assert "could not be evaluated" in rec.errors[0].message and rec.conditions[0].error


# ================================================================= decisions
def make_decision_wf():
    return wf([
        step("first", "add", params={"a": "=inputs.x"}),
        decision("route", [
            DecisionCase(label="zero", when="steps.first.total == 0", then=Action(type="await_input", message="Need a non-zero value", missing=["x"])),
            DecisionCase(label="small", when="steps.first.total < 10", then=Action(type="goto", target="finish", message="small -> finish")),
            DecisionCase(label="negative", when="steps.first.total < 0", then=Action(type="fail", message="negative!", category=ErrorCategory.USER_CORRECTABLE)),
            DecisionCase(label="huge", when="steps.first.total > 1000", then=Action(type="end", outcome="escalated", message="too big")),
            DecisionCase(label="otherwise", then=Action(type="continue", message="normal path")),
        ]),
        step("middle", "add", params={"a": 1}),
        step("finish", "add", params={"a": 2}),
    ], final=FinalSpec(outcomes={
        "completed": FinalOutcome(headline="ok {{ steps.first.total }}"),
        "escalated": FinalOutcome(headline="escalated", severity="warning", result={"v": "=steps.first.total"}),
    }))


def test_decision_goto_skips_the_bypassed_steps(engine):
    rec = engine.run(make_decision_wf(), {"x": 5})
    assert statuses(rec) == {"first": StepStatus.COMPLETED, "route": StepStatus.COMPLETED, "middle": StepStatus.SKIPPED, "finish": StepStatus.COMPLETED}
    assert "Bypassed by branch" in rec.steps[2].skipped_reason
    taken = [c for c in rec.conditions if c.result]
    assert taken[-1].label == "small" and taken[-1].next_step == "finish" and taken[-1].values == {"steps.first.total": 5}
    assert rec.status == ExecutionStatus.COMPLETED


def test_decision_cases_are_evaluated_in_order_until_one_matches(engine):
    rec = engine.run(make_decision_wf(), {"x": 50})
    labels = [(c.label, c.result) for c in rec.conditions]
    assert labels == [("zero", False), ("small", False), ("negative", False), ("huge", False), ("otherwise", None)]
    assert statuses(rec)["middle"] == StepStatus.COMPLETED and rec.conditions[-1].outcome == "normal path"


def test_decision_await_input_pauses_without_failing(engine):
    rec = engine.run(make_decision_wf(), {"x": 0})
    assert rec.status == ExecutionStatus.AWAITING_INPUT and rec.final_result is None
    assert rec.awaiting.message == "Need a non-zero value" and rec.awaiting.missing == ["x"] and rec.awaiting.step_id == "route"
    assert rec.steps[1].status == StepStatus.AWAITING_INPUT
    assert statuses(rec)["middle"] == StepStatus.PENDING and statuses(rec)["finish"] == StepStatus.PENDING
    assert not rec.errors  # asking the user is not an error


def test_decision_end_builds_the_named_outcome(engine):
    rec = engine.run(make_decision_wf(), {"x": 5000})
    assert rec.status == ExecutionStatus.COMPLETED
    fr = rec.final_result
    assert (fr.outcome, fr.severity, fr.headline) == ("escalated", "warning", "escalated")
    assert fr.data == {"reason": "too big", "v": 5000}
    assert statuses(rec)["middle"] == StepStatus.PENDING  # the run ended early


def test_decision_fail_is_a_failure_with_its_category(engine):
    d = wf([decision("d", [DecisionCase(label="bad", when="inputs.x < 0", then=Action(type="fail", message="negative!", category=ErrorCategory.USER_CORRECTABLE))])])
    rec = engine.run(d, {"x": -1})
    assert rec.status == ExecutionStatus.FAILED and rec.final_result is None
    assert rec.errors[0].category == ErrorCategory.USER_CORRECTABLE and rec.errors[0].message == "negative!"


def test_decision_with_no_matching_case_continues(engine):
    d = wf([decision("d", [DecisionCase(label="never", when="inputs.x > 100", then=Action(type="fail", message="x"))]), step("after", "add")])
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and rec.steps[0].note == "No case matched; continued."


def test_decision_condition_error_fails_the_run(engine):
    d = wf([decision("d", [DecisionCase(label="c", when="inputs.x > 1", then=Action(type="continue"))])])
    rec = engine.run(d, {"x": None})
    assert rec.status == ExecutionStatus.FAILED and "could not be evaluated" in rec.errors[0].message


def test_await_input_missing_can_be_an_expression(engine):
    d = wf([step("f", "echo", params={"value": ["a", "b"]}),
            decision("d", [DecisionCase(label="c", then=Action(type="await_input", message="need", missing="=steps.f.value"))])])
    assert engine.run(d, {"x": 1}).awaiting.missing == ["a", "b"]


# ============================================================ post-conditions
def test_ensure_failure_stops_the_workflow_and_withholds_the_output(engine):
    d = wf([step("s", "add", params={"a": 1}, ensure=[Ensure(expr="output.total > 5", message="Total {{ output.total }} is too small", category=ErrorCategory.USER_CORRECTABLE)]),
            step("t", "add", params={"a": "=steps.s.total"})])
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.FAILED and rec.steps[0].status == StepStatus.FAILED and rec.steps[1].status == StepStatus.PENDING
    assert rec.errors[0].message == "Total 1 is too small" and rec.errors[0].category == ErrorCategory.USER_CORRECTABLE
    assert "s" not in rec.partial_results  # a failed step's output is never treated as a result
    assert rec.conditions[0].kind == "postcondition" and rec.conditions[0].result is False


def test_ensure_pass_is_recorded(engine):
    rec = engine.run(wf([step("s", "add", params={"a": 9}, ensure=[Ensure(expr="output.total == 9", message="m")])]), {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and rec.conditions[0].result is True


# ================================================================== retries
def test_transient_errors_are_retried_then_succeed(engine, calls):
    rec = engine.run(wf([step("f", "flaky", params={"a": 2})]), {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and calls.flaky == 3
    s = rec.steps[0]
    assert [a.attempt for a in s.attempts] == [1, 2] and "Recovered after 2" in s.note
    assert len(rec.recovery_attempts) == 2 and [e.recovered for e in rec.errors] == [True, True]
    assert rec.tools_used[0].calls == 3 and rec.tools_used[0].failures == 2


def test_retries_are_bounded_and_the_failure_is_reported_honestly(engine, calls):
    rec = engine.run(wf([step("f", "flaky", params={"a": 99})]), {"x": 1})
    assert calls.flaky == 3  # default max_attempts
    assert rec.status == ExecutionStatus.FAILED and rec.steps[0].status == StepStatus.FAILED
    err = rec.errors[-1]
    assert err.category == ErrorCategory.RECOVERABLE and err.retryable and "gave up after 3 attempts" in err.message
    assert rec.final_result is None


def test_custom_retry_policy_and_backoff_delays(mini_tools, settings, calls):
    delays = []
    eng = ExecutionEngine(mini_tools, settings.with_overrides(retry_backoff_scale=1.0), sleep=delays.append)
    d = wf([step("f", "flaky", params={"a": 3}, retry=RetryPolicy(max_attempts=4, backoff_seconds=0.5, backoff_factor=2))])
    rec = eng.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and delays == [0.5, 1.0, 2.0]


def test_validation_and_terminal_errors_are_never_retried(engine, calls):
    for tool, cat in (("bad_input", ErrorCategory.USER_CORRECTABLE), ("terminal", ErrorCategory.TERMINAL)):
        rec = engine.run(wf([step("s", tool)]), {"x": 1})
        assert rec.status == ExecutionStatus.FAILED and rec.steps[0].attempts == []
        assert rec.errors[0].category == cat
    assert engine.run(wf([step("s", "wrong_shape")]), {"x": 1}).errors[0].code == "unexpected_tool_output"


def test_non_idempotent_side_effects_are_not_retried_blindly(engine, calls):
    rec = engine.run(wf([step("w", "write", params={"a": 1})]), {"x": 1})
    assert calls.write == 1  # a single attempt: repeating a payment-like action could duplicate it
    assert rec.status == ExecutionStatus.FAILED and rec.steps[0].attempts[0].attempt == 1


def test_idempotent_side_effects_may_be_retried(engine, calls):
    rec = engine.run(wf([step("w", "safe_write", params={"a": 1})]), {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and calls.write == 2


# ============================================================ failure policies
def test_on_failure_continue_yields_a_partial_result_with_warnings(engine):
    d = wf([step("good", "add", params={"a": 1}), step("shaky", "terminal", on_failure="continue"), step("after", "add", params={"a": "=steps.good.total"})],
           final=FinalSpec(outcomes={"completed": FinalOutcome(headline="finished", result={"g": "=steps.good.total", "after": "=steps.after.total"})},
                           warnings=[WarningRule(when="not exists(steps.shaky)", message="the shaky step is unavailable")]))
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.PARTIAL and statuses(rec)["shaky"] == StepStatus.FAILED and statuses(rec)["after"] == StepStatus.COMPLETED
    fr = rec.final_result
    assert fr.data == {"g": 1, "after": 1}
    assert any("Step 'Shaky' failed" in w for w in fr.warnings) and "the shaky step is unavailable" in fr.warnings


def test_default_policy_stops_and_preserves_collected_results(engine):
    d = wf([step("one", "add", params={"a": 1}), step("two", "add", params={"a": 2}), step("bad", "terminal"), step("never", "add")])
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.FAILED
    assert statuses(rec) == {"one": StepStatus.COMPLETED, "two": StepStatus.COMPLETED, "bad": StepStatus.FAILED, "never": StepStatus.PENDING}
    assert rec.partial_results == {"one": {"total": 1}, "two": {"total": 2}}  # nothing is lost
    assert rec.final_result is None and rec.completed_step_ids == ["one", "two"]


def test_referencing_a_failed_steps_output_is_an_error_not_a_guess(engine):
    d = wf([step("shaky", "terminal", on_failure="continue"), step("uses", "add", params={"a": "=steps.shaky.total"})])
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.FAILED
    assert "could not prepare its input" in rec.errors[-1].message and "shaky" in rec.errors[-1].message


def test_unresolvable_final_result_never_reports_success(engine):
    d = wf([step("a", "add", when="inputs.x > 100")],
           final=FinalSpec(outcomes={"completed": FinalOutcome(headline="x", result={"v": "=steps.a.total"})}))
    rec = engine.run(d, {"x": 1})  # step 'a' is skipped, so the result cannot be built
    assert rec.status == ExecutionStatus.FAILED and rec.final_result is None
    assert "result could not be assembled" in rec.errors[-1].message


def test_warning_rule_that_cannot_be_evaluated_simply_does_not_fire(engine):
    d = wf([step("a", "add", when="inputs.x > 100")], final=FinalSpec(
        outcomes={"completed": FinalOutcome(headline="ok")}, warnings=[WarningRule(when="steps.a.total > 1", message="never")]))
    rec = engine.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and rec.final_result.warnings == []


# ===================================================== robustness / no leakage
def test_unexpected_tool_exceptions_are_contained_and_redacted(engine, caplog):
    with caplog.at_level(logging.DEBUG):
        rec = engine.run(wf([step("b", "boom")]), {"x": 1})
    assert rec.status == ExecutionStatus.FAILED
    shown = rec.errors[0].message
    assert "unexpectedly" in shown and "SECRET" not in shown and "sk-" not in shown and "Traceback" not in shown
    logged = caplog.text
    assert "sk-abcdefghijklmnop" not in logged  # redacted even in the server log


def test_expression_errors_in_params_fail_the_step(engine):
    rec = engine.run(wf([step("s", "add", params={"a": "=inputs.nope"})]), {"x": 1})
    assert rec.status == ExecutionStatus.FAILED and "could not prepare its input" in rec.errors[0].message


def test_row_rule_conditions_reported_by_tools_reach_the_record(engine, settings, tools):
    eng = ExecutionEngine(tools, settings, sleep=lambda s: None)
    d = wf([step("q", "table_query", params={"rows": [{"v": 1}, {"v": 9}], "where": "row.v > 5", "label": "big values"})], tool_map={})
    rec = eng.run(d, {"x": 1})
    c = rec.conditions[0]
    assert (c.kind, c.label, c.rows_evaluated, c.rows_matched, c.expression) == ("row_rule", "big values", 2, 1, "row.v > 5")
    assert c.outcome == "1 of 2 row(s) matched" and c.samples


def test_row_expressions_are_not_evaluated_by_the_engine(engine, tools, settings):
    """`where` holds `row.*` which only exists per row; the engine must pass it through untouched."""
    eng = ExecutionEngine(tools, settings, sleep=lambda s: None)
    d = wf([step("q", "table_query", params={"rows": [{"v": 1}], "where": "row.v == 1"})])
    assert eng.run(d, {"x": 1}).status == ExecutionStatus.COMPLETED


def test_side_effect_duplicate_is_suppressed_within_a_run(mini_tools, settings, calls):
    eng = ExecutionEngine(mini_tools, settings, sleep=lambda s: None)
    d = wf([step("w1", "safe_write", params={"a": 0}), step("w2", "safe_write", params={"a": 0})])
    rec = eng.run(d, {"x": 1})
    assert rec.status == ExecutionStatus.COMPLETED and calls.write == 2  # different step ids -> different keys: both run once


def test_listener_receives_ordered_events_and_cannot_break_a_run(engine):
    events = []
    d = wf([step("a", "add", params={"a": 1}), step("skip", "add", when="false"), step("f", "flaky", params={"a": 1})])
    rec = engine.run(d, {"x": 1}, listener=events.append)
    assert [e["type"] for e in events if e["type"] != "condition"] == [
        "step_started", "step_completed", "step_skipped", "step_started", "step_retry", "step_completed", "run_finished"]
    assert events[-1]["status"] == "completed" and all(e["run_id"] == rec.run_id for e in events)

    def explode(_):
        raise RuntimeError("UI crashed")

    assert engine.run(d, {"x": 1}, listener=explode).status == ExecutionStatus.COMPLETED


def test_selection_info_and_input_sources_are_recorded(engine):
    from src.models import SelectionInfo
    rec = engine.run(wf([step("a", "add")]), {"x": 3}, request="do it", input_sources={"x": "request"},
                     selection=SelectionInfo(explanation="because", confidence=0.9, mode="llm"))
    assert rec.selection.explanation == "because" and rec.selection.mode == "llm" and rec.input_sources == {"x": "request"}


def test_preview_truncates_big_structures():
    p = preview({"rows": [{"i": i} for i in range(100)], "t": "x" * 1000})
    assert len(p["rows"]) == 6 and "more" in p["rows"][-1] and len(p["t"]) <= 240
    assert preview(5) == 5 and preview(None) is None
