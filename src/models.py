"""Pydantic schemas: Excel rows, workflow specs, normalized definitions,
execution records and agent responses.

Layers
------
1. ``ExcelWorkflow``      raw business definition read from the workbook
2. ``WorkflowSpec``       executable interpretation (YAML) of one Excel workflow
3. ``WorkflowDefinition`` the validated, normalized merge of 1 + 2 (what the
                          registry stores and the engine executes)
4. ``ExecutionRecord``    everything that happened during one run
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ErrorCategory

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# =============================================================================
# 1. Excel layer
# =============================================================================
class ExcelWorkflow(BaseModel):
    """One workflow row exactly as written in the workbook (plus provenance)."""

    workflow_id: str
    name: str
    trigger: str = ""
    inputs_text: str = ""
    inputs: list[str] = Field(default_factory=list)
    steps_text: str = ""
    steps: list[str] = Field(default_factory=list)
    decision_logic: str = ""
    tools_text: str = ""
    tools_required: list[str] = Field(default_factory=list)
    expected_output: str = ""
    sheet: str = ""
    row: int = 0
    extra: dict[str, str] = Field(default_factory=dict)


class ExcelTestQuestion(BaseModel):
    workflow_id: str
    request: str
    what_to_check: str = ""
    sheet: str = ""
    row: int = 0


class SheetInfo(BaseModel):
    name: str
    role: Literal["workflows", "test_questions", "ignored"]
    dimensions: str = ""
    header_row: int | None = None
    columns: list[str] = Field(default_factory=list)
    unrecognized_columns: list[str] = Field(default_factory=list)
    rows_read: int = 0
    note: str = ""


class ExcelCatalog(BaseModel):
    path: str
    sha256: str
    sheets: list[SheetInfo] = Field(default_factory=list)
    workflows: list[ExcelWorkflow] = Field(default_factory=list)
    test_questions: list[ExcelTestQuestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# =============================================================================
# 2. Spec layer (YAML) - inputs, steps, decisions, final result
# =============================================================================
class InputType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    LIST = "list"
    FILE = "file"
    CHOICE = "choice"


class InputSpec(StrictModel):
    name: str
    type: InputType = InputType.STRING
    description: str
    required: bool = False
    default: Any = None
    items: Literal["string", "number"] = "string"  # element type for list inputs
    choices: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    #: value must literally appear in the user's request (anti-hallucination for ids)
    grounded: bool = False
    #: offered to the user / LLM for extraction. False = system configuration
    #: that only takes its default (e.g. which internal CSV to read).
    expose: bool = True
    covers: list[str] = Field(default_factory=list)  # Excel "Inputs" phrases satisfied
    aliases: list[str] = Field(default_factory=list)  # label aliases for fallback extraction
    #: regex used only by the deterministic fallback extractor (group 1 if present, else the whole match)
    extract_pattern: str | None = None
    example: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not IDENT_RE.match(v):
            raise ValueError(f"input name {v!r} must be lower_snake_case")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> "InputSpec":
        if self.required and self.default is not None:
            raise ValueError(f"input '{self.name}' cannot be both required and have a default")
        if self.type == InputType.CHOICE and not self.choices:
            raise ValueError(f"choice input '{self.name}' needs `choices`")
        for pat in (self.extract_pattern,):
            if pat:
                try:
                    re.compile(pat)
                except re.error as exc:
                    raise ValueError(f"input '{self.name}' has an invalid extract_pattern: {exc}") from exc
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"input '{self.name}' has an invalid pattern: {exc}") from exc
        return self


class InputGroup(StrictModel):
    """At least one of ``any_of`` must be provided."""

    any_of: list[str] = Field(min_length=2)
    message: str


class RetryPolicy(StrictModel):
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_seconds: float = Field(default=0.25, ge=0, le=10)
    backoff_factor: float = Field(default=2.0, ge=1, le=4)


class Action(StrictModel):
    """What a decision case does when its condition holds."""

    type: Literal["continue", "goto", "end", "await_input", "fail"] = "continue"
    target: str | None = None  # goto: step id
    message: str | None = None  # template; shown to the user / recorded
    missing: list[str] | str = Field(default_factory=list)  # await_input: input names to re-ask, or "=expr"
    outcome: str = "completed"  # end: which FinalSpec outcome to build
    category: ErrorCategory = ErrorCategory.USER_CORRECTABLE  # fail

    @model_validator(mode="after")
    def _check(self) -> "Action":
        if self.type == "goto" and not self.target:
            raise ValueError("goto action needs a `target` step id")
        if self.type in ("await_input", "fail") and not self.message:
            raise ValueError(f"{self.type} action needs a `message`")
        return self


class DecisionCase(StrictModel):
    label: str
    when: str | None = None  # None = otherwise
    then: Action = Field(default_factory=Action)


class Ensure(StrictModel):
    """Post-condition checked after a tool step: ``expr`` is evaluated with the
    step's own result available as ``output``; false fails the step."""

    expr: str
    message: str  # template
    category: ErrorCategory = ErrorCategory.TERMINAL


