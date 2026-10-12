# Architecture

This document explains the components, the data that flows between them, and the rules each layer enforces.
The overview diagram is in the [README](../README.md#2-architecture); the reasoning behind the main choices is in
[technical_decisions.md](technical_decisions.md).

## 1. Principles

1. **The Excel workbook is the source of truth** for what each workflow is (name, trigger, inputs, steps, decision logic, tools, output).
2. **One reusable agent, one generic engine.** Workflows are *data*. No Python in `src/` names a workflow (a test greps for it).
3. **The LLM proposes; deterministic code decides.** The model suggests a workflow ID and parameter values. Code validates both, and only the engine executes anything.
4. **Never invent data.** Missing information is asked for or marked as missing; a failed step is never reported as success.
5. **Everything is inspectable.** Every run produces an `ExecutionRecord` with the conditions evaluated, values used and branches taken.

## 2. Components

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | `Settings` from env/`.env`; the list of 10 required workflow IDs; redacted `public_summary()` | – |
| `errors.py` | Typed errors with a category (`user_correctable` / `recoverable` / `terminal`) and a user-safe message | – |
| `models.py` | All Pydantic schemas (see §3) | `errors` |
| `excel_loader.py` | Workbook → `ExcelCatalog`; content-based sheet/header detection; provenance | `models` |
| `condition_evaluator.py` | Tokenizer, recursive-descent parser, AST evaluator, templates, static reference extraction | `errors` |
| `tool_registry.py` | `ToolSpec`, `ToolRegistry.execute()` (validate in → run → validate out), `ToolContext`, `FaultInjector` | `models`, `config` |
| `tools/*` | The 26 tools (§5); `data_access.py` is the file sandbox | `tool_registry`, `condition_evaluator` |
| `workflow_registry.py` | Merge Excel + YAML specs → `WorkflowDefinition`; **static validation**; lookup; reload; router catalog; normalized export | `excel_loader`, `tool_registry`, `condition_evaluator` |
| `execution_engine.py` | Generic interpreter producing an `ExecutionRecord` | `tool_registry`, `condition_evaluator`, `models` |
| `llm.py` | `OpenAIBackend.call_function` (forced strict function call), error mapping, **pause after a limit/outage** (no requests while paused) | `config`, `errors` |
| `router.py` | `LLMRouter`, `DeterministicRouter` → `RoutingDecision` | `workflow_registry`, `llm` |
| `extraction.py` | `LLMExtractor` (schema from the workflow's inputs), `DeterministicExtractor` | `models`, `llm` |
| `inputs.py` | Defaults, coercion, limits, attachment mapping, grounding, input groups → `InputResolution` | `models`, `tools.data_access` |
| `agent.py` | Orchestration, modes/fallback, multi-turn `PendingState`, response assembly | all of the above |
| `audit.py`, `logging_utils.py`, `result_formatter.py` | Redacted audit log, redaction helpers, text rendering | – |
| `app.py` | Streamlit UI: layout and wiring only | `agent`, `ui` |
| `ui/theme.py` | Design tokens: light/dark neutrals + four accent palettes (Indigo, Emerald, Champagne, Sunset) and the global stylesheet; `stylesheet(dark, palette)`; no external fonts/scripts/requests | – |
| `ui/components.py` | Pure functions returning **escaped, single-line HTML** (hero, outcome strip, pipeline, timeline, KPI cards, workflow cards…) | `src.models` |
| `ui/state.py` | Derives the pipeline stages and outcome strip from an `AgentResponse` | `ui.components` |
| `ui/icons.py` | Inline SVG icons (decorative, `aria-hidden`) | – |

Dependencies point one way (UI → agent → router/extraction/engine → tools → evaluator/models); there are no circular imports.

## 3. Data model

```text
Workbook row ──► ExcelWorkflow ─┐
                                ├─► WorkflowDefinition ──► (engine input)
YAML spec ────► WorkflowSpec ───┘     id, name, description, triggers, inputs, input_groups, data_sources,
                                      steps[StepDef], decision_logic (Excel text), tools_required (Excel text),
                                      tool_map, allowed_tools, success_output (Excel text), final, error_handling,
                                      conditions[ConditionInfo], examples, test_requests, enabled, is_extension, source
StepDef: id, name, excel_step, kind (tool|decision), tool, params, when, on_failure (fail|continue), retry,
         ensure[Ensure], cases[DecisionCase{label, when, then: Action}]
Action:  continue | goto(target) | end(outcome) | await_input(missing) | fail(category)
FinalSpec: outcomes{name → headline, severity, result}, warnings[{when, message}]
```

Runtime:

```text
AgentResponse { state: executed|needs_input|ambiguous|no_match|error, message, routing: RoutingDecision,
                resolution: InputResolution, record: ExecutionRecord, pending: PendingState, warnings, error, duration_ms }

ExecutionRecord { run_id, request, workflow_id/name, selection{explanation, confidence, mode, fallback_reason},
                  inputs, input_sources{request|attachment|default|reply},
                  status: completed|partial|failed|awaiting_input,
                  steps[StepRecord{status, tool, simulated, params, output_summary, output_preview, duration_ms,
                                   attempts[], error, skipped_reason, next_step, note}],
                  conditions[ConditionEvaluation{kind, label, expression, values, result, outcome, next_step,
                                                 rows_evaluated, rows_matched, samples}],
                  errors[], recovery_attempts[], final_result{outcome, severity, headline, data, warnings},
                  partial_results, awaiting{message, missing, step_id}, tools_used[], duration_ms }
```

## 4. Data flow of one request

```text
1  Agent.handle(text, attachments, pending?)
2  guards: empty / over 4000 characters → error response
3  ROUTE     pending? → continue that workflow
             else LLMRouter (or DeterministicRouter) → RoutingDecision
               no_match → explain what is available        ambiguous → ask which one
               selected → registry.has(id)?  (never run an unknown id)
4  EXTRACT   LLMExtractor / DeterministicExtractor → raw values (unvalidated)
5  RESOLVE   inputs.resolve_inputs(): attachments → file inputs, defaults, type/range checks,
             grounded identifiers, input groups, file existence in the sandbox
               missing/invalid → needs_input (+ PendingState carrying the valid answers)
6  EXECUTE   ExecutionEngine.run(definition, inputs)                            (see §6)
7  MAP       record.status → executed | needs_input (engine paused) | error
8  AUDIT     redacted one-line JSON appended to outputs/audit_log.jsonl
```

## 5. Tools

A tool is `ToolSpec(name, description, input_model, output_model, func, kind, simulated, side_effect, idempotent, core, expression_params)`.

* `ToolRegistry.execute` validates parameters (`extra="forbid"`) → raises `ToolParameterError`; runs the function; requires a dict; validates it against the output model (`extra="forbid"`) → raises `UnexpectedToolOutput`.
* `expression_params` (e.g. `where`, `derive.*`, `rules.*.invalid_when`) mark parameters that hold **row expressions**: the engine passes them through unevaluated and the registry compiles/validates them at load time against the allowed roots (`row`, `vars`).
* Tools raise typed errors: `ToolInputError` (user-correctable: bad file/column), `TransientToolError` (retryable), `ToolExecutionError` (terminal).
* Table tools exclude and **report** rows they cannot evaluate; they never treat missing data as passing or failing.
* `ToolContext.record_condition()` lets a tool report a row rule (rows evaluated/matched + samples) to the trace.

Tool families and the workflows that use them are listed in the README (§11) and in the app's *Developer view*.

## 6. The execution engine

```text
for each step (index i):
    guard `when`?  evaluate → record ConditionEvaluation(kind=guard) → false: SKIPPED, next
    decision step: evaluate cases in order (record each) → first true case → apply Action
        continue → next step        goto → jump forward, bypassed steps SKIPPED
        end(outcome) → stop, build that outcome        await_input → status AWAITING_INPUT, stop
        fail → step FAILED, stop
    tool step:
        resolve params from scope {inputs, steps}:  "=expr" typed · "{{ expr }}" template · expression_params untouched
        attempts ≤ policy.max_attempts (default 3):
            registry.execute(...)
            TransientToolError → record AttemptRecord, back-off sleep, retry   (only if tool.retry_allowed)
            other tool/agent errors → FAILED (category from the error)          unexpected exception → FAILED, safe message
        ensure[] post-conditions → record, FAILED if false (output withheld)
        store output in scope["steps"][id]; record summary + preview
    on FAILED: on_failure=fail → stop · on_failure=continue → go on (status PARTIAL at the end)
finish: AWAITING_INPUT / FAILED → no final result (partial_results kept)
        else build FinalResult from the outcome: headline, result mapping (`_`-prefixed internals stripped), warnings
```

Properties guaranteed by design and tests: forward-only jumps (no loops); failed or skipped steps expose **no output** (referencing one is an explicit error, tolerated only through `coalesce`/`exists`); a result that cannot be assembled fails the run instead of returning a partial answer as success; the UI listener can crash without affecting the run.

## 7. Condition evaluator

Grammar (precedence low→high): `or` · `and` · `not` · comparison (`< <= > >= == != in not in`) · `+ -` · `* / %` · unary `-` · postfix (`.field`, `[index]`, `fn(args)`) · literals/lists/parentheses.

Safety: input length ≤ 800, nesting ≤ 40, only whitelisted function names (checked at *parse* time), paths traverse only `dict` keys and `list` indexes, no `**`, no string/list multiplication, no attribute access on Python objects (`inputs.__class__` is just a missing key). Type rules are strict: arithmetic needs numbers, ordering needs two numbers or two strings, `null` cannot be ordered, conditions must evaluate to a boolean.

`Expression.references()` extracts static paths (`steps.load.rows[0].sku`) so the registry can verify them against tool output schemas before any run, and `explain_values()` produces the "values used" shown in the trace.

## 8. Registry validation

Run for every workflow at load time; each failure is reported as `CatalogIssue(severity, workflow_id, message)`:

1. ID well-formed and unique (duplicates inside a workbook reject **all** copies; an extension can never override an earlier source); name and steps present; a spec file exists and parses.
2. **Traceability**: `excel_step` sequence = Excel steps; every Excel input is `covers:`-ed and the spec covers nothing else; every Excel tool is in `tool_map` and vice versa; mapped tools are registered.
3. **Tools**: each step's tool is registered and permitted (mapped or `core`); parameters ⊆ schema; required parameters present; retry only on retry-safe tools.
4. **References**: `inputs.x` declared; `steps.y` earlier, not a decision, and `y`'s tool outputs the referenced field; `row.*` only inside row expressions; `vars.*` defined in the same step; `output.*` only in `ensure`.
5. **Control flow**: `goto` targets exist and are forward; `end` outcomes are defined; non-default outcomes only use steps that precede their earliest `end`; only one "otherwise" case and it is last.
6. Example attachments exist; routing patterns compile.

`report.ok` is true only when all ten required workflows are registered and valid.

## 9. LLM boundary

| Call | Function | Schema | Validation afterwards |
|---|---|---|---|
| Routing | `select_workflow` | `workflow_id` ∈ registered IDs ∪ {`NONE`}, `confidence`, `ambiguous`, `alternatives`, `explanation`, `clarification_question` | registry membership; ambiguity/low confidence → ask; `NONE` → no match |
| Extraction | `extract_inputs` | one nullable property per exposed input of the selected workflow | `inputs.resolve_inputs` (types, ranges, files, grounding) |
| Content | `emit_<task>` | per-task schema (text, messaging, channels, checklist…) | per-task checks, then the workflow's own `text_validator` steps |

Calls use `tool_choice` forced to the single function and `strict: true`. No temperature/token parameters are sent (some models reject them). Provider exceptions are mapped to `LLMTimeoutError`, `LLMRateLimitError`, `LLMAuthError`, `LLMUnavailableError`, `LLMResponseError` with fixed user-safe messages; the raw detail is redacted before logging.

**Pause.** A rate limit, overload, timeout or connection failure pauses the backend for the time the provider asked for (default 60 s / 30 s, clamped to 15 s - 10 min). While paused `call_function` raises `LLMPausedError` without a network call, so in `auto` mode the router, extractor and content tool fall back immediately (one failed call per outage, not one per step) and in `llm` mode the error is reported at once. A successful answer, or a successful *Check LLM connection*, ends the pause. Auth, not-found and bad-request errors do not pause: waiting cannot fix them.

## 10. Extension points

| To add… | Change |
|---|---|
| a workflow | workbook row + `specs/<ID>.yaml` (no code) |
| a tool | one module in `src/tools/`: input/output models, function, `register()` |
| a content-generation task | a `GenTask` in `tools/content.py` (schema, instructions, template, check) |
| a simulated system | a tool reading a fixture, calling `ctx.faults.check(name)` and returning `simulated: True` |
| a real integration | implement the same tool schema against the real API; set `simulated=False` |
| a rule lexicon | edit `data/reference/*.json` |
