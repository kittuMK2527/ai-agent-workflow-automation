# Test report

Recorded on 2026-10-12. Everything below was actually run; nothing is estimated.

## Environment

macOS (Darwin 25.6), **Python 3.11.6**, packages as resolved from `requirements-dev.txt` in a **brand-new virtual environment**:
streamlit 1.65.0 · pandas 2.3.3 · openpyxl 3.1.5 · pydantic 2.14.1 · openai 2.54.0 · python-dotenv 1.2.4 · PyYAML 6.0.3 · pytest 9.1.1.
The unit tests never read `OPENAI_API_KEY` or `.env` and never touch the network, whether or not a key exists on the machine. The live LLM checks are separate and described in "Live LLM verification".

## Result

```text
$ pytest                       # fresh venv, no API key, no network
681 passed, 1 deselected in ~30 s
```

The deselected test is the single **live** test (`pytest -m live`), which calls the configured provider and is opt-in. It was run separately against Gemini and passed
(see "Live LLM verification"). 682 tests are collected in total.

| File | Tests | What it verifies |
|---|---:|---|
| `test_conditions.py` | 93 | Safe expression language: operators, functions, short-circuiting, strict typing, `null` handling, templates, **rejection of unsafe constructs** (`__import__`, `eval`, attribute access, unknown functions, deep nesting), static reference extraction |
| `test_excel_loader.py` | 26 | The real workbook (10 workflows, 56 steps, 10 test questions, **SHA-256 equals the original**), plus synthetic layouts: reordered/renamed headers, header not in row 1, merged cells, continuation rows, numbered/multi-line steps, unknown columns, corrupt/missing/empty files |
| `test_workflow_registry.py` | 40 | Registration of the 10 workflows, lookup, hot reload, **deliberately broken catalogs**: duplicate IDs, missing spec/workflow, Excel text drift (changed/added step, uncovered input, unmapped tool), unregistered/forbidden tools, unknown/missing parameters, bad expressions, bad references (unknown/later step, unknown output field, undeclared input), backwards `goto`, undefined outcomes, extension loading and override protection; normalized JSON in sync |
| `test_tools.py` | 60 | Registry mechanics, sandbox (traversal, symlink, type), readers, every table/validation/matching/classification/content tool with valid input, invalid input, **empty results**, simulated failures, fault injection, unexpected output, formula-injection defusing, LLM-vs-template content incl. strict/auto failure behaviour |
| `test_execution_engine.py` | 34 | The generic engine on purpose-built tools: mapping, guards, decisions (`goto`/`end`/`await_input`/`fail`), post-conditions, **bounded retries**, no retry of validation/non-idempotent errors, `continue` → partial, preserved results, no false success, redacted logs, listener isolation |
| `test_workflows.py` | 94 | **Business rules of all ten workflows**, each against an independent oracle (pandas/plain Python on the fixtures) and with boundary cases (stock = minimum, price gap = exactly 10 %, failure rate = exactly 10 %): correct step order, tools, mapping, conditions, branching, results, failure and partial completion |
| `test_router.py` | 60 | All 10 Excel test questions + paraphrases route correctly; ambiguity; unrelated/generic requests rejected; attachment names cannot decide a route; LLM router with mocked answers (valid, `NONE`, ambiguous, **invented ID rejected**); auto/strict/deterministic modes and visible fallback |
| `test_inputs_extraction.py` | 64 | Typing/limits, defaults, attachment mapping, sandboxed file checks, grounding, input groups; deterministic extraction; the LLM extraction schema built from real inputs |
| `test_agent.py` | 27 | Every example ends in its declared state; guardrails; multi-turn clarification (answers accumulate, replies are not re-routed); attachments and path traversal; LLM-extracted values re-validated; hallucinated identifiers dropped; fallback on provider errors; containment of internal errors; audit log redaction |
| `test_llm_backend.py` | 40 | Request shape (forced strict function call), malformed provider answers, **real `openai` exception classes mapped** to friendly typed errors, the connection check proves a real structured answer and reports why it failed, **the pause after a limit/outage** (length per kind of problem, no request while paused, ends by itself, ignored by the connection check), and **end-to-end fallback**: one failed call in `auto` mode, then offline answers; `llm` mode reports the limit then the pause, secrets never in messages/logs, connection check, settings parsing |
| `test_architecture.py` | 15 | No workflow ID anywhere in `src/`; one generic engine; **the 11th workflow runs through the same agent**, the original ten are unchanged by it; a brand-new 12th workflow built from files only; unsupported tool fails validation; nothing simulated is presented as real; the only side effect writes a local CSV |
| `test_ui.py` | 15 | Streamlit app (headless `AppTest`): loads, examples from the real catalog, empty request, execution + timeline + result, **clarification flow regression**, reset, no-match, catalog cards/developer tabs, **pipeline tracker follows the real response**, **hostile text stays inert**, failure simulation → partial result with retries, **palette picker restyles the page**, "How it works" shown only until something runs, **an unreachable LLM (real connection failure, no mocks): Auto still answers and says so, LLM-only offers *Switch to Auto and retry*** |
| `test_ui_components.py` | 49 | The UI component library: **every component escapes untrusted text**, block HTML is single-line (a Markdown-parser constraint), pipeline stages and outcome tones for every kind of response (success, asks, missing inputs, no match, ambiguous, rejected, failed, partial), accessibility attributes (ARIA roles, text + icon on every status), light/dark neutrals define the same tokens, the four accent palettes each define every token, **no external requests in the stylesheet**, every CSS variable is defined, **WCAG AA contrast for text/background pairs, and for every accent palette in light and dark** (accent text on each surface, label colour on both button-gradient stops, translucent hero text on each hero stop), accent colours stay distinct from the error red |
| `test_repo_hygiene.py` | 64 | No secret-like strings, `.env.example`, `.gitignore`, no tracked `.env`, bounded requirements, every deliverable exists, README sections, no invented GitHub/Loom URLs, **generated docs equal what the code produces** (`workflow_analysis`, `validation_report`, `sample_requests`, `expected_outputs`, regenerated fixtures byte-identical), scripts exit codes |

