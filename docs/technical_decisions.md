# Technical decisions

Each entry gives the decision, the alternatives that were considered, and the trade-off accepted.

## D1. The Excel workbook stays authoritative; a YAML spec adds the executable detail

* **Context.** The workbook is free text: steps joined by `→`, prose decision logic, generic tool names, no failure behaviour. A machine needs typed inputs, concrete tools and evaluable rules.
* **Decision.** Read the workbook as the definition of *what* a workflow is. A per-workflow YAML spec adds *how*, and the registry **cross-checks the two** (step text and order, input coverage, tool mapping) and refuses a workflow that drifted.
* **Alternatives.**
  * *Put the executable logic in extra Excel columns/sheets.* Keeps one file, but it modifies the assignment workbook, makes multi-line YAML-like content awkward in cells, and hides code-like logic from review/diff.
  * *Have the LLM read the Excel text at run time and improvise the steps.* Not reproducible or testable, risks inventing logic, and conflicts with "the engine must validate everything".
  * *One Python function per workflow.* Violates the "one generic engine" requirement and is exactly what the extensibility goal forbids.
* **Trade-off.** Two artefacts per workflow must be kept in sync. The strict cross-check turns drift into an immediate, specific error rather than a silent divergence.

## D2. One generic engine and a small safe expression language

* **Decision.** Workflows are data interpreted by `ExecutionEngine`; business rules from the Excel's decision logic are expressions (`row.current_stock < row.threshold`) evaluated by a safe evaluator, so the rule is *visible in the definition, shown in the UI, and reported in the trace with the values used*.
* **Alternatives.**
  * *Business rules inside Python tools* (e.g. a `restock_calculator`). Simpler per workflow, but the Excel rule disappears into code, the trace cannot show it, and each new workflow needs code.
  * *LangGraph/LangChain.* They would orchestrate LLM turns well, but this system deliberately keeps the LLM out of execution; the graph features (cycles, memory, tool-calling agents) are not needed, and they add a large dependency surface.
  * *`eval` or an embedded scripting language.* Rejected outright for safety.
* **Trade-off.** The language is intentionally small; unusual logic needs a new tool. Table tools (`table_query`, `table_aggregate`, `table_join`) were designed to be general enough that most calculations are expressible, which is what makes the 11th workflow code-free.

## D3. A hand-written expression parser instead of a library

* **Decision.** One module (`condition_evaluator.py`): tokenizer, recursive-descent parser, AST evaluator.
* **Alternatives.** `simpleeval` / `asteval` (a dependency whose attack surface and error messages are not ours to control), a JSON AST in the specs (unreadable at this scale), Python's `ast` module restricted by a visitor (still Python semantics: truthiness, `**`, string multiplication…).
* **Why.** Full control of semantics: strict types, `null` cannot be ordered, short-circuiting `iff/coalesce/exists`, a function whitelist checked at *parse* time, and, importantly, `references()` so the registry can verify `steps.x.field` against the producing tool's output schema **before** anything runs.
* **Trade-off.** More code to own; mitigated by 93 dedicated tests including unsafe-construct rejection.

## D4. Specs in YAML

JSON has no comments and makes long expressions/messages hard to review; Python specs would blur "data" and "code". YAML (via `yaml.safe_load`, validated by strict Pydantic models with `extra="forbid"`) is readable, commentable and validated on load. Cost: one small dependency.

## D5. The LLM is confined to proposals, through forced function calls

* **Decision.** Three kinds of call, each `tool_choice`-forced with a strict schema: `select_workflow`, `extract_inputs`, `emit_<task>`. Results are validated by code afterwards.
* **Alternatives.**
  * *A free-running agent loop where the model calls workflow tools.* Flexible, but then the LLM decides which operations run, which is precisely what must not happen; behaviour becomes non-deterministic and hard to test.
  * *JSON mode / free text parsing.* Fragile; function schemas generated from the real catalog make an invalid workflow ID impossible to express (and it is still re-checked).
* **Trade-off.** Fewer degrees of freedom for the model (by design). `strict` schemas require every property listed and nullable types, handled by `obj_schema()`/`nullable()`.

## D6. Routing and extraction are separate calls

Extraction needs the *selected* workflow's input schema, so it cannot happen before routing without sending all ten schemas. Two small calls give a type-correct, workflow-specific schema, a clean place to stop for clarification, and independent failure/fallback handling. Cost: two round-trips on the LLM path.

## D7. A clearly labelled deterministic fallback

