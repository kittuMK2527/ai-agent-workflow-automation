# AI Agent Workflow Automation

An AI agent that takes a **natural-language business request**, chooses the right workflow from the **Excel file that defines
ten business workflows**, extracts and validates its inputs, executes its steps through **one generic engine** using
registered (simulated) tools, evaluates the Excel decision rules, handles errors, and returns a structured result with a
complete execution trace.

```text
"Where is order ORD-1001?"  →  Customer Order Status (WF005)  →  8 steps, 3 simulated tools, 2 decisions
                            →  "Order ORD-1001 is shipped. SwiftShip - in_transit (tracking SS1234567890, ETA 2026-10-14)."
```

> **Honest status.** Everything below was built and verified locally (see [Verification](#verification-and-test-report)).
> The live LLM path **was exercised against Google Gemini** (`gemini-3.5-flash-lite`, through Gemini's OpenAI-compatible endpoint, see
> [Using Google Gemini](#using-google-gemini-or-another-openai-compatible-provider)): all 10 Excel questions were routed correctly and
> LLM extraction plus LLM-written content ran end to end. It **could not be exercised against OpenAI's own API** (no OpenAI key was
> available); that path is covered by tests that use the real `openai` SDK exception types against a fake client. Without a key the app
> runs in a clearly labelled **deterministic fallback mode** that is never presented as an LLM.
> All business data (orders, shipments, employees, …) is **simulated fixture data**; nothing contacts a real system.

---

## Contents

1. [Features](#1-features) · 2. [Architecture](#2-architecture) · 3. [Technology choices](#3-technology-choices) ·
4. [Prerequisites](#4-prerequisites) · 5. [Installation](#5-installation) · 6. [Configuration](#6-environment-variables) ·
7. [Run the app](#7-run-the-application) · 8. [Run the tests](#8-run-the-tests) · 9. [How the Excel is processed](#9-how-the-excel-workflows-are-processed) ·
10. [How the agent selects a workflow](#10-how-the-agent-selects-a-workflow) · 11. [Tool calling](#11-how-tool-calling-works) ·
12. [Conditions and branching](#12-how-conditions-and-branching-work) · 13. [Errors and retries](#13-how-errors-and-retries-are-handled) ·
14. [Add an 11th workflow](#14-how-to-add-an-11th-workflow) · 15. [Sample requests](#15-sample-requests-and-expected-results) ·
16. [Simulation vs real](#16-simulation-versus-real-api-behaviour) · 17. [Security](#17-security-considerations) ·
18. [Known limitations](#18-known-limitations) · 19. [Troubleshooting](#19-troubleshooting) · 20. [Project layout](#20-project-layout)

---

## 1. Features

* **Excel is authoritative.** Ten workflows are read from `data/workflows.xlsx` (a byte-identical copy of the assignment file). A spec per workflow adds the executable detail, and the registry **refuses to load a workflow whose spec no longer matches the workbook text**.
* **LLM routing and extraction** via OpenAI function calling (forced, strict JSON schemas built from the *real* catalog and each workflow's *real* inputs). The model's workflow ID is validated against the registry; an unknown ID is rejected, never executed.
* **Clarification instead of guessing.** Missing, invalid, ambiguous and unmatched requests are answered with a precise question or an explanation, and multi-turn replies continue the same workflow.
* **One generic execution engine.** No workflow-specific Python exists in `src/` (a test enforces it). Sequential steps, guards, decision branches (`continue` / `goto` / `end` / `await_input` / `fail`), post-conditions, failure policies, bounded retries, partial results.
* **Safe condition language.** Rules such as `row.current_stock < row.threshold` are parsed by a hand-written parser: no `eval`, no attribute access, whitelisted functions only.
* **Central tool registry** (26 tools) with Pydantic input/output schemas; every call is validated before and after.
* **Complete execution record:** request, selected workflow and why, validated inputs and where each came from, per-step status/params/output/attempts, every condition with the values used and the branch taken, errors with categories, recovery attempts, duration.
* **Premium Streamlit UI** (light and dark, follows your Streamlit theme; **four colour palettes - Indigo, Emerald, Champagne, Sunset - switchable from the sidebar**): animated gradient hero with live catalog KPIs and a "How it works" panel, a command card with one-click quick starts, a live **pipeline tracker** (Route → Extract → Validate → Execute → Result), an **outcome strip**, a visual **execution timeline** (status, tool, SIMULATED labels, retries, durations), step/condition/error drill-downs, a card-based **workflow catalog** with the verbatim Excel text, a developer view, failure simulation and file upload. Responsive down to phone width, WCAG-AA contrast tested for every palette in light and dark, no external fonts or scripts.
* **Honest fallback.** No key, rate limit, timeout or outage → the LLM is paused and a labelled deterministic router/extractor/template answers meanwhile (`auto` mode), or an explicit error with a one-click switch to Auto (`llm` mode).
* **An 11th workflow is demonstrated** (`examples/extension_wf011`) using only a workbook row and a YAML file.

## 2. Architecture

```text
 Natural-language request (+ optional attached CSV/XLSX)
                    │
                    ▼
        ┌──────────────────────┐        ┌─────────────────────────────┐
        │  Streamlit UI /      │        │  Workflow registry          │
        │  Agent.handle()      │◄──────►│  Excel workbook + YAML specs │
        └──────────┬───────────┘        │  validated at load time     │
                   │                    └─────────────────────────────┘
                   ▼
        ┌──────────────────────┐   OpenAI function call `select_workflow`
        │  Router              │   (or labelled deterministic fallback)
        │  ID checked against  │──► selected │ ambiguous │ no_match
        │  the registry        │
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐   OpenAI function call `extract_inputs`
        │  Input extraction    │   schema generated from the workflow's inputs
        │  + validation        │──► typed values │ missing │ invalid ──► ask the user
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐        ┌──────────────────────────────┐
        │  Generic execution   │───────►│  Tool registry (26 tools)     │
        │  engine              │◄───────│  schema-validated in & out    │
        │  guards · decisions  │        │  simulated APIs · local data  │
        │  retries · policies  │        │  computation · LLM content    │
        └──────────┬───────────┘        └──────────────────────────────┘
                   │  safe expression evaluator (conditions, row rules)
                   ▼
        ┌──────────────────────┐
        │  ExecutionRecord     │──► UI trace · structured result · audit log (JSONL, redacted)
        └──────────────────────┘
```

The LLM only *proposes* (a workflow ID and parameter values). Everything after that is deterministic and validated by code. Details:
[`docs/architecture.md`](docs/architecture.md), rationale: [`docs/technical_decisions.md`](docs/technical_decisions.md).

## 3. Technology choices

| Choice | Why |
|---|---|
| **Python 3.11+** | Modern typing, fast enough, ubiquitous for this kind of backend |
| **Pydantic v2** | One schema language for workbook rows, specs, tool inputs/outputs, execution records, and the JSON schemas sent to the LLM |
| **OpenAI Python SDK** (chat completions + forced function calling, strict schemas) | Required by the assignment; structured output without parsing free text. Model is configurable (`OPENAI_MODEL`) |
| **pandas + openpyxl** | `openpyxl` reads the workbook with merged-cell fidelity; `pandas` reads uploaded CSV/XLSX and renders tables in the UI |
| **PyYAML** | Execution specs are readable, commentable data (JSON would make 10 large specs hard to review) |
| **Streamlit** | A professional, inspectable UI with little code; `AppTest` allows headless UI tests |
| **python-dotenv** | Local configuration without touching the shell |
| **pytest** | Test runner |
| *Not used* | LangChain/LangGraph, a database, a queue: nothing here needed them (see technical decisions) |

## 4. Prerequisites

* Python **3.11 or newer** (developed and tested on 3.11.6, macOS)
* `pip` and the ability to create a virtual environment
* Optional: an OpenAI API key for the LLM path. The app and the full test suite work **without** one.

## 5. Installation

```bash
git clone <your-repository-url> ai-agent-workflow-automation
cd ai-agent-workflow-automation

python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt    # runtime dependencies + pytest (use requirements.txt for runtime only)

cp .env.example .env                   # then edit .env (optional, see below)
```

## 6. Environment variables

Read from the environment and from `.env` (never committed). Only the first three matter for most people.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Enables the LLM path (a key for the provider behind `OPENAI_BASE_URL`; the name is historical). Never logged, displayed or stored in a record |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any chat model that supports function calling with strict schemas |
| `LLM_MODE` | `auto` | `auto`: LLM when a key exists, visible fallback on provider errors · `llm`: strict, no fallback · `deterministic`: never call the LLM |
| `OPENAI_BASE_URL` | – | OpenAI-compatible endpoint, e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` for Gemini |
| `OPENAI_TIMEOUT_SECONDS` / `OPENAI_MAX_RETRIES` | `30` / `2` | Provider timeout and SDK-level retries (each failed call can take `timeout × (retries + 1)`; the pause above stops it repeating) |
| `EXTRA_CATALOG_DIRS` | – | Extra catalog folders (each with `*.xlsx` + `specs/`), e.g. `examples/extension_wf011` |
| `WORKFLOW_WORKBOOK`, `WORKFLOW_SPECS_DIR`, `FIXTURES_DIR`, `OUTPUT_DIR`, `UPLOAD_DIR`, `LOG_LEVEL` | repo paths / `INFO` | Locations and logging |

## 7. Run the application

```bash
streamlit run app.py
```

Open the URL Streamlit prints (default <http://localhost:8501>). Pick a **quick start** (one example per workflow) or type a request, then press **Execute Workflow**.
In the sidebar you can choose the execution mode, load the demo 11th workflow, simulate API outages, reload the catalog and reset. With no API key the header shows
**"Deterministic fallback (not an LLM)"**. The page follows your Streamlit theme: use the top-right menu → Settings → Theme to switch between light and dark. The colour palette is chosen in the sidebar under **Appearance** (Indigo, Emerald, Champagne, Sunset); it only restyles the page and never affects results.

Command-line alternatives (no UI):

```bash
python scripts/validate_workflows.py            # catalog validation report (exit 1 if unhealthy)
python scripts/run_smoke_tests.py               # run all 22 example requests end to end
python scripts/verify_live_llm.py               # LIVE LLM check of the 10 Excel questions (needs OPENAI_API_KEY)
```

## 8. Run the tests

```bash
pytest                    # whole suite, no network, no API key needed
pytest tests/test_workflows.py -v      # the business-rule tests for the ten workflows
pytest -m live            # only the live-provider test (skipped unless OPENAI_API_KEY is set)
```

What is covered is listed in [`docs/test_report.md`](docs/test_report.md), including what could **not** be tested here.

## 9. How the Excel workflows are processed

1. `src/excel_loader.py` reads **every sheet**, finds header rows by content (not position), un-merges cells, splits `→`-separated steps and `;`-separated lists, keeps unknown columns and records sheet/row provenance. Result: `ExcelCatalog`.
2. For each workflow `data/specs/<ID>.yaml` supplies typed inputs, executable steps, row rules (safe expressions), decision branches and the result shape.
3. `src/workflow_registry.py` merges them into a **normalized `WorkflowDefinition`** and validates it statically:
   * the spec's `excel_step` sequence equals the workbook's Steps cell; every Excel input is `covers:`-ed; every Excel tool is mapped in `tool_map`;
   * tools exist and are permitted; parameters exist in the tool schema; required ones are present;
   * every expression parses; `inputs.x` is declared; `steps.y` runs earlier **and** `y`'s tool really outputs that field;
   * branch targets exist and only jump forward; `end` outcomes exist; non-default outcomes only use steps that have run.
4. Duplicate IDs, missing specs, malformed definitions and missing required workflows are reported per workflow; **the catalog is only "OK" when all 10 required workflows are registered and valid**.
5. `python scripts/validate_workflows.py --write` regenerates `data/normalized_workflows.json` (the normalized representation) and `docs/validation_report.md`.

The detailed per-workflow analysis, with every ambiguity in the workbook and how it was resolved, is in
[`docs/workflow_analysis.md`](docs/workflow_analysis.md).

## 10. How the agent selects a workflow

* **LLM path.** The model receives the actual catalog (id, name, description, example requests, required inputs) and must answer by calling `select_workflow` with `workflow_id` (an enum of registered IDs + `NONE`), `confidence`, `ambiguous`, `alternatives`, `explanation`, `clarification_question`.
  The result is validated: an ID the registry does not know raises an error; `NONE` → *no match*; `ambiguous=true` or confidence < 0.5 → the user is asked to choose.
* **Input extraction.** A second forced call, `extract_inputs`, whose schema is generated from the *selected workflow's own inputs*. Values must be stated in the request, else `null`. Afterwards `src/inputs.py` applies defaults, coerces and range-checks types, maps attached files, and drops "grounded" identifiers (order IDs, emails) that do not literally appear in the request.
* **Clarification.** Missing/invalid inputs produce a precise question and a *pending* state; the next message continues the same workflow (answers accumulate across turns).
* **Deterministic fallback** (no key, `LLM_MODE=deterministic`, or provider failure in `auto`): an IDF-weighted keyword scorer over each workflow's name, keywords, trigger and example requests; a top-two score gap triggers "ambiguous", a low score "no match". It routes all 10 workbook test questions correctly, but it is **keyword matching, not language understanding**, and every response says so.

## 11. How tool calling works

Two distinct things are called "tools" here, and they are kept apart:

* **LLM function calling** (OpenAI): used only for routing, input extraction and content generation. The model returns *data*; it cannot run anything.
* **Workflow tools** (`src/tool_registry.py`): the 26 registered capabilities a workflow step may invoke. A step names a tool; the engine resolves it through the registry, which validates parameters against the tool's Pydantic schema **before** the call and the result **after** it. A workflow may only use tools mapped from its own Excel `Tools_Required` (or the generic "core" table utilities). The LLM never selects or invokes a tool.

| Kind | Tools |
|---|---|
| `local_data` | `csv_reader` `file_parser` `keyword_reader` `product_data_reader` |
| `simulated_api` (**SIMULATED**) | `order_search` `order_details` `shipment_lookup` `employee_db` |
| `computation` | `table_query` `table_aggregate` `table_join` `table_dedupe` `field_validator` `identifier_validator` `detect_columns` `normalize_columns` `data_validator` `text_validator` `record_matcher` `group_matches` `keyword_classifier` `category_mapper` `task_analyzer` `report_builder` |
| `llm` | `content_generator` (OpenAI when available, otherwise a labelled template) |
| `file_output` | `dataset_exporter` (writes a CSV into `outputs/`; content-addressed so repeating it is idempotent) |

## 12. How conditions and branching work

Rules are small expressions in the specs, evaluated by `src/condition_evaluator.py`:

```text
Literals    12  0.5  'text'  true  false  null  [1, 2, 'a']
Paths       inputs.threshold   steps.load.rows[0].sku   row["Unit Price"]
Arithmetic  + - * / %          Comparison  < <= > >= == != in not in
Logic       and or not         Functions   abs round ceil floor min max sum len lower upper strip str norm_text norm_code
                                           contains startswith intersect unique first join concat get fmt pct_change
                                           days_between add_days is_blank number iff coalesce exists
Templates   "{{ steps.search.count }} order(s) found"      Typed value in a param:  "=steps.load.rows"
```

* Ordering a `null` is an **error**, never silently false; a row it cannot evaluate is excluded and **reported**.
* `iff`, `coalesce`, `exists`, `and`, `or` short-circuit; there is no loop construct and `goto` only jumps forward.
* **Per-row rules** (`where: row.current_stock < row.threshold`) are evaluated inside the table tools and reported to the trace with *rows evaluated / matched* and sample values. A derived true/false column (e.g. `is_exception`) is reported the same way.
* **Guards** (`when:`) skip a step. **Decision steps** list cases in order; the first true case acts: `continue`, `goto <step>`, `end` (finish with a named outcome, e.g. WF009's `escalated`), `await_input` (pause and ask), `fail`.
* **Post-conditions** (`ensure:`) check a step's own result (e.g. generated text must pass validation) and fail the step if not.
* Every evaluation is recorded: the expression, the **values used**, the result, the outcome and the next step.

## 13. How errors and retries are handled

| Category | Examples | Behaviour |
|---|---|---|
| **user-correctable** | missing/invalid input, unknown order, file without a SKU column, bad role | The agent explains what to fix; unknown order/identifier pauses the workflow (`awaiting_input`) and continues after the reply. Never retried |
| **recoverable** | timeout / outage of a (simulated) API or the LLM | Retried up to **3 times** with exponential back-off, only if the tool is idempotent or has no side effects; then the step fails |
| **terminal** | unexpected tool output, bad definition, internal bug | Fails the step with a safe message; details go to the (redacted) log |

* Execution status: `completed`, `partial`, `failed`, `awaiting_input`; step status: `pending`, `running`, `completed`, `skipped`, `failed`, `awaiting_input`. A failed step is **never reported as success**; results already collected are preserved (`partial_results`).
* `on_failure: continue` lets a non-essential step fail without stopping the run → status `partial` with a warning (WF005 shipment lookup).
* Non-idempotent side effects are never retried blindly; an identical side-effect action cannot run twice in one execution.
* Messages shown to users never contain keys, stack traces or internal details. Logs are redacted, **including tracebacks**.
* LLM failures: `auto` falls back visibly; `llm` reports the error; both distinguish rate limit, timeout, auth and malformed responses.

## 14. How to add an 11th workflow

**No new chatbot, router, engine function or Python is required** if the workflow can be built from existing tools. A working demonstration lives in
[`examples/extension_wf011`](examples/extension_wf011) (*Inventory Valuation Summary*).

1. **Add the row** to the workbook, in the same format (`Workflows` sheet, optionally `Test_Questions`).
   To keep the assignment's workbook untouched, put it in a new `*.xlsx` in a catalog folder (see `scripts/make_extension_workbook.py`).
2. **Add the spec** `specs/WF0xx.yaml` next to it: `inputs` (with `covers:` for each Excel input), `tool_map` (each Excel tool phrase → registered tools),
   `steps` (each with `excel_step:` equal to the Excel text, a registered `tool`, `params`, optional `when` / decision cases / `ensure`), and `final` (headline + result).
3. **Tools:** reuse the existing ones (`table_query` and `table_aggregate` cover most calculations). Only if a capability is genuinely new, add a tool in `src/tools/` and register it. A tool is a function plus two Pydantic models.
4. **Load it:** `EXTRA_CATALOG_DIRS=path/to/folder streamlit run app.py` (or tick "Load demo extension" for the bundled one) and press **Reload catalog**.
5. **Validate:** `EXTRA_CATALOG_DIRS=path/to/folder python scripts/validate_workflows.py`. Mistakes (step text not matching the Excel, unknown tool, bad reference, backwards jump…) are reported with the workflow and the exact problem.
6. **Run it** through the same agent: ask for it in plain language, or `agent.run_workflow("WF0xx", {...})`. Add a test like `tests/test_architecture.py::test_a_twelfth_workflow_needs_only_a_workbook_row_and_a_spec`, which builds a brand-new workflow from files only.

Extension workflows are labelled as such in the catalog and can never override one of the ten.

## 15. Sample requests and expected results

All 22 examples (every Excel test question plus fuller variants) with their **real** traces and results are in
[`examples/expected_outputs.md`](examples/expected_outputs.md), and listed in [`examples/sample_requests.md`](examples/sample_requests.md); both files are generated by
`python scripts/run_smoke_tests.py --write-examples` from actual runs. A selection:

| Request | Workflow | Result |
|---|---|---|
| Which products need restocking? | WF001 | 9 of 22 products need restocking; 299 units to order; `SKU-1005` (stock = minimum) is *not* flagged |
| Find products where vendor price differs by more than 10%. | WF002 | 5 of 22 matched products; `SKU-1003` at exactly 10.00% is *not* flagged |
| Process this vendor spreadsheet and show invalid rows. 📎 sample CSV | WF003 | 7 of 10 rows valid; invalid rows 4, 5 and 10 with reasons; cleaned CSV written |
| Generate SEO content for this product. | WF004 | Asks which product (never invents one) |
| Where is order ORD-1001? | WF005 | Shipped, SwiftShip, tracking `SS1234567890`, ETA 2026-10-14 |
| Where is order ORD-9999? | WF005 | "No order was found… please provide another order ID" → reply `ORD-1002` continues and completes |
| Find likely duplicate products in the catalog. | WF006 | 2 definite groups (identical normalised SKU) + 2 possible groups with confidence |
| Create a campaign brief for the new collection. | WF007 | Asks for the goal, products and dates before generating anything |
| Classify these keywords and map them to pages. 📎 sample CSV | WF008 | 25 unique keywords, 4 intents, categories + target pages, 9 high-priority |
| Assign this urgent task to the best available developer: Fix the checkout payment bug in the React frontend | WF009 | Ken Watanabe (only developer with react + payments + frontend, 20 free hours) |
| Which workflows are failing most often? | WF010 | 4 of 10 flagged: WF005 (20%), WF003, WF009 (13.3%), WF006 (slow); WF002 at exactly 10% is not flagged |

## 16. Simulation versus real API behaviour

* **Simulated** (`kind = simulated_api`, flagged `simulated: true` in tool results, labelled **SIMULATED** in the trace, result and UI): `order_search`, `order_details`, `shipment_lookup`, `employee_db`. They read `data/fixtures/*` deterministically, validate parameters, return empty results for unknown ids, and can fail on demand (UI "Simulate an outage", or `FaultInjector` in code) to exercise retries and partial results.
* **Local data** tools read CSV/XLSX files from `data/fixtures` or an upload; **no tool sends email, calls a payment or notification service, or contacts any external system.** The only side effect is writing a CSV into `outputs/`, and a test asserts exactly that.
* **Real**: only the OpenAI calls (routing, extraction, content generation), and only when `OPENAI_API_KEY` is set. When content is produced by the built-in template, the result says `mode: template` and the UI warns that no language model was used.

## 17. Security considerations

* Secrets come only from the environment; they are never hard-coded, logged, displayed or written into records. `.env` is git-ignored; a test scans the repository for key-like strings.
* **Log redaction** covers messages *and tracebacks* (`src/logging_utils.py`); the audit log masks emails and stores no inputs.
* **No `eval`/`exec`.** Conditions use a bespoke parser with a function whitelist, a length limit, a nesting limit and no attribute access.
* **Prompt-injection posture:** user text and file contents reach the LLM only as quoted data in routing, extraction and content generation. The LLM cannot choose tools, invoke code or bypass a workflow definition; its workflow ID is checked against the registry; extracted values are re-validated; "grounded" identifiers must literally appear in the request; generated copy is validated (length limits, no colour/material words absent from the facts) before it is returned.
* **File access is sandboxed** to `data/fixtures` and `uploads/` (resolved paths, symlink-safe, traversal blocked); uploads are limited to CSV/XLSX and 5 MB, saved under a random name; row limits protect memory; CSV exports **defuse spreadsheet formulas** (`=`, `+`, `@`).
* Error messages are written for users; internals stay in the redacted log.

### Using Google Gemini (or another OpenAI-compatible provider)

The backend talks to any endpoint that speaks the OpenAI Chat Completions protocol with forced function calling. Put the values in `.env`
(git-ignored; never in `.env.example`):

```bash
OPENAI_API_KEY=<your Gemini API key>
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_MODEL=gemini-3.5-flash-lite
```

* Keys from Google AI Studio now start with `AQ.` (the older ones with `AIza`). Both are Gemini keys, not OpenAI keys.
* **Free-tier quotas are per model and low.** Some models allow only a few requests per minute, and some a small number per day; a request
  that needs LLM content makes several calls. See "When the LLM limit is reached" below for what the app does about that. Models also get
  retired for new keys (`gemini-2.5-flash` returned `404`), so use **Check LLM connection** in the sidebar: it sends a real one-call test
  and reports the actual reason.
* Check it end to end with `python scripts/verify_live_llm.py` (spends a few dozen small requests).

#### When the LLM limit is reached (or the provider is busy or unreachable)

* **The first failure pauses the LLM.** A `429` (limit or quota), a `503` (overloaded), a timeout or a connection failure puts the model on a
  pause: as long as the provider asked for (its `retryDelay` / `Retry-After`), otherwise 60 s for a limit and 30 s for an outage, never less
  than 15 s or more than 10 min. While paused **nothing is sent**, so the rest of the request does not wait through the same slow failure again
  (one request can make 6+ LLM calls), and a rate-limited key is not hammered. The pause ends by itself; **Check LLM connection** ends it at once if
  the model answers.
* **`auto` mode (default) keeps answering.** Routing, input extraction and content writing each fall back to the labelled deterministic path
  (keyword router, rule-based extraction, template text). The result says so: a *Fallback router · not an LLM* chip, a note with the reason, and in the
  sidebar *LLM paused · offline fallback* with the time left. The next request uses the LLM again once the pause is over.
* **`llm` (strict) mode never falls back**: it reports the limit, and the error panel offers **Switch to Auto and retry**, which re-runs the same request.
* Fewer retries make failures show up quickly: `OPENAI_MAX_RETRIES=1` and `OPENAI_TIMEOUT_SECONDS=30` are sensible for a free-tier key.

## 18. Known limitations

* **The live LLM path was verified on one provider and model only:** Google Gemini `gemini-3.5-flash-lite` via its OpenAI-compatible endpoint (10/10 Excel questions routed; extraction and content generation ran). **OpenAI's own API was never called** (no key), so `gpt-4o-mini`, the default, is an untested assumption; with it the code relies on the same protocol and is covered by mocks and real SDK exception types. Other models may route differently: run `scripts/verify_live_llm.py`. The Gemini run used a free-tier key, so it was limited by per-minute and per-day quotas.
* **Deterministic fallback quality:** keyword scoring and regex extraction are fine for the supplied wording but weaker on free-form phrasing than an LLM; the keyword and skill classifiers are rule lexicons by design.
* **Data is fixture data.** Replacing a simulated tool with a real integration means implementing the same input/output schema.
* **Interpretations** where the workbook is silent (reorder formula, "high" similarity, priority, time thresholds…) are documented, visible and overridable, but they are *my* interpretations; confirm them with the business owner.
* The Excel/spec coupling is strict by design: renaming a step in the workbook requires updating the spec.
* Single process, in-memory sessions, no authentication or multi-user hardening; sessions are not persisted (only the audit log).
* Limits: 50,000 rows per file, 2,000 products for pairwise duplicate comparison, 5 MB uploads, English text.
* Tested on macOS with Python 3.11; other platforms are expected to work but were not exercised.

## 19. Troubleshooting

| Symptom | Fix |
|---|---|
| Header says **Deterministic fallback** | No `OPENAI_API_KEY` (or `LLM_MODE=deterministic`). Set the key in `.env` and restart; use *Check LLM connection* in the sidebar |
| "LLM mode needs OPENAI_API_KEY" | `LLM_MODE=llm` requires a key; set it or use `auto` |
| "The OpenAI credentials were rejected" / "model … is not available" | Check the key and `OPENAI_MODEL` |
| Rate limit / timeout messages | In `auto` mode the app falls back visibly; in `llm` mode retry later or lower the load |
| Catalog **DEGRADED** | Open *Workflow catalog → Validation issues*, or run `python scripts/validate_workflows.py`; the message names the workflow and the exact problem |
| `ModuleNotFoundError: src` | Run commands from the repository root (`pytest.ini` sets the path for tests) |
| "File … was not found" | Attach the file in the UI, or use a name from `data/fixtures`; only CSV/XLSX up to 5 MB are accepted |
| Port 8501 busy | `streamlit run app.py --server.port 8765` |
| Tests behave differently on your machine | Tests ignore `OPENAI_API_KEY` and never use the network; check you are on Python ≥ 3.11 with `pip install -r requirements-dev.txt` |
| New outputs pile up | Generated files go to `outputs/` (git-ignored); delete them freely |

## 20. Project layout

```text
app.py                      Streamlit UI (wires data to the components below)
ui/                         presentation layer: theme.py (light/dark neutrals, four accent palettes, stylesheet) · components.py (pure, escaped HTML builders)
                            icons.py (inline SVG) · state.py (pipeline stages / outcome derived from a response)
src/
  config.py                 settings from env / .env
  excel_loader.py           workbook → ExcelCatalog (robust, content-based)
  workflow_registry.py      Excel + specs → validated WorkflowDefinitions; static validation
  models.py                 Pydantic schemas (Excel rows, specs, definitions, records, responses)
  condition_evaluator.py    safe expression parser/evaluator, templates
  tool_registry.py          ToolSpec, ToolRegistry, fault injection, tool context
  tools/                    data_access (sandbox) · files · tables · validation · simulated · matching · classification · content
  execution_engine.py       the generic engine
  llm.py                    OpenAI backend (forced function calling), error mapping
  router.py                 LLM router + deterministic fallback router
  extraction.py             LLM + deterministic input extraction
  inputs.py                 typing, defaults, attachments, grounding, groups
  agent.py                  orchestration, modes, multi-turn state
  result_formatter.py       text/markdown rendering   ·   audit.py  redacted JSONL audit log
data/
  workflows.xlsx            the assignment workbook (unchanged copy)      normalized_workflows.json  generated
  specs/WF001..WF010.yaml   executable interpretation of each workflow
  fixtures/                 simulated business data (see its README)      reference/  rule lexicons
tests/                      pytest suite (see docs/test_report.md)
scripts/                    validate_workflows · run_smoke_tests · verify_live_llm · generate_* helpers
examples/                   sample_requests.md · expected_outputs.md (generated) · extension_wf011/ (the 11th-workflow demo)
docs/                       workflow_analysis · architecture · technical_decisions · validation_report · test_report · loom_demo_script
AI_Agent_Workflow_Assessment (1).xlsx   the original file, exactly as received
```

## Verification and test report

See [`docs/test_report.md`](docs/test_report.md) for the recorded results and the list of things that could not be tested here
(OpenAI's own API, other LLM models, non-macOS platforms, a real GitHub push, a Loom recording).
