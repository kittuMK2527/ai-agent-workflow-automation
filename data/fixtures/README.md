# Fixtures: the simulated business systems

Everything here is **demo data**. No file contains real customers, orders or prices, and no tool that reads
these files contacts a real system. They stand in for the systems the workbook refers to, and they contain
deliberate edge cases so the business rules can be tested.

| File | Plays the role of | Used by | Edge cases it contains |
|---|---|---|---|
| `inventory.csv` | product inventory CSV | WF001 | `SKU-1005` stock equals its minimum (not low); `SKU-1008` has zero stock |
| `products.csv`, `vendor_prices.csv` | internal prices / vendor price list | WF002 | exactly-10% difference (`SKU-1003`); 10.04% (`SKU-1002`); a negative difference; lower-case vendor SKU (`sku-1006`); one SKU on each side only |
| `vendor_file_sample.csv`, `.xlsx` | a vendor's messy file | WF003 | odd header names, blank SKU, blank name, both blank, padded whitespace, a fully blank line |
| `orders.json`, `shipments.json` | **SIMULATED** order system and carrier API | WF005 | shipped / delivered / processing / cancelled / delayed / on hold; one email with two orders; orders without a shipment |
| `catalog.csv` | product catalog | WF006, WF007 | SKU variants (`SKU-2001`, `sku2001`, `SKU 2001`), near-identical names, size/colour variants that are **not** duplicates, same name from two brands, an "Autumn 2026" collection |
| `keywords_sample.csv`, `categories.csv` | keyword list and category/target-page map | WF008 | duplicate keywords differing in case/spacing, all four intents, a keyword with no category |
| `employees.csv`, `open_tasks.csv` | **SIMULATED** HR / task database | WF009 | one employee on leave, one done task that must not count as workload, a skill nobody has |
| `workflow_execution_logs.csv` | workflow execution history | WF010 | one workflow at *exactly* 10% failure, one slow-but-reliable workflow, one failing workflow |

`workflow_execution_logs.csv` and `vendor_file_sample.xlsx` are produced by `python scripts/generate_fixtures.py`
(seeded: the output is byte-identical every run). The other files are hand-written.

Lexicons used by the rule-based tools live in `data/reference/` (`skill_lexicon.json`, `intent_lexicon.json`,
`attribute_lexicon.json`).