## Other verification performed

* **Catalog validation:** `python scripts/validate_workflows.py` → *OK - 10/10 required workflows registered*, 0 errors, 0 warnings.
* **All 22 example requests** run end to end (`python scripts/run_smoke_tests.py`): 22/22 end in the declared state.
* **The ten Excel `Test_Questions`** all route to the intended workflow in deterministic mode. *Caveat:* the fallback router was tuned while looking at these questions and my paraphrases, so this is a functional check, not an unbiased accuracy measurement.
* **Real browser runs** of the Streamlit app (in-app browser, light and dark, desktop and phone width): hero, quick starts, pipeline tracker, outcome strip, timeline, result tables, the catalog cards and detail tabs, the sidebar, the empty-request guard, `Where is order ORD-9999?` → awaiting input → reply `ORD-1002` → completed. This is how the widget-state defect below was found, and how the premium UI was reviewed.
* **Clean installation:** a new venv created from `requirements-dev.txt` runs the whole suite and the scripts.
* **Real user activity** while the app was running (a workbook uploaded with a vague request) exposed a routing weakness that is now fixed and tested.

## Defects found by testing, and fixed

| Found by | Defect | Fix + regression test |
|---|---|---|
| engine test | A traceback written by `log.exception` leaked an exception message containing a key-like string past the log filter | `log_exception()` logs a *redacted* traceback; `RedactingFormatter`; `test_unexpected_tool_exceptions_are_contained_and_redacted` |
| workflow test | A valid inventory file lacking the optional `category`/`supplier` columns failed the whole workflow | `select_optional` in `table_query`; `test_wf001_missing_stock_values…` |
| workflow test | Template meta description for a bare product name was shorter than the validator's own minimum (validator correctly refused it) | Template adds a neutral call to action; `test_wf004_missing_attributes_are_marked_not_invented` |
| workflow test | Internal `_source_row` keys leaked into user-facing results | `strip_private` on evaluated results |
| workflow test | Derived true/false rule columns (e.g. "price difference exceeds threshold") were invisible in the condition trace | Reported like filters, with the values used |
| extraction test | A file name mentioned in the request was assigned to *every* file input | Assigned to the first user-provided file input only |
| extraction test | A file-name regex swallowed the words before it | Tightened pattern |
| agent test | An unsupported attachment type was silently ignored | A warning tells the user |
| loader test | A `Process Steps` header and continuation lines glued onto the previous step | Alias added; line breaks always end a step |
| browser run | `StreamlitWidgetAlreadyInstantiatedError` after a clarification (headless test did not reach this path) | Deferred state changes + `test_clarification_flow_regression_widget_state` |
| user run | A vague "uploaded file" request was routed to WF003 on generic words, and attachment *names* could route by themselves (`hello` + a file called `…Workflow…` → WF010) | Generic words and attachment names cannot decide alone; two regression tests |
| hygiene test | Generated docs contained a random temp-directory name | Stable scratch folder name; docs-in-sync tests |
| UI contrast test | Success-green text on its tinted chip was 4.48:1 and the muted-meta colour only 3.7:1 (below WCAG AA 4.5:1) | Darker tokens in both palettes; `test_text_and_background_pairs_meet_wcag_aa_contrast` |
| UI contrast test (palettes) | Emerald hero gradient's last stop gave only 4.0:1 for the faintest translucent white text (KPI labels) | Stop darkened (`#0F766E` → `#0D6B64`, 4.5:1+); `test_white_hero_text_is_readable_on_every_gradient_stop` |
| owner's use | Requests hung for minutes or showed an error: every LLM call in a request retried through its own slow failure (`timeout × retries`, 6+ calls per request) and a rate-limited key was asked again on every request; in `llm` mode the error was a dead end | The LLM pauses after the first limit/outage so later calls skip it instantly; `auto` answers with the labelled fallback; `llm` mode shows *Switch to Auto and retry*; retries/timeout lowered in `.env`; tests above |
| live LLM run | The LLM extracted the campaign `products` as "Autumn 2026 collection" (the user's wording); the catalog says "Autumn 2026", so the lookup found nothing and the workflow asked for products. Offline extraction had trimmed it | `product_data_reader` retries once with filler words (collection, range, the, new...) removed; filler alone never matches; `test_product_data_reader_ignores_filler_words_around_a_collection_name` |
| live LLM run | **Check LLM connection** said "available" for `gemini-2.5-flash`, which is listed but retired for new keys (generation returned `404`) | The check now sends one tiny forced function call and reports the real failure; `test_check_connection_proves_a_real_structured_answer`, `test_check_connection_reports_why_it_failed` |
| UI browser review (palettes) | In a dark browser the page was dark on first load but turned light after the first click: `.streamlit/config.toml` pinned `[theme] base = "light"`, so Streamlit reported a light theme on reruns (reproduced in a scratch app: `dark` on load, `light` after a click) | Config now defines `[theme.light]` and `[theme.dark]` with no `base`; verified in the browser that dark stays dark across reruns (native widgets follow too) |
| UI browser review | Execute button label truncated in a narrow column; stray indigo underline under the selected tab; uneven catalog card heights; sidebar toggle overlapping content on phones | Wider column + `nowrap`; selection indicator hidden; fixed card layout; top padding on small screens |

## Live LLM verification (Google Gemini, free-tier key)

Run on 2026-10-12 with a Gemini API key (`AQ.` format) through Gemini's OpenAI-compatible endpoint
(`OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`), model **`gemini-3.5-flash-lite`**, `LLM_MODE=llm` (strict: no fallback can hide a failure).

| Check | Result |
|---|---|
| `python scripts/verify_live_llm.py` | **10/10** Excel test questions routed to the intended workflow by the LLM; WF004, WF007 and WF009 correctly stopped to ask for missing details |
| `pytest -m live` | 1 passed |
| WF004, WF005, WF007, WF008, WF009 with full-detail requests | LLM extraction filled the right inputs; WF004 and WF007 content was written by the model (`mode: llm`, no template fallback); WF005, WF008, WF009 completed |
| Vague request (`Generate SEO content for this product.`) | Routed to WF004; nothing was invented; the agent asked for the missing information |
| Sidebar **Check LLM connection** | Reports success for the working model and `not available for this API key` for the retired `gemini-2.5-flash` |

What this does **not** show: it is one provider, one model and one run on a free tier. `gemini-3.8-flash` hit its daily request quota during testing (`429`, per-model per-day), and the busier models returned `503` at times;
`gemini-3.5-flash-lite` answered every request except short per-minute bursts (`429`). The app handled these as designed (visible error in `llm` mode, labelled fallback in `auto` mode),
but a different or paid account may behave differently. OpenAI's own API was **never called** (no OpenAI key).

## Not tested / could not be tested here

* **OpenAI's own API.** No OpenAI key was available, so `gpt-4o-mini` (the default model) was never called. The OpenAI-protocol code path was exercised through Gemini's compatible endpoint, and by tests with a fake client and the real SDK exception types.
  Strict function calling must be supported by whatever model you configure; `python scripts/verify_live_llm.py` exits `2` and says "NOT verified" when there is no key, so it cannot be mistaken for a pass.
* Other operating systems or Python versions (macOS + 3.11.6 only).
* Accessibility audit, narrow/mobile layouts, and behaviour under heavy or concurrent use; sessions are in memory and single user.
* Performance with the maximum file sizes (the limits are enforced and tested, not benchmarked).
* **Publishing:** no GitHub remote exists and nothing was pushed; **no Loom recording exists**.