class DataSource(StrictModel):
    """A system the workflow reads from that is not supplied by the user."""

    name: str
    description: str
    covers: list[str] = Field(default_factory=list)


class StepDef(StrictModel):
    id: str
    name: str
    excel_step: str  # the literal Excel step this executable step implements
    description: str = ""
    kind: Literal["tool", "decision"] = "tool"
    tool: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    when: str | None = None  # guard: step is skipped when false
    on_failure: Literal["fail", "continue"] = "fail"
    retry: RetryPolicy | None = None
    ensure: list[Ensure] = Field(default_factory=list)
    cases: list[DecisionCase] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not IDENT_RE.match(v):
            raise ValueError(f"step id {v!r} must be lower_snake_case")
        return v

    @model_validator(mode="after")
    def _kind(self) -> "StepDef":
        if self.kind == "tool" and not self.tool:
            raise ValueError(f"tool step '{self.id}' needs a `tool`")
        if self.kind == "decision":
            if self.tool or self.params:
                raise ValueError(f"decision step '{self.id}' cannot have `tool`/`params`")
            if not self.cases:
                raise ValueError(f"decision step '{self.id}' needs at least one case")
        return self


class WarningRule(StrictModel):
    when: str
    message: str


class FinalOutcome(StrictModel):
    headline: str  # template
    severity: Literal["success", "warning", "info"] = "success"
    result: dict[str, Any] = Field(default_factory=dict)  # name -> literal | "=expr" | template


