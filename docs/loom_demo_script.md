# Loom demo script (≈ 9–10 minutes)

> This is a **plan for a recording that has not been made**. No Loom link exists yet. Record it yourself, then add the URL to the
> repository README. Everything below uses the real application, with real outputs, no slides required.

## Pre-flight (do once, off camera)

```bash
cd <repo>
source .venv/bin/activate
pytest                                   # all green before you start
python scripts/validate_workflows.py     # "Catalog: OK - 10/10 required workflows registered"
rm -f outputs/*.csv outputs/*.jsonl      # clean outputs folder (keeps .gitkeep)
streamlit run app.py --server.port 8501
```

* Browser at 100 % zoom, window ~1280 px wide; sidebar visible. Close other tabs. The page follows your Streamlit theme (menu → Settings → Theme): light reads best when recording. Pick a palette in the sidebar (Appearance); Indigo is the default.
* **With an API key** (`.env`): the header badge shows `LLM · <model>`; press *Check LLM connection* once beforehand.
  **Without a key** the header shows *Deterministic fallback (not an LLM)*. Say so; do not present it as an LLM.
* Have these open in an editor: `data/workflows.xlsx`, `data/specs/WF001.yaml`, `examples/extension_wf011/specs/WF011.yaml`.
* Do **not** claim: that OpenAI was verified (the live run used Gemini `gemini-3.5-flash-lite`; say which provider you show), that anything is a real
  integration, that a GitHub repo or a Loom exists before it does.

## Timeline

| Time | Show | Say (key points) |
|---|---|---|
| **0:00–0:50** | README architecture diagram | Problem: business requests arrive in plain language and map to ten defined workflows. One agent, not ten bots. The LLM *proposes* a workflow and inputs; deterministic code validates and executes. Flow: request → router → registry → input validation → generic engine → tools → conditions → trace → result |
| **0:50–1:40** | `data/workflows.xlsx` (2 sheets), then terminal: `python scripts/validate_workflows.py` | The Excel is the source of truth: 10 workflows, free-text steps, decision logic, tools, expected output, plus test questions. The loader found both sheets by content. Validation: all 10 extracted; each spec traces back to the Excel text, tools exist, references are valid. Show `WF001.yaml`: `excel_step:` ties every step to the workbook |
| **1:40–2:20** | App → **Workflow catalog** tab | Catalog banner "OK – 10/10". Select WF002: Excel text on the left; steps, tool mapping, and the executable condition `abs(row.pct_difference) > vars.threshold` on the right. Interpretations are documented (e.g. % relative to internal price) |
| **2:20–3:40** | **Run** tab → click the **Customer Order Status** quick start (or type `Where is order ORD-1001?`) → Execute | Watch the **pipeline tracker** turn green stage by stage. Outcome strip: the answer, workflow, time, steps, simulated tools. Selection card: which workflow, why, mode badge, extracted input `ORD-1001` with its source. **Timeline**: 8 steps, tools, **SIMULATED** labels on order/shipment tools, two decision steps. Open *Step details* → one step's *Tool input / output* |
| **3:40–4:50** | Type `Where is order ORD-9999?` | The Excel rule "if no order is found, ask for another identifier": status *awaiting input*, remaining steps pending, no false success. Open **Conditions & branches**: `steps.search_orders.count == 0` with the value used. Reply `ORD-1002` → same workflow continues ("Continuing previous request", source *reply*) and completes |
| **4:50–5:50** | `Find products where vendor price differs by more than 10%.` | Result: 5 of 22 exceptions. Conditions tab: rule + *rows evaluated / matched*. Point out `SKU-1003` at **exactly 10.00 % is not flagged** (strict "exceeds"). Unmatched SKUs reported as a warning, not dropped |
| **5:50–6:50** | Sidebar → *Simulate an outage* → "Shipment API: persistent outage" → re-run `Where is order ORD-1001?` | Retry attempts visible (3, bounded), step failed, workflow finishes **PARTIAL** with the order status and a warning. Then "Order search API: persistent outage": the run **fails**, nothing is reported as success, and results collected so far are preserved. Reset the outage to *None* |
| **6:50–7:40** | `Assign this task to a developer: Migrate the database to MongoDB` | Excel rule "escalate if no suitable employee": outcome *escalated*, with the reason each employee was excluded. Then `Assign this urgent task to the best available developer: Fix the checkout payment bug in the React frontend` → Ken Watanabe with ranking and reasoning |
| **7:40–8:10** | `Process this vendor spreadsheet and show invalid rows.` with **no** file, then with the sample file attached | Missing file → the agent asks (never invents data). With the sample CSV: 7 valid / 3 invalid with row numbers, cleaned file written |
| **8:10–9:00** | Tick **Load demo extension (WF011)** → ask `What is the total value of our inventory by category?` Then show `WF011.yaml` and the workbook row | The 11th workflow: one workbook row + one YAML file, **no new chatbot, router or Python**. It runs through the same agent and engine; the original ten are unchanged. Mention `test_a_twelfth_workflow_needs_only_a_workbook_row_and_a_spec` |
| **9:00–9:40** | Terminal: `pytest` | 681 tests, no network, no API key needed. Highlight: independent oracles, boundary tests, deliberately broken catalogs, UI tests. Real bugs found by tests are now regression tests. Key decisions: Excel authoritative, LLM only proposes, safe expressions, labelled fallback. Limitations: live LLM path verified via mocks (state clearly whether you ran `verify_live_llm.py`), simulated data, interpretations to confirm |

## Commands to have ready

```bash
pytest                                      # whole suite
pytest tests/test_workflows.py -v | head -40
python scripts/run_smoke_tests.py           # all 22 examples
python scripts/verify_live_llm.py           # only with a key (exit 2 = not verified); free-tier keys are rate-limited
git diff --stat                             # while adding WF011 files: nothing under src/ changes
EXTRA_CATALOG_DIRS=examples/extension_wf011 python scripts/validate_workflows.py
```

## If something goes wrong on camera

* *Order IDs look different* - use the ones in `data/fixtures/orders.json` (ORD-1001…ORD-1007).
* *LLM is slow or rate-limited* - in `auto` mode the response shows a "used the deterministic fallback" note; point it out as the designed behaviour. Switch the sidebar to *Deterministic* to continue.
* *A page looks stale* - press **Reset / new execution** in the sidebar.
* *Port busy* - `streamlit run app.py --server.port 8765`.

## Checklist before publishing the recording

* [ ] Header badge matches what you say (LLM vs deterministic).
* [ ] You showed at least: a successful run, a clarification, a conditional branch with values, a failure/partial case, the 11th workflow, the tests.
* [ ] No `.env` content, API key or personal data on screen.
* [ ] The Loom URL (when it exists) is added to the README and to the submission.