* **Decision.** An IDF-weighted keyword router and a regex/pattern extractor, used for offline demos, tests and provider failures. Every response carries `mode` and `fallback_reason`; the UI header says "Deterministic fallback (not an LLM)".
* **Alternatives.** Fail when the LLM is unavailable (unusable offline and untestable); mock the LLM and present the output as LLM (dishonest).
* **Modes.** `auto` (fall back visibly), `llm` (strict, never silent), `deterministic` (never call). The mode is explicit configuration, not an accident of an outage.
* **Trade-off.** The fallback is weaker on free-form wording. It was tuned and *evaluated* on the workbook's own questions plus paraphrases and negative cases, and its limits are documented.

## D8. Tool granularity: a few generic tools plus small domain tools

Ten workflows could have been ten monolithic tools. Instead, generic table tools carry the arithmetic and the Excel rules, and small domain tools do what genuinely needs code (file sniffing, column detection, order/shipment simulation, text similarity, intent lexicons, text validation, content generation). This is what lets `WF011` reuse the existing toolbox and what keeps rules visible in specs.

## D9. Simulation is explicit, deterministic and failure-capable

Orders, shipments and employees are *fixture-backed simulated APIs*: structured I/O, parameter validation, empty results for unknown IDs, `simulated: true` and a `SIMULATED` note in every result, and a `FaultInjector` (also exposed in the UI) so retry, partial-result and failure behaviour can be shown on demand. No tool can send mail, take payments or call anything external; a test enforces that the only side-effect tool writes a local CSV.

## D10. Failure semantics

* Statuses: workflow `completed | partial | failed | awaiting_input`; step `pending | running | completed | skipped | failed | awaiting_input`.
* Errors carry a category: *user-correctable* (ask), *recoverable* (retry, bounded), *terminal*.
* Retry **only** `TransientToolError`, only if the tool is idempotent or side-effect free, 3 attempts, exponential back-off. Validation errors are never retried.
* Default is *fail fast and keep what was collected*; a step may opt in to `on_failure: continue` (WF005's shipment lookup) giving `partial`. A result that cannot be assembled fails the run: success is never inferred.
* Failed/skipped steps expose no output, so downstream steps cannot consume garbage.

## D11. Conversation state is a small explicit object

Clarification uses a `PendingState` (workflow ID, valid answers so far, which inputs are missing), not LLM chat memory. Answers accumulate across turns, a one-line reply answers the single open question, and the reply cannot be re-routed to another workflow. It works identically with and without an LLM and is trivial to test.

## D12. Missing data is asked for, never invented

The workbook's own rules ("do not invent missing product attributes", "request goal/dates before generating", "escalate if no suitable employee", "ask for another identifier") are applied uniformly: required inputs block, optional ones are marked "not specified", skills are never guessed from nothing, unmapped keywords stay unmapped, and rows with unusable values are **excluded and reported** rather than counted either way.

## D13. Generated text is validated, and the generator says which mode produced it

The content tool uses the LLM when available, otherwise a template; both use only the supplied facts. `text_validator` then enforces length limits and rejects colour/material words that are not in the facts. A failed validation fails the workflow (the invented text is never returned), and results/warnings state when a template, not a language model, wrote the text.

## D14. No database, queue or framework beyond what is needed

Everything is deterministic file-backed data and in-memory state; a database would add moving parts without a benefit here. The audit trail is an append-only redacted JSONL file. Streamlit holds session state.

## D15. Testing strategy

* **Independent oracles.** Expected results for each workflow are computed straight from the fixture files (pandas/plain Python), not from the engine's output, and each Excel decision rule has a boundary test (stock = minimum, price difference = exactly 10%, failure rate = exactly 10%).
* **Layers tested separately:** evaluator, loader, registry validation (including deliberately broken catalogs), tools, engine (with purpose-built tools), router, extraction, agent, UI (`AppTest`), repository hygiene.
* **LLM without network.** A fake backend plus the real `openai` exception classes against a fake client; a single opt-in `live` test for a real key.
* **Tests found real defects** during development (a traceback leaking a secret past the log filter, a valid file failing on missing optional columns, a file name assigned to every file input, a widget-state crash in the browser that the headless test had missed, template copy too short for its own validator); each is now covered by a regression test.

## D16. Interpretations of the workbook

Where the workbook is silent, decisions were made explicitly, recorded in each spec's `interpretation_notes`, shown in the app, made overridable inputs where possible, and listed in [`workflow_analysis.md`](workflow_analysis.md) §5. They should be confirmed with the business owner.

## What would come next

* Run `scripts/verify_live_llm.py` with a real key and tune prompts/model if routing differs.
* Replace simulated tools with real integrations behind the same schemas, and add per-tool timeouts.
* Persist sessions/records (database) and add authentication for multi-user use.
* Stream LLM tokens and per-step progress in the UI; add cancellation.
* A visual spec editor and a "dry-run" validator for business users.
