"""Generic workflow execution engine.

One interpreter executes *any* :class:`WorkflowDefinition`; there is no
workflow-specific Python. For each step it

1. evaluates the optional guard (``when``) and skips the step if false,
2. resolves parameters from the validated inputs and earlier step outputs,
3. resolves the tool through the :class:`ToolRegistry` (which validates the
   parameters before and the output after the call),
4. retries *transient* tool errors only (bounded, with back-off), never
   validation errors and never non-idempotent side effects,
5. checks post-conditions (``ensure``) and stores the output in the context,
6. for decision steps evaluates the cases in order and applies the first
   matching action (continue / goto / end / await_input / fail),
7. applies the failure policy (stop with ``failed`` or continue as ``partial``).

Everything that happens is written to an :class:`ExecutionRecord`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Callable

from .condition_evaluator import (
    compile_expression,
    explain_values,
    has_template,
    is_expression_string,
    render_template,
)
from .config import Settings
from .errors import (
    ErrorCategory,
    ExpressionError,
    TransientToolError,
    WorkflowAgentError,
)
from .logging_utils import log_exception, redact
from .models import (
    Action,
    AttemptRecord,
    AwaitingInput,
    ConditionEvaluation,
    ErrorRecord,
    ExecutionRecord,
    ExecutionStatus,
    FinalResult,
    RetryPolicy,
    SelectionInfo,
    StepDef,
    StepRecord,
    StepStatus,
    ToolUsage,
    WorkflowDefinition,
    utcnow,
)
from .tool_registry import FaultInjector, ToolContext, ToolRegistry, ToolSpec
from .tools.data_access import DataAccess

log = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any]], None]
DEFAULT_RETRY = RetryPolicy()
MAX_STEPS_PER_RUN = 500


# ----------------------------------------------------------------- previews
def preview(value: Any, *, items: int = 5, depth: int = 0, text_limit: int = 240) -> Any:
    """Compact, JSON-friendly rendering of a value for the trace."""
    if isinstance(value, str):
        return value if len(value) <= text_limit else value[: text_limit - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        if depth >= 3:
            return f"{{{len(value)} fields}}"
        return {str(k): preview(v, items=items, depth=depth + 1, text_limit=text_limit) for k, v in list(value.items())[:25]}
    if isinstance(value, (list, tuple)):
        head = [preview(v, items=items, depth=depth + 1, text_limit=text_limit) for v in value[:items]]
        if len(value) > items:
            head.append(f"… {len(value) - items} more (total {len(value)})")
        return head
    return str(value)[:text_limit]


def summarise_output(out: dict[str, Any]) -> str:
    parts = []
    for k, v in out.items():
        if isinstance(v, list):
            parts.append(f"{k}: {len(v)} item(s)")
        elif isinstance(v, dict):
            parts.append(f"{k}: {len(v)} field(s)")
        elif isinstance(v, str):
            parts.append(f"{k}: {v[:40] + '…' if len(v) > 40 else v}")
        else:
            parts.append(f"{k}: {v}")
        if len(parts) >= 6:
            parts.append("…")
            break
    return " · ".join(parts)


def strip_private(value: Any) -> Any:
    """Remove internal ``_``-prefixed bookkeeping keys (e.g. ``_source_row``) from user-facing results."""
    if isinstance(value, dict):
        return {k: strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [strip_private(v) for v in value]
    return value


class _Stop(Exception):
    """Internal: stop the run loop."""


class ExecutionEngine:
    def __init__(
        self,
        tools: ToolRegistry,
        settings: Settings,
        *,
        llm: Any | None = None,
        faults: FaultInjector | None = None,
        data: DataAccess | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.tools = tools
        self.settings = settings
        self.llm = llm
        self.faults = faults or FaultInjector()
        self.data = data or DataAccess(settings)
        self.sleep = sleep

    # ================================================================== run
    def run(
        self,
        definition: WorkflowDefinition,
        inputs: dict[str, Any],
        *,
        request: str = "",
        input_sources: dict[str, str] | None = None,
        selection: SelectionInfo | None = None,
        listener: Listener | None = None,
        run_id: str | None = None,
    ) -> ExecutionRecord:
        run_id = run_id or uuid.uuid4().hex[:12]
        record = ExecutionRecord(
            run_id=run_id,
            request=request,
            workflow_id=definition.id,
            workflow_name=definition.name,
            selection=selection or SelectionInfo(),
            inputs=dict(inputs),
            input_sources=dict(input_sources or {}),
            steps=[
                StepRecord(
                    step_id=s.id, name=s.name, description=s.description, excel_step=s.excel_step, kind=s.kind,
                    tool=s.tool, on_failure=s.on_failure,
                    **self._tool_meta(s),
                )
                for s in definition.steps
            ],
        )
        run = _Run(self, definition, record, listener)
        started = time.perf_counter()
        try:
            run.execute()
        except _Stop:
            pass
        except Exception as exc:  # engine bug - never surface as success
            log_exception(log, "Unexpected engine error in %s", definition.id)
            record.status = ExecutionStatus.FAILED
            record.errors.append(ErrorRecord(
                code="engine_internal_error", category=ErrorCategory.TERMINAL,
                message="An unexpected internal error stopped the workflow. Details were written to the log.",
            ))
            log.error("Detail: %s", redact(f"{type(exc).__name__}: {exc}"))
        run.finish()
        record.finished_at = utcnow()
        record.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        run.emit("run_finished", status=record.status.value)
        return record

    def _tool_meta(self, step: StepDef) -> dict[str, Any]:
        if step.kind == "tool" and step.tool and self.tools.has(step.tool):
            spec = self.tools.get(step.tool)
            return {"tool_kind": spec.kind, "simulated": spec.simulated}
        return {}


class _Run:
    """State of a single execution."""

    def __init__(self, engine: ExecutionEngine, definition: WorkflowDefinition, record: ExecutionRecord, listener: Listener | None):
        self.engine, self.definition, self.record, self.listener = engine, definition, record, listener
        self.scope: dict[str, Any] = {"inputs": record.inputs, "steps": {}}
        self.outcome = "completed"
        self.end_message: str | None = None
        self.cond_id = 0
        self.index = {s.id: i for i, s in enumerate(definition.steps)}
        self.effects: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, ToolUsage] = {}

    # --------------------------------------------------------------- events
    def emit(self, type_: str, **data: Any) -> None:
        if self.listener:
            try:
                self.listener({"type": type_, "run_id": self.record.run_id, **data})
            except Exception:  # a UI callback must never break a run
                log.exception("listener failed")

    def add_condition(self, **kw: Any) -> ConditionEvaluation:
        self.cond_id += 1
        cond = ConditionEvaluation(id=self.cond_id, **kw)
        self.record.conditions.append(cond)
        self.emit("condition", condition=cond.model_dump(mode="json"))
        return cond

    # ----------------------------------------------------------------- loop
    def execute(self) -> None:
        steps = self.definition.steps
        i = 0
        executed = 0
        while i < len(steps):
            executed += 1
            if executed > MAX_STEPS_PER_RUN:  # pragma: no cover - forward-only jumps make this unreachable
                raise _Stop
            step, srec = steps[i], self.record.steps[i]
            nxt = steps[i + 1].id if i + 1 < len(steps) else None
            if step.when and not self.guard_passes(step, srec, nxt):
                i += 1
                continue
            self.emit("step_started", step_id=step.id, name=step.name)
            srec.status, srec.started_at = StepStatus.RUNNING, utcnow()
            t0 = time.perf_counter()
            try:
                i = self.run_decision(i, step, srec, nxt) if step.kind == "decision" else self.run_tool(i, step, srec, nxt)
            finally:
                srec.duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    # ---------------------------------------------------------------- guard
    def guard_passes(self, step: StepDef, srec: StepRecord, nxt: str | None) -> bool:
        try:
            result = compile_expression(step.when).evaluate_bool(self.scope)
        except ExpressionError as exc:
            self.add_condition(step_id=step.id, kind="guard", label=f"Run '{step.name}' only if", expression=step.when,
                               error=exc.user_message, outcome="Condition could not be evaluated", next_step=None)
            self.fail_step(step, srec, ErrorRecord(
                code=exc.code, category=ErrorCategory.TERMINAL, step_id=step.id,
                message=f"The condition for step '{step.name}' could not be evaluated: {exc.user_message}"))
            if step.on_failure == "fail":
                raise _Stop
            return False
        self.add_condition(
            step_id=step.id, kind="guard", label=f"Run '{step.name}' only if", expression=step.when,
            values=explain_values(step.when, self.scope), result=result,
            outcome="Step runs" if result else "Step skipped", next_step=step.id if result else nxt,
        )
        if not result:
            srec.status, srec.skipped_reason, srec.next_step = StepStatus.SKIPPED, f"Condition false: {step.when}", nxt
            self.emit("step_skipped", step_id=step.id, reason=srec.skipped_reason)
        return result

    # ----------------------------------------------------------- tool steps
    def run_tool(self, i: int, step: StepDef, srec: StepRecord, nxt: str | None) -> int:
        tool = self.engine.tools.get(step.tool)
        usage = self.usage.setdefault(step.tool, ToolUsage(tool=step.tool, kind=tool.kind, simulated=tool.simulated))
        try:
            params = self.resolve(step.params, "", tool)
        except ExpressionError as exc:
            self.fail_step(step, srec, ErrorRecord(
                code=exc.code, category=ErrorCategory.TERMINAL, step_id=step.id,
                message=f"Step '{step.name}' could not prepare its input: {exc.user_message}"))
            return self.after_failure(i, step)
        srec.params = preview(params, items=3)
        policy = step.retry or DEFAULT_RETRY
        key = self.effect_key(step, params) if tool.side_effect else None
        attempt = 0
        while True:
            attempt += 1
            ctx = ToolContext(
                settings=self.engine.settings, data=self.engine.data, faults=self.engine.faults, llm=self.engine.llm,
                run_id=self.record.run_id, step_id=step.id, attempt=attempt,
                condition_sink=lambda d, sid=step.id: self.row_condition(sid, d),
            )
            try:
                if key and key in self.effects:
                    out = self.effects[key]
                    srec.note = "Duplicate side effect suppressed: this exact action already ran in this execution."
                else:
                    usage.calls += 1
                    out = self.engine.tools.execute(step.tool, params, ctx)
                    if key:
                        self.effects[key] = out
                break
            except TransientToolError as exc:
                usage.failures += 1
                att = AttemptRecord(attempt=attempt, error_code=exc.code, message=exc.user_message, category=exc.category)
                if attempt < policy.max_attempts and tool.retry_allowed:
                    delay = policy.backoff_seconds * (policy.backoff_factor ** (attempt - 1)) * self.engine.settings.retry_backoff_scale
                    att.delay_seconds = round(delay, 3)
                    srec.attempts.append(att)
                    self.record.recovery_attempts.append(att)
                    self.emit("step_retry", step_id=step.id, attempt=attempt, message=exc.user_message, delay=att.delay_seconds)
                    if delay > 0:
                        self.engine.sleep(delay)
                    continue
                srec.attempts.append(att)
                self.record.recovery_attempts.append(att)
                self.fail_step(step, srec, ErrorRecord(
                    code=exc.code, category=ErrorCategory.RECOVERABLE, step_id=step.id, retryable=True,
                    message=f"{exc.user_message} (gave up after {attempt} attempt{'s' if attempt != 1 else ''})."))
                log.warning("step %s failed after %d attempts: %s", step.id, attempt, redact(exc.detail or exc.user_message))
                return self.after_failure(i, step)
            except WorkflowAgentError as exc:
                usage.failures += 1
                self.fail_step(step, srec, ErrorRecord(code=exc.code, category=exc.category, step_id=step.id, message=exc.user_message))
                log.info("step %s failed: %s | %s", step.id, exc.code, redact(exc.detail or exc.user_message))
                return self.after_failure(i, step)
            except Exception as exc:  # a bug inside a tool: report, do not crash, never claim success
                usage.failures += 1
                log_exception(log, "unexpected error in tool %s", step.tool)
                self.fail_step(step, srec, ErrorRecord(
                    code="tool_internal_error", category=ErrorCategory.TERMINAL, step_id=step.id,
                    message=f"Tool '{step.tool}' failed unexpectedly. Details were written to the log."))
                log.error("detail: %s", redact(f"{type(exc).__name__}: {exc}"))
                return self.after_failure(i, step)

        if srec.attempts:  # succeeded after at least one retry
            for a in srec.attempts:
                self.record.errors.append(ErrorRecord(code=a.error_code, category=a.category, message=a.message,
                                                      step_id=step.id, retryable=True, recovered=True))
            srec.note = (srec.note + " " if srec.note else "") + f"Recovered after {len(srec.attempts)} failed attempt(s)."
        if ctx.notes:
            srec.note = (srec.note + " " if srec.note else "") + " ".join(ctx.notes)
        srec.output_summary = summarise_output(out)
        srec.output_preview = preview(out)
        if out.get("mode") == "llm":
            self.record.llm_used_in_tools = True
        # post-conditions
        for ens in step.ensure:
            local = {**self.scope, "output": out}
            try:
                ok = compile_expression(ens.expr).evaluate_bool(local)
                msg = render_template(ens.message, local) if not ok else ""
            except ExpressionError as exc:
                ok, msg = False, f"Could not verify the result of step '{step.name}': {exc.user_message}"
            self.add_condition(step_id=step.id, kind="postcondition", label="Result must satisfy", expression=ens.expr,
                               values=explain_values(ens.expr, local), result=ok,
                               outcome="Passed" if ok else f"Failed: {msg}", next_step=nxt if ok else None)
            if not ok:
                self.fail_step(step, srec, ErrorRecord(code="postcondition_failed", category=ens.category, step_id=step.id, message=msg))
                return self.after_failure(i, step)
        self.scope["steps"][step.id] = out
        srec.status, srec.next_step = StepStatus.COMPLETED, nxt
        self.emit("step_completed", step_id=step.id, summary=srec.output_summary)
        return i + 1

    def row_condition(self, step_id: str, d: dict[str, Any]) -> None:
        """Condition evaluations reported by tools that apply a rule to every row."""
        matched, evaluated = d.get("rows_matched"), d.get("rows_evaluated")
        if matched is not None and evaluated is not None:
            outcome = f"{matched} of {evaluated} row(s) matched"
        elif evaluated is not None:
            outcome = f"{evaluated} row(s) evaluated"
        else:
            outcome = ""
        if d.get("error"):
            outcome += f" - {d['error']}"
        nxt = self.engine_next(step_id)
        self.add_condition(
            step_id=step_id, kind="row_rule", label=str(d.get("label") or "Row rule"), expression=d.get("expression"),
            outcome=outcome.strip(" -"), rows_evaluated=evaluated, rows_matched=matched, samples=d.get("samples") or [],
            error=d.get("error"), next_step=nxt,
        )

    def engine_next(self, step_id: str) -> str | None:
        i = self.index[step_id]
        return self.definition.steps[i + 1].id if i + 1 < len(self.definition.steps) else None

    def effect_key(self, step: StepDef, params: dict[str, Any]) -> str:
        blob = json.dumps({"s": step.id, "p": params}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    # -------------------------------------------------------------- decisions
    def run_decision(self, i: int, step: StepDef, srec: StepRecord, nxt: str | None) -> int:
        for case in step.cases:
            if case.when is None:
                result, values = None, {}
            else:
                try:
                    result = compile_expression(case.when).evaluate_bool(self.scope)
                    values = explain_values(case.when, self.scope)
                except ExpressionError as exc:
                    self.add_condition(step_id=step.id, kind="decision", label=case.label, expression=case.when,
                                       error=exc.user_message, outcome="Condition could not be evaluated")
                    self.fail_step(step, srec, ErrorRecord(
                        code=exc.code, category=ErrorCategory.TERMINAL, step_id=step.id,
                        message=f"The decision '{step.name}' could not be evaluated: {exc.user_message}"))
                    raise _Stop
            if result is False:
                self.add_condition(step_id=step.id, kind="decision", label=case.label, expression=case.when,
                                   values=values, result=False, outcome="Not taken")
                continue
            action = case.then
            try:
                message = render_template(action.message, self.scope) if action.message else None
            except ExpressionError as exc:
                message = f"(message unavailable: {exc.user_message})"
            return self.apply_action(i, step, srec, case.label, case.when, values, result, action, message, nxt)
        # no case matched: simply continue
        srec.status, srec.next_step = StepStatus.COMPLETED, nxt
        srec.note = "No case matched; continued."
        self.emit("step_completed", step_id=step.id, summary=srec.note)
        return i + 1

    def apply_action(self, i, step, srec, label, expr, values, result, action: Action, message, nxt) -> int:
        if action.type == "continue":
            self.add_condition(step_id=step.id, kind="decision", label=label, expression=expr, values=values, result=result,
                               outcome=message or "Continue", next_step=nxt)
            srec.status, srec.next_step, srec.note = StepStatus.COMPLETED, nxt, message or label
            self.emit("step_completed", step_id=step.id, summary=srec.note)
            return i + 1
        if action.type == "goto":
            target = self.index[action.target]
            self.add_condition(step_id=step.id, kind="decision", label=label, expression=expr, values=values, result=result,
                               outcome=message or f"Go to '{action.target}'", next_step=action.target)
            srec.status, srec.next_step, srec.note = StepStatus.COMPLETED, action.target, message or label
            for j in range(i + 1, target):
                other = self.record.steps[j]
                other.status, other.skipped_reason = StepStatus.SKIPPED, f"Bypassed by branch '{label}'"
                self.emit("step_skipped", step_id=other.step_id, reason=other.skipped_reason)
            self.emit("step_completed", step_id=step.id, summary=srec.note)
            return target
        if action.type == "end":
            self.add_condition(step_id=step.id, kind="decision", label=label, expression=expr, values=values, result=result,
                               outcome=f"End workflow ({action.outcome}): {message or ''}".strip(": "), next_step=None)
            srec.status, srec.note = StepStatus.COMPLETED, message or label
            self.outcome, self.end_message = action.outcome, message
            self.emit("step_completed", step_id=step.id, summary=srec.note)
            raise _Stop
        if action.type == "await_input":
            missing = action.missing
            if isinstance(missing, str):
                try:
                    missing = list(compile_expression(missing[1:]).evaluate(self.scope))
                except ExpressionError:
                    missing = []
            self.add_condition(step_id=step.id, kind="decision", label=label, expression=expr, values=values, result=result,
                               outcome=f"Ask the user: {message}", next_step=None)
            srec.status, srec.note = StepStatus.AWAITING_INPUT, message
            self.record.status = ExecutionStatus.AWAITING_INPUT
            self.record.awaiting = AwaitingInput(message=message or "More information is needed.", missing=list(missing), step_id=step.id)
            self.emit("step_awaiting_input", step_id=step.id, message=message)
            raise _Stop
        # fail
        self.add_condition(step_id=step.id, kind="decision", label=label, expression=expr, values=values, result=result,
                           outcome=f"Stop with an error: {message}", next_step=None)
        self.fail_step(step, srec, ErrorRecord(code="decision_fail", category=action.category, step_id=step.id,
                                               message=message or label))
        raise _Stop

    # --------------------------------------------------------------- failures
    def fail_step(self, step: StepDef, srec: StepRecord, error: ErrorRecord) -> None:
        srec.status, srec.error = StepStatus.FAILED, error
        self.record.errors.append(error)
        self.emit("step_failed", step_id=step.id, error=error.model_dump(mode="json"))

    def after_failure(self, i: int, step: StepDef) -> int:
        if step.on_failure == "continue":
            self.record.steps[i].next_step = self.engine_next(step.id)
            return i + 1
        raise _Stop

    # ------------------------------------------------------------- resolution
    def resolve(self, value: Any, path: str, tool: ToolSpec) -> Any:
        if tool.is_expression_param(path):
            return value  # row expression: evaluated by the tool, per row
        if isinstance(value, dict):
            return {k: self.resolve(v, f"{path}.{k}" if path else str(k), tool) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, f"{path}.{n}" if path else str(n), tool) for n, v in enumerate(value)]
        if isinstance(value, str):
            if is_expression_string(value):
                return compile_expression(value[1:]).evaluate(self.scope)
            if has_template(value):
                return render_template(value, self.scope)
        return value

    def resolve_final(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self.resolve_final(v) for k, v in value.items() if not str(k).startswith("_")}
        if isinstance(value, list):
            return [self.resolve_final(v) for v in value]
        if isinstance(value, str):
            if is_expression_string(value):
                return strip_private(compile_expression(value[1:]).evaluate(self.scope))
            if has_template(value):
                return render_template(value, self.scope)
        return value

    # ----------------------------------------------------------------- finish
    def finish(self) -> None:
        rec = self.record
        rec.tools_used = list(self.usage.values())
        rec.partial_results = {sid: preview(out, items=3) for sid, out in self.scope["steps"].items()}
        failed = [s for s in rec.steps if s.status == StepStatus.FAILED]
        if rec.status == ExecutionStatus.AWAITING_INPUT:
            return
        if rec.status == ExecutionStatus.FAILED:  # engine-level failure already recorded
            return
        if failed and any(s.on_failure == "fail" for s in failed):
            rec.status = ExecutionStatus.FAILED
            return
        try:
            rec.final_result = self.build_final(failed)
        except ExpressionError as exc:
            rec.status = ExecutionStatus.FAILED
            rec.errors.append(ErrorRecord(
                code="final_result_error", category=ErrorCategory.TERMINAL,
                message=f"The workflow ran, but its result could not be assembled: {exc.user_message}"))
            return
        rec.status = ExecutionStatus.PARTIAL if failed else ExecutionStatus.COMPLETED

    def build_final(self, failed: list[StepRecord]) -> FinalResult:
        spec = self.definition.final
        out = spec.outcomes[self.outcome]
        data = {k: self.resolve_final(v) for k, v in out.result.items()}
        if self.end_message and self.outcome != "completed":
            data = {"reason": self.end_message, **data}
        warnings: list[str] = []
        for s in failed:
            warnings.append(f"Step '{s.name}' failed ({s.error.message if s.error else 'error'}) and the workflow continued without it.")
        for rule in spec.warnings:
            try:
                fired = compile_expression(rule.when).evaluate_bool(self.scope)
            except ExpressionError:
                continue  # a warning rule that cannot be evaluated (step not run) simply does not fire
            if fired:
                warnings.append(render_template(rule.message, self.scope))
        return FinalResult(outcome=self.outcome, severity=out.severity,
                           headline=render_template(out.headline, self.scope), data=data, warnings=warnings)
