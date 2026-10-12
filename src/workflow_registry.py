"""Workflow registry: loads the Excel catalog, merges it with the YAML specs,
validates every definition statically and exposes lookup for the router/engine.

Static validation (all reported as clear, per-workflow issues):

* Excel traceability - the spec's ``excel_step`` sequence equals the workbook's
  Steps cell, every Excel input is covered, every Excel tool is mapped.
* Tools - every referenced tool is registered, allowed for the workflow, and
  receives parameters that exist in its input schema.
* References - ``inputs.x`` is declared, ``steps.y`` is an earlier step and the
  field exists in that tool's output schema.
* Expressions - every condition/rule/template parses; branch targets exist and
  only jump forward; result templates reference steps that are guaranteed to
  have run.
"""

from __future__ import annotations

import logging
import re
from itertools import chain
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .condition_evaluator import (
    Expression,
    compile_expression,
    has_template,
    is_expression_string,
    template_expressions,
)
from .config import REQUIRED_WORKFLOW_IDS, Settings
from .errors import CatalogError, ExpressionError
from .excel_loader import load_workbook_catalog
from .models import (
    CatalogIssue,
    CatalogReport,
    ConditionInfo,
    ExcelCatalog,
    ExcelWorkflow,
    InputType,
    StepDef,
    TestRequest,
    WorkflowDefinition,
    WorkflowSpec,
)
from .tool_registry import ToolRegistry, ToolSpec, walk_params

log = logging.getLogger(__name__)

DEFAULT_ERROR_POLICY = [
    "Default failure policy: a failed step stops the workflow (status 'failed') and keeps every result collected so far.",
    "Transient tool errors (timeouts, outages) are retried up to 3 times with exponential back-off; validation errors are never retried.",
    "Steps marked `on_failure: continue` may fail without stopping the workflow; the run then finishes as 'partial'.",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower()).strip(" .")


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts) + (f" (+{len(exc.errors()) - 6} more)" if len(exc.errors()) > 6 else "")