class FinalSpec(StrictModel):
    outcomes: dict[str, FinalOutcome]
    warnings: list[WarningRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_completed(self) -> "FinalSpec":
        if "completed" not in self.outcomes:
            raise ValueError("final.outcomes must define a 'completed' outcome")
        return self


class ExampleRequest(StrictModel):
    """A realistic sample request, used by the UI chips, docs and smoke tests."""

    title: str
    request: str
    attachments: list[str] = Field(default_factory=list)  # fixture file names
    expect_state: Literal["executed", "needs_input"] = "executed"
    note: str = ""


class WorkflowSpec(StrictModel):
    """Executable interpretation of one Excel workflow (``data/specs/<ID>.yaml``)."""

    workflow_id: str
    version: int = 1
    enabled: bool = True
    routing_examples: list[str] = Field(default_factory=list)
    routing_keywords: list[str] = Field(default_factory=list)
    routing_patterns: list[str] = Field(default_factory=list)  # regexes that strongly indicate this workflow
    examples: list[ExampleRequest] = Field(default_factory=list)
    interpretation_notes: list[str] = Field(default_factory=list)
    error_handling_notes: list[str] = Field(default_factory=list)
    inputs: list[InputSpec] = Field(default_factory=list)
    input_groups: list[InputGroup] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    #: Excel "Tools_Required" phrase -> registered tool names that implement it
    tool_map: dict[str, list[str]]
    steps: list[StepDef]
    final: FinalSpec


# =============================================================================
# 3. Normalized definition
# =============================================================================
class ConditionInfo(BaseModel):
    """A condition declared by a workflow (for catalog display)."""

    step_id: str
    kind: Literal["guard", "decision_case", "row_rule", "postcondition"]
    label: str
    expression: str | None
    action: str | None = None


class TestRequest(BaseModel):
    request: str
    what_to_check: str = ""


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    description: str
    triggers: list[str]
    routing_examples: list[str] = Field(default_factory=list)
    routing_keywords: list[str] = Field(default_factory=list)
    routing_patterns: list[str] = Field(default_factory=list)
    examples: list[ExampleRequest] = Field(default_factory=list)
    inputs: list[InputSpec]
    input_groups: list[InputGroup] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    steps: list[StepDef]
    decision_logic: str  # verbatim Excel text
    tools_required: list[str]  # verbatim Excel phrases
    tool_map: dict[str, list[str]]
    allowed_tools: list[str]
    success_output: str  # verbatim Excel "Expected_Output"
    final: FinalSpec
    error_handling: list[str]
    interpretation_notes: list[str] = Field(default_factory=list)
    test_requests: list[TestRequest] = Field(default_factory=list)
    conditions: list[ConditionInfo] = Field(default_factory=list)
    excel_inputs: list[str] = Field(default_factory=list)
    excel_steps: list[str] = Field(default_factory=list)
    enabled: bool = True
    is_extension: bool = False
    source: str = ""  # "<workbook> / <sheet> row N"

    @property
    def required_inputs(self) -> list[InputSpec]:
        return [i for i in self.inputs if i.required]

    @property
    def exposed_inputs(self) -> list[InputSpec]:
        return [i for i in self.inputs if i.expose]

    def input(self, name: str) -> InputSpec | None:
        return next((i for i in self.inputs if i.name == name), None)

    def step(self, step_id: str) -> StepDef | None:
        return next((s for s in self.steps if s.id == step_id), None)


class CatalogIssue(BaseModel):
    severity: Literal["error", "warning"]
    workflow_id: str | None
    message: str


class CatalogReport(BaseModel):
    """Validation report produced when the registry loads the catalog."""

    ok: bool
    loaded_ids: list[str]
    expected_ids: list[str]
    missing_ids: list[str]
    invalid_ids: list[str]
    extension_ids: list[str] = Field(default_factory=list)
    issues: list[CatalogIssue] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    sheets: list[SheetInfo] = Field(default_factory=list)
    workbook_sha256: str = ""

    @property
    def errors(self) -> list[CatalogIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[CatalogIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def status_label(self) -> str:
        if self.ok:
            return f"OK - {len(self.loaded_ids) - len(self.extension_ids)}/{len(self.expected_ids)} required workflows registered"
        return f"DEGRADED - {len(self.errors)} error(s)"


# =============================================================================
# 4. Execution record
# =============================================================================
class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    AWAITING_INPUT = "awaiting_input"


class ExecutionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # finished, but a non-critical step failed (on_failure: continue)
    FAILED = "failed"
    AWAITING_INPUT = "awaiting_input"


class ErrorRecord(BaseModel):
    code: str
    category: ErrorCategory
    message: str  # user-safe
    step_id: str | None = None
    retryable: bool = False
    recovered: bool = False


class AttemptRecord(BaseModel):
    attempt: int
    error_code: str
    message: str
    category: ErrorCategory
    delay_seconds: float = 0.0


class ConditionEvaluation(BaseModel):
    """One evaluated condition: what, with which values, the outcome, and what came next."""

    id: int
    step_id: str
    kind: Literal["guard", "decision", "row_rule", "postcondition", "final_warning"]
    label: str
    expression: str | None
    values: dict[str, Any] = Field(default_factory=dict)
    result: bool | None = None
    outcome: str = ""
    next_step: str | None = None
    rows_evaluated: int | None = None
    rows_matched: int | None = None
    samples: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class StepRecord(BaseModel):
    step_id: str
    name: str
    description: str = ""
    excel_step: str = ""
    kind: Literal["tool", "decision"] = "tool"
    tool: str | None = None
    tool_kind: str | None = None
    simulated: bool = False
    status: StepStatus = StepStatus.PENDING
    params: dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    output_preview: Any = None
    started_at: datetime | None = None
    duration_ms: float | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    error: ErrorRecord | None = None
    skipped_reason: str | None = None
    next_step: str | None = None
    on_failure: str = "fail"
    note: str | None = None


class FinalResult(BaseModel):
    outcome: str
    severity: Literal["success", "warning", "info"] = "success"
    headline: str
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AwaitingInput(BaseModel):
    message: str
    missing: list[str] = Field(default_factory=list)
    step_id: str | None = None


class ToolUsage(BaseModel):
    tool: str
    kind: str
    simulated: bool
    calls: int = 0
    failures: int = 0


class SelectionInfo(BaseModel):
    """How the workflow was chosen (copied from the routing decision)."""

    explanation: str = ""
    confidence: float | None = None
    mode: str = "direct"  # llm | deterministic | direct
    fallback_reason: str | None = None


class ExecutionRecord(BaseModel):
    run_id: str
    request: str
    workflow_id: str
    workflow_name: str
    selection: SelectionInfo = Field(default_factory=SelectionInfo)
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_sources: dict[str, str] = Field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.RUNNING
    steps: list[StepRecord] = Field(default_factory=list)
    conditions: list[ConditionEvaluation] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    recovery_attempts: list[AttemptRecord] = Field(default_factory=list)
    final_result: FinalResult | None = None
    partial_results: dict[str, Any] = Field(default_factory=dict)
    awaiting: AwaitingInput | None = None
    warnings: list[str] = Field(default_factory=list)
    tools_used: list[ToolUsage] = Field(default_factory=list)
    llm_used_in_tools: bool = False
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_ms: float | None = None

    def step_record(self, step_id: str) -> StepRecord | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    @property
    def executed_step_ids(self) -> list[str]:
        return [s.step_id for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.FAILED)]

    @property
    def completed_step_ids(self) -> list[str]:
        return [s.step_id for s in self.steps if s.status == StepStatus.COMPLETED]


# =============================================================================
# 5. Routing and agent
# =============================================================================
class Candidate(BaseModel):
    workflow_id: str
    name: str
    score: float | None = None
    reason: str = ""


class RoutingDecision(BaseModel):
    decision: Literal["selected", "ambiguous", "no_match"]
    workflow_id: str | None = None
    explanation: str = ""
    confidence: float | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    clarification_question: str | None = None
    mode: Literal["llm", "deterministic", "continuation", "direct"] = "deterministic"
    model: str | None = None
    fallback_reason: str | None = None


class MissingInput(BaseModel):
    name: str
    description: str
    reason: str = "not provided"


class InvalidInput(BaseModel):
    name: str
    value: Any = None
    reason: str


class InputResolution(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    missing: list[MissingInput] = Field(default_factory=list)
    invalid: list[InvalidInput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extraction_mode: Literal["llm", "deterministic", "none"] = "none"
    extraction_note: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid


class PendingState(BaseModel):
    """What the agent needs to resume a conversation after a clarification."""

    workflow_id: str
    collected_inputs: dict[str, Any] = Field(default_factory=dict)
    collected_sources: dict[str, str] = Field(default_factory=dict)
    original_request: str = ""
    reason: str = ""
    missing: list[str] = Field(default_factory=list)


class Attachment(BaseModel):
    name: str  # display name
    path: str  # file name (relative to fixtures/uploads) or absolute path inside an allowed root


AgentState = Literal["executed", "needs_input", "ambiguous", "no_match", "error"]


class AgentResponse(BaseModel):
    state: AgentState
    message: str
    request: str
    routing: RoutingDecision | None = None
    resolution: InputResolution | None = None
    record: ExecutionRecord | None = None
    pending: PendingState | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ErrorRecord | None = None
    duration_ms: float | None = None


# =============================================================================
# 6. Tools (metadata exposed to UI / docs)
# =============================================================================
class ToolInfo(BaseModel):
    name: str
    description: str
    kind: str
    simulated: bool
    side_effect: bool
    idempotent: bool
    core: bool
    expression_params: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