def load_spec(path: Path) -> WorkflowSpec:
    """Parse one YAML spec file (raises :class:`CatalogError` with a readable message)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name} is not valid YAML: {exc}", code="spec_invalid_yaml") from exc
    except OSError as exc:
        raise CatalogError(f"{path.name} could not be read.", detail=str(exc), code="spec_unreadable") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"{path.name} must contain a YAML mapping.", code="spec_invalid")
    try:
        return WorkflowSpec.model_validate(raw)
    except ValidationError as exc:
        raise CatalogError(f"{path.name} is invalid: {format_validation_error(exc)}", code="spec_invalid") from exc


# ============================================================================
# Static validation
# ============================================================================
class _Validator:
    def __init__(self, excel: ExcelWorkflow, spec: WorkflowSpec, tools: ToolRegistry, fixtures_dir: Path):
        self.excel, self.spec, self.tools, self.fixtures_dir = excel, spec, tools, fixtures_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.step_index = {s.id: i for i, s in enumerate(spec.steps)}
        self.input_names = {i.name for i in spec.inputs}
        self.allowed = set(chain.from_iterable(spec.tool_map.values()))

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    # ----------------------------------------------------------------- run
    def run(self) -> None:
        self.check_traceability()
        self.check_inputs()
        self.check_steps()
        self.check_final()

    # --------------------------------------------------- Excel traceability
    def check_traceability(self) -> None:
        ex, sp = self.excel, self.spec
        if sp.workflow_id != ex.workflow_id:
            self.err(f"Spec workflow_id '{sp.workflow_id}' does not match the workbook ID '{ex.workflow_id}'.")
        # steps
        seq: list[str] = []
        for s in sp.steps:
            n = _norm(s.excel_step)
            if not seq or seq[-1] != n:
                seq.append(n)
        want = [_norm(x) for x in ex.steps]
        if seq != want:
            for i in range(max(len(seq), len(want))):
                a = seq[i] if i < len(seq) else "<missing>"
                b = want[i] if i < len(want) else "<missing>"
                if a != b:
                    self.err(
                        f"Excel step {i + 1} is '{ex.steps[i] if i < len(ex.steps) else '<none>'}' but the spec implements '{a}'. "
                        "The workbook is authoritative: update the spec's `excel_step` values (and steps) to match it."
                    )
                    break
        # inputs
        covered = {_norm(c) for i in sp.inputs for c in i.covers} | {_norm(c) for d in sp.data_sources for c in d.covers}
        for phrase in ex.inputs:
            if _norm(phrase) not in covered:
                self.err(f"Excel input '{phrase}' is not covered by any spec input or data source (`covers`).")
        excel_inputs = {_norm(p) for p in ex.inputs}
        for c in sorted(covered - excel_inputs):
            self.err(f"Spec claims to cover '{c}', which is not an input in the workbook.")
        # tools
        want_tools = {_norm(t): t for t in ex.tools_required}
        have_tools = {_norm(k): k for k in sp.tool_map}
        for n, t in want_tools.items():
            if n not in have_tools:
                self.err(f"Excel tool '{t}' is not mapped to a registered tool in `tool_map`.")
        for n, k in have_tools.items():
            if n not in want_tools:
                self.err(f"`tool_map` lists '{k}', which is not in the workbook's Tools_Required.")
        for phrase, names in sp.tool_map.items():
            if not names:
                self.err(f"`tool_map['{phrase}']` is empty.")
            for name in names:
                if not self.tools.has(name):
                    self.err(f"Tool '{name}' (for '{phrase}') is not registered.")
        if ex.decision_logic and not any(s.kind == "decision" or s.when or _has_rule_expression(s, self.tools) for s in sp.steps):
            self.warnings.append("The workbook has decision logic, but the spec contains no condition, decision step or row rule.")

    # ---------------------------------------------------------------- inputs
    def check_inputs(self) -> None:
        for ex in self.spec.examples:
            for att in ex.attachments:
                if not (Path(self.fixtures_dir) / att).exists():
                    self.err(f"Example '{ex.title}' attaches '{att}', which is not in the fixtures folder.")
        for pat in self.spec.routing_patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                self.err(f"routing pattern '{pat}' is not a valid regex: {exc}")
        names = [i.name for i in self.spec.inputs]
        for n in {n for n in names if names.count(n) > 1}:
            self.err(f"Input '{n}' is declared more than once.")
        for g in self.spec.input_groups:
            for n in g.any_of:
                if n not in self.input_names:
                    self.err(f"Input group refers to unknown input '{n}'.")
        for i in self.spec.inputs:
            if i.default is not None and i.type == InputType.CHOICE and i.default not in (i.choices or []):
                self.err(f"Input '{i.name}': default {i.default!r} is not one of its choices.")
            if i.default is not None and i.type in (InputType.NUMBER, InputType.INTEGER) and not isinstance(i.default, (int, float)):
                self.err(f"Input '{i.name}': default must be a number.")
            if i.type == InputType.FILE and i.default is not None and not isinstance(i.default, str):
                self.err(f"Input '{i.name}': default must be a file name.")

    # ----------------------------------------------------------------- steps
    def check_steps(self) -> None:
        ids = [s.id for s in self.spec.steps]
        for n in {n for n in ids if ids.count(n) > 1}:
            self.err(f"Step id '{n}' is used more than once.")
        for idx, step in enumerate(self.spec.steps):
            where = f"step '{step.id}'"
            if step.when:
                self.check_expr(step.when, where + " guard", idx, roots={"inputs", "steps"})
            if step.kind == "tool":
                self.check_tool_step(idx, step)
            else:
                self.check_decision(idx, step)

    def check_tool_step(self, idx: int, step: StepDef) -> None:
        where = f"step '{step.id}'"
        if not self.tools.has(step.tool):
            self.err(f"{where}: tool '{step.tool}' is not registered.")
            return
        tool = self.tools.get(step.tool)
        if step.tool not in self.allowed and not tool.core:
            self.err(f"{where}: tool '{step.tool}' is not permitted for this workflow (it is neither mapped from the workbook's Tools_Required nor a core utility).")
        fields = tool.input_fields()
        for name in step.params:
            if name not in fields:
                self.err(f"{where}: unknown parameter '{name}' for tool '{step.tool}' (accepted: {', '.join(fields)}).")
        for name, f in fields.items():
            if f.is_required() and name not in step.params:
                self.err(f"{where}: required parameter '{name}' of tool '{step.tool}' is missing.")
        allowed_row_roots = {"row"} | ({"vars"} if "vars" in fields else set())
        vars_given = step.params.get("vars") if isinstance(step.params.get("vars"), dict) else None
        for path, leaf in walk_params(step.params):
            if not isinstance(leaf, str):
                continue
            if tool.is_expression_param(path):
                self.check_expr(leaf, f"{where} param '{path}'", idx, roots=allowed_row_roots, vars_given=vars_given)
            elif is_expression_string(leaf):
                self.check_expr(leaf[1:], f"{where} param '{path}'", idx, roots={"inputs", "steps"})
            elif has_template(leaf):
                for e in template_expressions(leaf):
                    self.check_expr(e, f"{where} param '{path}'", idx, roots={"inputs", "steps"})
        for ens in step.ensure:
            self.check_expr(ens.expr, f"{where} ensure", idx, roots={"output", "inputs", "steps"}, own_tool=tool)
            self.check_template(ens.message, f"{where} ensure message", idx, roots={"output", "inputs", "steps"}, own_tool=tool)
        if step.retry and not tool.retry_allowed:
            self.err(f"{where}: retry is not allowed for the non-idempotent tool '{step.tool}'.")

    def check_decision(self, idx: int, step: StepDef) -> None:
        where = f"decision '{step.id}'"
        otherwise = [c for c in step.cases if c.when is None]
        if len(otherwise) > 1:
            self.err(f"{where}: more than one 'otherwise' case.")
        if otherwise and step.cases[-1].when is not None:
            self.err(f"{where}: the 'otherwise' case must be last.")
        for case in step.cases:
            if case.when:
                self.check_expr(case.when, f"{where} case '{case.label}'", idx, roots={"inputs", "steps"})
            a = case.then
            if a.message:
                self.check_template(a.message, f"{where} message", idx, roots={"inputs", "steps"})
            if a.type == "goto":
                if a.target not in self.step_index:
                    self.err(f"{where}: goto target '{a.target}' does not exist.")
                elif self.step_index[a.target] <= idx:
                    self.err(f"{where}: goto '{a.target}' jumps backwards; only forward jumps are allowed (no loops).")
            if a.type == "end" and a.outcome not in self.spec.final.outcomes:
                self.err(f"{where}: end outcome '{a.outcome}' is not defined in final.outcomes.")
            if a.type == "await_input":
                if isinstance(a.missing, list):
                    for n in a.missing:
                        if n not in self.input_names:
                            self.err(f"{where}: await_input asks for unknown input '{n}'.")
                elif is_expression_string(a.missing):
                    self.check_expr(a.missing[1:], f"{where} missing", idx, roots={"inputs", "steps"})
                else:
                    self.err(f"{where}: `missing` must be a list of input names or an '=expression'.")

    # ------------------------------------------------------------ expressions
    def check_expr(self, text: str, where: str, idx: int, *, roots: set[str], vars_given: dict | None = None,
                   own_tool: ToolSpec | None = None) -> Expression | None:
        try:
            expr = compile_expression(text)
        except ExpressionError as exc:
            self.err(f"{where}: invalid expression '{text}' - {exc.user_message}")
            return None
        for ref in expr.references():
            root = ref.root
            if root not in roots:
                self.err(f"{where}: '{ref.dotted}' is not available here (allowed: {', '.join(sorted(roots))}).")
                continue
            if root == "inputs" and len(ref.segments) > 1 and ref.segments[1] not in self.input_names:
                self.err(f"{where}: unknown input '{ref.segments[1]}'.")
            elif root == "steps":
                self.check_step_ref(ref.segments, where, idx)
            elif root == "output" and own_tool is not None and len(ref.segments) > 1:
                if ref.segments[1] not in own_tool.output_fields():
                    self.err(f"{where}: tool '{own_tool.name}' has no output field '{ref.segments[1]}'.")
            elif root == "vars" and vars_given is not None and len(ref.segments) > 1:
                if ref.segments[1] not in vars_given:
                    self.err(f"{where}: 'vars.{ref.segments[1]}' is used but not defined in this step's `vars`.")
        return expr

    def check_step_ref(self, segments: tuple, where: str, idx: int, *, any_order: bool = False, limit: int | None = None) -> None:
        if len(segments) < 2:
            self.err(f"{where}: 'steps' must be followed by a step id.")
            return
        sid = segments[1]
        if sid not in self.step_index:
            self.err(f"{where}: refers to unknown step '{sid}'.")
            return
        pos = self.step_index[sid]
        if not any_order and pos >= idx:
            self.err(f"{where}: refers to step '{sid}', which does not run before it.")
        if limit is not None and pos >= limit:
            self.err(f"{where}: refers to step '{sid}', which has not run when this outcome is produced.")
        ref_step = self.spec.steps[pos]
        if ref_step.kind == "decision":
            self.err(f"{where}: step '{sid}' is a decision and has no output.")
        elif len(segments) > 2 and self.tools.has(ref_step.tool or ""):
            fields = self.tools.get(ref_step.tool).output_fields()
            if segments[2] not in fields:
                self.err(f"{where}: step '{sid}' (tool '{ref_step.tool}') has no output field '{segments[2]}' (available: {', '.join(sorted(fields))}).")

    def check_template(self, text: str, where: str, idx: int, *, roots: set[str], own_tool: ToolSpec | None = None) -> None:
        for e in template_expressions(text):
            self.check_expr(e, where, idx, roots=roots, own_tool=own_tool)

    # ------------------------------------------------------------------ final
    def check_final(self) -> None:
        final = self.spec.final
        n = len(self.spec.steps)
        # an outcome other than 'completed' may only use steps before the earliest `end` that produces it
        limit: dict[str, int] = {}
        for i, s in enumerate(self.spec.steps):
            for c in s.cases:
                if c.then.type == "end":
                    limit[c.then.outcome] = min(limit.get(c.then.outcome, n), i)
        for name, out in final.outcomes.items():
            where = f"final outcome '{name}'"
            if name != "completed" and name not in limit:
                self.warnings.append(f"Final outcome '{name}' is never produced by an `end` action.")
            lim = limit.get(name) if name != "completed" else None
            self._check_final_value(out.headline, where + " headline", lim, as_template=True)
            for key, val in out.result.items():
                self._check_final_value(val, f"{where} result '{key}'", lim)
        for w in final.warnings:
            self.check_expr(w.when, "final warning condition", n, roots={"inputs", "steps"})
            self._check_final_value(w.message, "final warning message", None, as_template=True)

    def _check_final_value(self, value: Any, where: str, limit: int | None, *, as_template: bool = False) -> None:
        n = len(self.spec.steps)
        for _, leaf in walk_params(value) if not isinstance(value, str) else [("", value)]:
            if not isinstance(leaf, str):
                continue
            exprs: list[str] = []
            if is_expression_string(leaf):
                exprs = [leaf[1:]]
            elif has_template(leaf):
                exprs = template_expressions(leaf)
            for e in exprs:
                expr = self.check_expr(e, where, n, roots={"inputs", "steps"})
                if expr is None:
                    continue
                for ref in expr.references():
                    if ref.root == "steps" and limit is not None:
                        self.check_step_ref(ref.segments, where, n, any_order=True, limit=limit)


def _has_rule_expression(step: StepDef, tools: ToolRegistry) -> bool:
    if step.kind != "tool" or not tools.has(step.tool or ""):
        return False
    tool = tools.get(step.tool)
    return any(tool.is_expression_param(p) for p, _ in walk_params(step.params))


def _conditions_of(spec: WorkflowSpec, tools: ToolRegistry) -> list[ConditionInfo]:
    out: list[ConditionInfo] = []
    for s in spec.steps:
        if s.when:
            out.append(ConditionInfo(step_id=s.id, kind="guard", label=f"Run '{s.name}' only if", expression=s.when))
        if s.kind == "decision":
            for c in s.cases:
                out.append(ConditionInfo(step_id=s.id, kind="decision_case", label=c.label, expression=c.when, action=c.then.type))
        elif tools.has(s.tool or ""):
            tool = tools.get(s.tool)
            for path, leaf in walk_params(s.params):
                if isinstance(leaf, str) and tool.is_expression_param(path) and not path.startswith("derive") :
                    out.append(ConditionInfo(step_id=s.id, kind="row_rule", label=str(s.params.get("label") or s.name), expression=leaf))
            for e in s.ensure:
                out.append(ConditionInfo(step_id=s.id, kind="postcondition", label="Result must satisfy", expression=e.expr))
    return out


# ============================================================================
# Registry
# ============================================================================
def build_definition(excel: ExcelWorkflow, spec: WorkflowSpec, tests: list[TestRequest], tools: ToolRegistry,
                     *, is_extension: bool, source: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=excel.workflow_id,
        name=excel.name,
        description=f"{excel.trigger}. Result: {excel.expected_output}" if excel.trigger else excel.expected_output,
        triggers=[t for t in [excel.trigger, *spec.routing_examples] if t],
        routing_examples=spec.routing_examples,
        routing_keywords=spec.routing_keywords,
        routing_patterns=spec.routing_patterns,
        examples=spec.examples,
        inputs=spec.inputs,
        input_groups=spec.input_groups,
        data_sources=spec.data_sources,
        steps=spec.steps,
        decision_logic=excel.decision_logic,
        tools_required=excel.tools_required,
        tool_map=spec.tool_map,
        allowed_tools=sorted(set(chain.from_iterable(spec.tool_map.values()))),
        success_output=excel.expected_output,
        final=spec.final,
        error_handling=[*spec.error_handling_notes, *DEFAULT_ERROR_POLICY],
        interpretation_notes=spec.interpretation_notes,
        test_requests=tests,
        conditions=_conditions_of(spec, tools),
        excel_inputs=excel.inputs,
        excel_steps=excel.steps,
        enabled=spec.enabled,
        is_extension=is_extension,
        source=source,
    )


class WorkflowRegistry:
    """Catalog of validated workflow definitions."""

    def __init__(self, settings: Settings, tools: ToolRegistry, *, required_ids: tuple[str, ...] = REQUIRED_WORKFLOW_IDS):
        self.settings = settings
        self.tools = tools
        self.required_ids = required_ids
        self._defs: dict[str, WorkflowDefinition] = {}
        self._excel: list[ExcelCatalog] = []
        self.report: CatalogReport | None = None

    # ------------------------------------------------------------------ load
    def load(self) -> CatalogReport:
        """(Re)load every workbook + spec. Safe to call repeatedly (hot reload)."""
        defs: dict[str, WorkflowDefinition] = {}
        issues: list[CatalogIssue] = []
        self._excel = []
        sources: list[tuple[Path, Path, bool]] = [(self.settings.workbook_path, self.settings.specs_dir, False)]
        for d in self.settings.extra_catalog_dirs:
            books = sorted(Path(d).glob("*.xlsx")) if Path(d).is_dir() else []
            if not books:
                issues.append(CatalogIssue(severity="warning", workflow_id=None, message=f"Extra catalog folder '{d}' has no .xlsx workbook."))
            sources.extend((b, Path(d) / "specs", True) for b in books)

        sheets, sha, labels = [], "", []
        for workbook, specs_dir, is_ext in sources:
            labels.append(str(workbook))
            try:
                excel = load_workbook_catalog(workbook)
            except CatalogError as exc:
                issues.append(CatalogIssue(severity="error", workflow_id=None, message=exc.user_message))
                continue
            self._excel.append(excel)
            if not is_ext:
                sheets, sha = excel.sheets, excel.sha256
            for w in excel.warnings:
                issues.append(CatalogIssue(severity="warning", workflow_id=None, message=w))
            self._ingest(excel, specs_dir, is_ext, defs, issues)

        loaded = list(defs)
        ext_ids = [i for i, d in defs.items() if d.is_extension]
        missing = [i for i in self.required_ids if i not in defs]
        invalid = sorted({i.workflow_id for i in issues if i.severity == "error" and i.workflow_id})
        for m in missing:
            if not any(i.workflow_id == m and i.severity == "error" for i in issues):
                issues.append(CatalogIssue(severity="error", workflow_id=m, message=f"Required workflow {m} is not in the workbook."))
        self._defs = defs
        required_invalid = [i for i in invalid if i in self.required_ids]
        self.report = CatalogReport(
            ok=not missing and not required_invalid,
            loaded_ids=loaded, expected_ids=list(self.required_ids), missing_ids=missing,
            invalid_ids=invalid, extension_ids=ext_ids, issues=issues, sources=labels, sheets=sheets, workbook_sha256=sha,
        )
        level = logging.INFO if self.report.ok else logging.WARNING
        log.log(level, "Workflow catalog: %s", self.report.status_label)
        return self.report

    reload = load

    def _ingest(self, excel: ExcelCatalog, specs_dir: Path, is_ext: bool, defs: dict[str, WorkflowDefinition],
                issues: list[CatalogIssue]) -> None:
        tests: dict[str, list[TestRequest]] = {}
        for t in excel.test_questions:
            tests.setdefault(t.workflow_id, []).append(TestRequest(request=t.request, what_to_check=t.what_to_check))
        in_book = [w.workflow_id for w in excel.workflows]
        known = set(tests) - set(in_book)
        for k in sorted(known):
            issues.append(CatalogIssue(severity="warning", workflow_id=k, message=f"Test question refers to {k}, which is not a workflow in the workbook."))
        for wf in excel.workflows:
            wid = wf.workflow_id
            fail = lambda msg: issues.append(CatalogIssue(severity="error", workflow_id=wid, message=msg))
            if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", wid):
                fail(f"Invalid workflow ID '{wid}' (row {wf.row}).")
                continue
            if in_book.count(wid) > 1:
                fail(f"Duplicate workflow ID {wid} appears {in_book.count(wid)} times in the workbook (row {wf.row}); all copies are rejected so the wrong one can never run silently.")
                continue
            if wid in defs:
                fail(f"Duplicate workflow ID {wid}: already registered from another source ('{defs[wid].source}'); this one is rejected.")
                continue
            if not wf.name:
                fail(f"{wid}: Workflow_Name is empty (row {wf.row}).")
                continue
            if not wf.steps:
                fail(f"{wid}: no steps found in the Steps column (row {wf.row}).")
                continue
            spec_path = specs_dir / f"{wid}.yaml"
            if not spec_path.exists():
                fail(f"{wid}: no execution spec found at {spec_path}.")
                continue
            try:
                spec = load_spec(spec_path)
            except CatalogError as exc:
                fail(f"{wid}: {exc.user_message}")
                continue
            v = _Validator(wf, spec, self.tools, self.settings.fixtures_dir)
            v.run()
            for m in v.warnings:
                issues.append(CatalogIssue(severity="warning", workflow_id=wid, message=m))
            if v.errors:
                for m in v.errors:
                    fail(m)
                continue
            try:
                defs[wid] = build_definition(wf, spec, tests.get(wid, []), self.tools, is_extension=is_ext,
                                             source=f"{Path(excel.path).name} / {wf.sheet} row {wf.row}")
            except ValidationError as exc:
                fail(f"{wid}: could not build the definition - {format_validation_error(exc)}")

    # ---------------------------------------------------------------- lookup
    def _require_loaded(self) -> None:
        if self.report is None:
            self.load()

    def has(self, workflow_id: str) -> bool:
        self._require_loaded()
        return workflow_id in self._defs

    def get(self, workflow_id: str) -> WorkflowDefinition:
        self._require_loaded()
        try:
            return self._defs[workflow_id]
        except (KeyError, TypeError):
            raise CatalogError(f"Unknown workflow '{workflow_id}'.", code="unknown_workflow") from None

    def all(self, *, include_disabled: bool = False) -> list[WorkflowDefinition]:
        self._require_loaded()
        return [d for d in self._defs.values() if include_disabled or d.enabled]

    def ids(self) -> list[str]:
        return [d.id for d in self.all()]

    def router_catalog(self) -> list[dict[str, Any]]:
        """What the router is allowed to see: id, name, description and how to recognise the workflow."""
        return [
            {
                "id": d.id, "name": d.name, "description": d.description, "triggers": d.triggers,
                "keywords": d.routing_keywords,
                "required_inputs": [i.name for i in d.required_inputs],
                "inputs": [{"name": i.name, "description": i.description, "required": i.required} for i in d.exposed_inputs],
            }
            for d in self.all()
        ]

    def normalized_catalog(self) -> dict[str, Any]:
        """The whole validated catalog as plain JSON-able data (written to data/normalized_workflows.json)."""
        self._require_loaded()
        return {
            "generated_from": [Path(p).name for p in self.report.sources],
            "workbook_sha256": self.report.workbook_sha256,
            "required_workflow_ids": list(self.required_ids),
            "workflows": [d.model_dump(mode="json") for d in self._defs.values() if not d.is_extension],
        }

    def excel_catalogs(self) -> list[ExcelCatalog]:
        self._require_loaded()
        return self._excel
