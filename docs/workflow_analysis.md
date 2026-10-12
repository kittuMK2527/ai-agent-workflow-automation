# Workflow analysis

This document analyses the ten business workflows in the assignment workbook
(`AI_Agent_Workflow_Assessment (1).xlsx`, preserved unchanged as `data/workflows.xlsx`) and records how each one
was turned into an executable definition. The per-workflow sections are **generated from the same catalog the
engine runs** (`python scripts/generate_workflow_analysis.py`), so they cannot disagree with the code.

## 1. What is in the workbook

| Sheet | Role | Content |
|---|---|---|
| `Workflows` | workflow definitions | 10 rows (WF001-WF010) × 8 columns: `Workflow_ID`, `Workflow_Name`, `Trigger`, `Inputs`, `Steps`, `Decision_Logic`, `Tools_Required`, `Expected_Output` |
| `Test_Questions` | sample requests | 10 rows: `Workflow_ID`, `Test_Request`, `What_To_Check` |

There are no merged cells, hidden sheets, tables, formulas, comments or defined names. Every business definition is
**free text inside a cell**:

* `Steps` is one cell of steps separated by `→` (56 steps in total across the ten workflows).
* `Inputs` and `Tools_Required` are lists separated by `;`.
* `Decision_Logic` is a sentence of prose, not a formal rule.
* There is **no column for failure conditions or recovery behaviour**, and tool names are generic phrases
  ("calculator", "LLM", "CSV reader") rather than identifiers.

The loader (`src/excel_loader.py`) therefore finds sheets and header rows by content, tolerates merged cells,
multi-row workflows, line-break or numbered steps and unknown columns, and records the sheet and row of every workflow.
The same code handles the assignment workbook and the extension workbook in `examples/extension_wf011/`.

## 2. From free text to an executable definition

The workbook says **what** a workflow does; it cannot say **how** a machine should do it. Each workflow therefore has
a second file, `data/specs/<ID>.yaml` (the *execution spec*), that adds only what the Excel cannot express:

| Excel gives | The spec adds |
|---|---|
| `Inputs` phrases | typed inputs (type, required, default, limits) with `covers:` pointing back at the Excel phrase |
| `Steps` phrases | executable steps (tool + parameters) with `excel_step:` naming the Excel step each one implements |
| `Decision_Logic` prose | safe expressions (`row.current_stock < row.threshold`) used by filters, guards and branches |
| `Tools_Required` phrases | `tool_map:` from each phrase to registered tools |
| `Expected_Output` | the structured final result and headline |

**The Excel stays authoritative.** At load time the registry refuses a workflow if the spec's `excel_step` sequence does not
equal the workbook's Steps cell, if an Excel input or tool is not accounted for, or if the spec claims to cover something the
workbook does not say. Editing a step in the workbook without updating the spec is therefore a loud, specific error.

Input coverage, tool mapping and the other checks are listed in `docs/validation_report.md`.

## 3. Summary

| ID | Workflow | Trigger (Excel) | Excel steps | Executable steps | Excel tools | Conditions |
|---|---|---|---|---|---|---|

| WF001 | Inventory Restock Check | User asks which products need restocking | 5 | 6 | CSV reader; calculator | 2 |
| WF002 | Product Price Validation | User asks to validate product prices | 5 | 7 | CSV reader; calculator | 1 |
| WF003 | Vendor File Processing | User provides a vendor file for processing | 6 | 8 | Excel/CSV parser; data validation | 7 |
| WF004 | Product Description Generator | User asks to generate product content | 5 | 10 | LLM; text validation | 7 |
| WF005 | Customer Order Status | User asks for an order status | 5 | 8 | Order database/API; shipment lookup | 5 |
| WF006 | Duplicate Product Detection | User asks to find duplicate products | 6 | 6 | CSV/database reader; text similarity | 1 |
| WF007 | Marketing Campaign Brief | User asks to create a campaign brief | 6 | 9 | LLM; product data reader | 5 |
| WF008 | SEO Keyword Classification | User uploads or provides a keyword list | 6 | 9 | CSV reader; LLM/classifier | 3 |
| WF009 | Employee Task Assignment | Manager asks the agent to assign a task | 6 | 13 | Employee/task database; ranking logic | 6 |
| WF010 | Workflow Performance Report | User asks for a performance report | 6 | 19 | CSV/database reader; calculator; reporting | 6 |

## 4. Per-workflow analysis

### WF001 · Inventory Restock Check

*Source:* `workflows.xlsx / Workflows row 2`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks which products need restocking |
| Inputs | Product inventory CSV; minimum stock threshold |
| Steps | Load inventory → compare current stock with minimum threshold → identify low-stock products → calculate reorder quantity → generate restock list |
| Decision_Logic | If current_stock < minimum_stock, mark product for restock |
| Tools_Required | CSV reader; calculator |
| Expected_Output | List of products requiring restock with current stock, threshold, and suggested reorder quantity |

*Excel test question:* “Which products need restocking?” (Tests workflow selection and threshold logic)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `inventory_file` | file | no | inventory.csv | Product inventory CSV | Product inventory CSV (needs sku, product_name, current_stock and minimum_stock columns). |
| `minimum_stock` | number | no |  | minimum stock threshold | Global minimum stock threshold; overrides every product's own minimum. |
| `restock_multiplier` | number | no | 2.0 |  | Target stock as a multiple of the minimum, used to size the reorder quantity. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Load inventory | `load_inventory` | `csv_reader` | must satisfy `output.row_count > 0` |
| compare current stock with minimum threshold | `compare_stock` | `table_query` |  |
| identify low-stock products | `identify_low_stock` | `table_query` |  |
| calculate reorder quantity | `calculate_reorder` | `table_query` |  |
| generate restock list | `generate_restock_list` | `table_query` |  |
| generate restock list | `totals` | `table_aggregate` |  |

**Tool mapping**

- “CSV reader” → `csv_reader`
- “calculator” → `table_query`, `table_aggregate`

**Decision logic as executable conditions**

- *Excel rule: current_stock < minimum_stock* (`identify_low_stock`): `row.current_stock < row.threshold`

**Success output**

Outcomes: `completed`. Result fields: `products_checked`, `products_to_restock`, `total_units_to_order`, `threshold_basis`, `reorder_rule`, `restock_list`

**Failure and recovery**

- Rows with a missing or non-numeric stock value are excluded and reported as a data-quality warning; they are never counted as low or as fine.
- A missing/unreadable inventory file stops the workflow with a message that names the file.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- The workbook does not say how the reorder quantity is calculated. Interpretation: target stock = minimum stock x restock_multiplier (default 2.0); suggested reorder quantity = ceil(target stock - current stock).
- 'minimum stock threshold' is read from each product's minimum_stock column. A threshold given in the request overrides it for every product.
- The comparison is strict (current_stock < minimum_stock): a product exactly at its minimum is not flagged, as the Excel rule says '<'.

### WF002 · Product Price Validation

*Source:* `workflows.xlsx / Workflows row 3`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks to validate product prices |
| Inputs | Product CSV; vendor price list |
| Steps | Load product prices → match products by SKU → compare internal and vendor prices → calculate percentage difference → flag exceptions |
| Decision_Logic | Flag when price difference exceeds 10% |
| Tools_Required | CSV reader; calculator |
| Expected_Output | Validation report showing matched products, price differences, and exceptions |

*Excel test question:* “Find products where vendor price differs by more than 10%.” (Tests comparison and decision logic)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `products_file` | file | no | products.csv | Product CSV | Internal product CSV (sku, price). |
| `vendor_prices_file` | file | no | vendor_prices.csv | vendor price list | Vendor price list CSV (sku, vendor_price). |
| `threshold_pct` | number | no | 10 |  | Flag products whose price difference is larger than this percentage. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Load product prices | `load_products` | `csv_reader` |  |
| Load product prices | `load_vendor_prices` | `csv_reader` |  |
| match products by SKU | `match_by_sku` | `table_join` |  |
| compare internal and vendor prices | `compare_prices` | `table_query` |  |
| calculate percentage difference | `calculate_percentage` | `table_query` |  |
| flag exceptions | `flag_exceptions` | `table_query` |  |
| flag exceptions | `list_exceptions` | `table_query` |  |

**Tool mapping**

- “CSV reader” → `csv_reader`
- “calculator” → `table_query`, `table_join`

**Decision logic as executable conditions**

- *Exceptions only* (`list_exceptions`): `row.is_exception`

**Success output**

Outcomes: `completed`. Result fields: `matched_products`, `exceptions_found`, `threshold_pct`, `exceptions`, `all_comparisons`, `internal_only_skus`, `vendor_only_skus`

**Failure and recovery**

- Rows whose prices are missing, non-numeric or zero cannot be compared; they are excluded and reported.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Percentage difference = (vendor price - internal price) / internal price x 100, so the internal price is the base. The sign shows the direction (positive = vendor is higher).
- 'Exceeds 10%' is applied to the absolute difference and is strict: exactly 10.00% is not flagged. The percentage is rounded to 2 decimals before the comparison so floating-point noise cannot flip the result.
- The 10% threshold comes from the Excel decision logic; a different threshold in the request overrides it (threshold_pct).
- Products present in only one of the two files are reported as unmatched, not silently dropped.

### WF003 · Vendor File Processing

*Source:* `workflows.xlsx / Workflows row 4`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User provides a vendor file for processing |
| Inputs | CSV/XLSX file containing vendor product data |
| Steps | Read file → detect columns → normalize column names → validate required fields → identify invalid rows → produce cleaned dataset |
| Decision_Logic | Rows missing SKU or product name are invalid |
| Tools_Required | Excel/CSV parser; data validation |
| Expected_Output | Cleaned file plus validation summary and invalid-row report |

*Excel test question:* “Process this vendor spreadsheet and show invalid rows.” (Tests file ingestion and validation)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `vendor_file` | file | yes |  | CSV/XLSX file containing vendor product data | Vendor CSV or XLSX file (attach it or name a sample file). |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Read file | `read_file` | `file_parser` | must satisfy `output.row_count > 0` |
| detect columns | `detect_columns` | `detect_columns` |  |
| normalize column names | `normalize_columns` | `normalize_columns` |  |
| validate required fields | `required_fields_present` | decision | SKU or product-name column not found: `not (exists(steps.detect_columns.detected.sku) and exists(steps.detect_columns.detected.product_name))` → fail; Required columns found: `otherwise` → continue |
| identify invalid rows | `identify_invalid_rows` | `data_validator` |  |
| produce cleaned dataset | `export_cleaned` | `dataset_exporter` |  |
| produce cleaned dataset | `build_invalid_report` | `table_query` | runs only if `steps.identify_invalid_rows.invalid_count > 0` |
| produce cleaned dataset | `export_invalid_report` | `dataset_exporter` | runs only if `steps.identify_invalid_rows.invalid_count > 0` |

**Tool mapping**

- “Excel/CSV parser” → `file_parser`, `detect_columns`, `normalize_columns`, `dataset_exporter`
- “data validation” → `data_validator`

**Decision logic as executable conditions**

- *SKU or product-name column not found* (`required_fields_present`): `not (exists(steps.detect_columns.detected.sku) and exists(steps.detect_columns.detected.product_name))` → fail
- *Required columns found* (`required_fields_present`): `otherwise` → continue
- *Identify invalid rows* (`identify_invalid_rows`): `is_blank(row.sku)`
- *Identify invalid rows* (`identify_invalid_rows`): `is_blank(row.product_name)`
- *Run 'Build invalid-row report' only if* (`build_invalid_report`): `steps.identify_invalid_rows.invalid_count > 0`
- *Run 'Export invalid-row report' only if* (`export_invalid_report`): `steps.identify_invalid_rows.invalid_count > 0`

**Success output**

Outcomes: `completed`. Result fields: `validation_summary`, `detected_columns`, `invalid_row_report`, `cleaned_file`, `invalid_report_file`, `cleaned_dataset`

**Failure and recovery**

- If the file has no recognisable SKU or product-name column the workflow stops with a user-correctable message listing the accepted header names; no rows are guessed.
- Unreadable, empty or oversized files stop the workflow with a clear message.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- The workbook requires a SKU and a product name. Header names vary between vendors, so columns are detected from synonym lists (this file) and renamed to canonical names: sku, product_name, price, quantity, brand, category.
- 'description' is deliberately NOT accepted as a synonym for product name: in vendor files it usually holds long marketing text.
- Cleaning = renamed columns, trimmed whitespace, invalid rows removed. Values such as prices are not rewritten.
- A row is invalid when its SKU or its product name is blank (Excel decision logic). Both reasons are reported when both apply.

### WF004 · Product Description Generator

*Source:* `workflows.xlsx / Workflows row 5`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks to generate product content |
| Inputs | Product name; category; attributes; material; color; target audience |
| Steps | Validate required attributes → create product description → generate short description → generate SEO title → generate meta description |
| Decision_Logic | Do not invent missing product attributes; explicitly mark missing information |
| Tools_Required | LLM; text validation |
| Expected_Output | Product description, short description, SEO title, and meta description |

*Excel test question:* “Generate SEO content for this product.” (Tests LLM workflow and missing-data handling)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `product_name` | string | yes |  | Product name | Name of the product to write about. |
| `category` | string | no |  | category | Product category, e.g. Accessories. |
| `attributes` | list | no |  | attributes | Key features or attributes, e.g. waterproof, lightweight. |
| `material` | string | no |  | material | Material the product is made from. |
| `color` | string | no |  | color | Product colour. |
| `target_audience` | string | no |  | target audience | Who the product is for. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Validate required attributes | `validate_attributes` | `field_validator` |  |
| Validate required attributes | `missing_information_policy` | decision | Product name missing: `not steps.validate_attributes.is_valid` → await_input; Some expected attributes are missing: `len(steps.validate_attributes.missing_expected) > 0` → continue; All attributes provided: `otherwise` → continue |
| create product description | `write_description` | `content_generator` |  |
| create product description | `check_description` | `text_validator` | must satisfy `output.valid` |
| generate short description | `write_short_description` | `content_generator` |  |
| generate short description | `check_short_description` | `text_validator` | must satisfy `output.valid` |
| generate SEO title | `write_seo_title` | `content_generator` |  |
| generate SEO title | `check_seo_title` | `text_validator` | must satisfy `output.valid` |
| generate meta description | `write_meta_description` | `content_generator` |  |
| generate meta description | `check_meta_description` | `text_validator` | must satisfy `output.valid` |

**Tool mapping**

- “LLM” → `content_generator`
- “text validation” → `text_validator`

**Decision logic as executable conditions**

- *Product name missing* (`missing_information_policy`): `not steps.validate_attributes.is_valid` → await_input
- *Some expected attributes are missing* (`missing_information_policy`): `len(steps.validate_attributes.missing_expected) > 0` → continue
- *All attributes provided* (`missing_information_policy`): `otherwise` → continue

**Success output**

Outcomes: `completed`. Result fields: `product_name`, `description`, `short_description`, `seo_title`, `meta_description`, `missing_information`, `attributes_used`, `generation_modes`, `character_counts`

**Failure and recovery**

- LLM timeouts/rate limits are retried (bounded) in strict LLM mode; in auto mode the labelled template is used instead.
- Generated text that fails validation fails the workflow with the validator's reasons.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Product name is the only hard-required input: without it there is nothing to write about. Category, attributes, material, colour and target audience are 'expected' attributes.
- Excel decision logic: 'Do not invent missing product attributes; explicitly mark missing information'. Missing expected attributes do NOT block the workflow; they are listed under missing_information, the copy leaves them out, and a warning is shown.
- Each generated text is checked by the text validator (length limits, product name in the SEO title, and no colour/material words that are not in the supplied facts). A failed check fails the workflow rather than returning unchecked copy.
- The text is produced by the LLM when one is configured, otherwise by a labelled built-in template; the result states which.

### WF005 · Customer Order Status

*Source:* `workflows.xlsx / Workflows row 6`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks for an order status |
| Inputs | Order ID or customer email |
| Steps | Validate identifier → search order data → retrieve order status → retrieve shipment information → summarize current status |
| Decision_Logic | If no order is found, ask for another identifier |
| Tools_Required | Order database/API; shipment lookup |
| Expected_Output | Order status, items, shipment status, and tracking information when available |

*Excel test question:* “Where is order ORD-1001?” (Tests lookup and missing-order handling)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `identifier` | string | yes |  | Order ID or customer email | Order ID (like ORD-1001) or the customer's email address. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Validate identifier | `validate_identifier` | `identifier_validator` |  |
| Validate identifier | `identifier_is_valid` | decision | Not an order ID or email: `not steps.validate_identifier.valid` → await_input; Valid identifier: `otherwise` → continue |
| search order data | `search_orders` | `order_search` |  |
| search order data | `order_found` | decision | No order found: `steps.search_orders.count == 0` → await_input; Order(s) found: `otherwise` → continue |
| retrieve order status | `get_order_status` | `order_details` | must satisfy `output.count > 0` |
| retrieve shipment information | `lookup_shipments` | `shipment_lookup` | failure does not stop the workflow |
| summarize current status | `combine_order_and_shipment` | `table_join` |  |
| summarize current status | `summarize_status` | `table_query` |  |

**Tool mapping**

- “Order database/API” → `identifier_validator`, `order_search`, `order_details`
- “shipment lookup” → `shipment_lookup`

**Decision logic as executable conditions**

- *Not an order ID or email* (`identifier_is_valid`): `not steps.validate_identifier.valid` → await_input
- *Valid identifier* (`identifier_is_valid`): `otherwise` → continue
- *No order found* (`order_found`): `steps.search_orders.count == 0` → await_input
- *Order(s) found* (`order_found`): `otherwise` → continue

**Success output**

Outcomes: `completed`. Result fields: `orders`, `order_count`, `shipment_lookup_available`, `data_source`

**Failure and recovery**

- Order search / details: transient errors are retried up to 3 times; then the workflow fails.
- Shipment lookup: transient errors are retried up to 3 times; then the step is marked failed and the workflow continues (partial result).
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- The identifier is an order ID (ORD-1001, case/hyphen tolerant) or an email address. An email can match several orders; all are returned.
- Excel decision logic: 'If no order is found, ask for another identifier'. This pauses the workflow in an 'awaiting input' state and asks for a new identifier instead of failing or guessing.
- The order system and shipment lookup are SIMULATED (fixture data). Every result is labelled simulated.
- Shipment lookup is not essential to know the order status, so if the (simulated) shipment service stays unavailable after retries the workflow still returns the order status and finishes as PARTIAL with a warning.

### WF006 · Duplicate Product Detection

*Source:* `workflows.xlsx / Workflows row 7`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks to find duplicate products |
| Inputs | Product catalog |
| Steps | Load products → normalize names/SKUs → compare identifiers → compare product attributes → group likely duplicates → assign confidence |
| Decision_Logic | Exact SKU match is a definite duplicate; high attribute similarity is a possible duplicate |
| Tools_Required | CSV/database reader; text similarity |
| Expected_Output | Duplicate groups with matching fields and confidence level |

*Excel test question:* “Find likely duplicate products in the catalog.” (Tests similarity and confidence)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `catalog_file` | file | no | catalog.csv | Product catalog | Product catalog CSV (sku, product_name and optional brand, category, color, size, material). |
| `similarity_threshold` | number | no | 0.85 |  | Minimum weighted similarity for a possible duplicate. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Load products | `load_products` | `csv_reader` | must satisfy `output.row_count > 1` |
| normalize names/SKUs | `normalize` | `table_query` |  |
| compare identifiers | `compare_identifiers` | `record_matcher` |  |
| compare product attributes | `compare_attributes` | `record_matcher` |  |
| group likely duplicates | `group_duplicates` | `group_matches` |  |
| assign confidence | `assign_confidence` | `table_query` |  |

**Tool mapping**

- “CSV/database reader” → `csv_reader`
- “text similarity” → `record_matcher`, `group_matches`

**Decision logic as executable conditions**


**Success output**

Outcomes: `completed`. Result fields: `products_checked`, `definite_groups`, `possible_groups`, `pairs_compared`, `pairs_skipped_for_conflicting_attributes`, `duplicate_groups`

**Failure and recovery**

- Pairwise comparison is limited to 2,000 products; a larger catalog stops the workflow with a message instead of running for a long time.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Definite duplicate (Excel): rows whose SKU is identical after normalisation (case, spaces and punctuation ignored: 'SKU-2001', 'sku2001' and 'SKU 2001' are the same SKU).
- Possible duplicate (Excel: 'high attribute similarity'): the workbook does not define 'high'. Interpretation: weighted similarity >= similarity_threshold (default 0.85) over product name (text similarity, weight 0.7), category (0.15) and material (0.15).
- Variants are not duplicates: if two products both have a value for brand, color or size and the values differ, the pair is excluded ('Cotton Crew T-Shirt - White (M)' vs '(L)').
- Confidence: definite = 1.0; possible = lowest similarity inside the group; labelled 'high' when >= 0.95, otherwise 'medium'. Blank attribute values are treated as unknown, not as matching.

### WF007 · Marketing Campaign Brief

*Source:* `workflows.xlsx / Workflows row 8`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks to create a campaign brief |
| Inputs | Campaign goal; product list; target audience; promotion; dates |
| Steps | Validate inputs → identify campaign objective → summarize products → create messaging → create channel recommendations → create campaign checklist |
| Decision_Logic | If campaign goal or dates are missing, request them before generating the brief |
| Tools_Required | LLM; product data reader |
| Expected_Output | Structured campaign brief with objective, audience, messaging, channels, timeline, and checklist |

*Excel test question:* “Create a campaign brief for the new collection.” (Tests structured content generation)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `campaign_goal` | string | yes |  | Campaign goal | What the campaign should achieve, e.g. "increase autumn collection sales". |
| `products` | list | yes |  | product list | SKUs, product names or a collection name to feature. |
| `target_audience` | string | no |  | target audience | Who the campaign is aimed at. |
| `promotion` | string | no |  | promotion | Offer or promotion, e.g. "15% off". |
| `start_date` | date | yes |  | dates | Campaign start date (YYYY-MM-DD). |
| `end_date` | date | yes |  | dates | Campaign end date (YYYY-MM-DD). |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Validate inputs | `validate_inputs` | `field_validator` |  |
| Validate inputs | `required_inputs_present` | decision | Required information missing: `not steps.validate_inputs.is_valid` → await_input; End date before start date: `inputs.end_date < inputs.start_date` → await_input; All required information present: `otherwise` → continue |
| identify campaign objective | `identify_objective` | `content_generator` |  |
| summarize products | `load_products` | `product_data_reader` |  |
| summarize products | `products_found` | decision | None of the products found: `steps.load_products.count == 0` → await_input; Products found: `otherwise` → continue |
| summarize products | `summarize_products` | `table_query` |  |
| create messaging | `create_messaging` | `content_generator` |  |
| create channel recommendations | `recommend_channels` | `content_generator` |  |
| create campaign checklist | `create_checklist` | `content_generator` |  |

**Tool mapping**

- “LLM” → `content_generator`
- “product data reader” → `product_data_reader`

**Decision logic as executable conditions**

- *Required information missing* (`required_inputs_present`): `not steps.validate_inputs.is_valid` → await_input
- *End date before start date* (`required_inputs_present`): `inputs.end_date < inputs.start_date` → await_input
- *All required information present* (`required_inputs_present`): `otherwise` → continue
- *None of the products found* (`products_found`): `steps.load_products.count == 0` → await_input
- *Products found* (`products_found`): `otherwise` → continue

**Success output**

Outcomes: `completed`. Result fields: `objective`, `objective_type`, `target_audience`, `promotion`, `timeline`, `products`, `messaging`, `channels`, `checklist`, `generation_modes`

**Failure and recovery**

- None of the requested products found in the catalog: the workflow asks the user for valid products instead of writing a brief about nothing.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Excel decision logic: 'If campaign goal or dates are missing, request them before generating the brief'. Goal, start date and end date are required; so is the product list because the 'summarize products' step cannot run without it. Target audience and promotion are optional and shown as 'Not specified' rather than invented.
- 'dates' is interpreted as a start date and an end date (ISO, YYYY-MM-DD); an end date before the start date is sent back to the user.
- 'product list' may be SKUs, product names, or a collection name; items are looked up in the catalog and unknown items are reported.
- Objective, messaging, channels and checklist come from the LLM when one is configured, otherwise from labelled built-in templates. The template tone line says it is a default.

### WF008 · SEO Keyword Classification

*Source:* `workflows.xlsx / Workflows row 9`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User uploads or provides a keyword list |
| Inputs | Keyword CSV; product/category information |
| Steps | Read keywords → remove duplicates → classify search intent → map keywords to categories → identify high-priority keywords → export results |
| Decision_Logic | Classify each keyword as informational, commercial, transactional, or navigational |
| Tools_Required | CSV reader; LLM/classifier |
| Expected_Output | Keyword report with intent, category, priority, and recommended target page |

*Excel test question:* “Classify these keywords and map them to pages.” (Tests classification and mapping)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `keywords_file` | file | no |  | Keyword CSV | Keyword CSV/XLSX (column "keyword", optional "search_volume"). |
| `keywords` | list | no |  | Keyword CSV | Keywords typed directly into the request. |
| `categories_file` | file | no | categories.csv | product/category information | Product category file (category, target_page, terms). |
| `high_volume_threshold` | number | no | 1000 |  | Minimum monthly search volume for a keyword to count as high priority. |

*At least one of* `keywords_file`, `keywords` *is required.*

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Read keywords | `read_keywords` | `keyword_reader` |  |
| remove duplicates | `remove_duplicates` | `table_dedupe` |  |
| classify search intent | `classify_intent` | `keyword_classifier` | must satisfy `output.count == len(steps.remove_duplicates.rows)` |
| classify search intent | `intent_summary` | `table_aggregate` |  |
| map keywords to categories | `load_categories` | `csv_reader` |  |
| map keywords to categories | `map_categories` | `category_mapper` |  |
| identify high-priority keywords | `score_priority` | `table_query` |  |
| identify high-priority keywords | `high_priority` | `table_query` |  |
| export results | `export_results` | `dataset_exporter` |  |

**Tool mapping**

- “CSV reader” → `csv_reader`, `keyword_reader`, `table_dedupe`, `dataset_exporter`
- “LLM/classifier” → `keyword_classifier`, `category_mapper`

**Decision logic as executable conditions**

- *Remove duplicates* (`remove_duplicates`): `norm_text(row.keyword)`
- *High-priority keywords* (`high_priority`): `row.priority == 'high'`

**Success output**

Outcomes: `completed`. Result fields: `keywords_classified`, `duplicates_removed`, `intent_counts`, `high_priority_keywords`, `keyword_report`, `exported_file`

**Failure and recovery**

- No keywords, a missing keyword column or an unreadable file stop the workflow with a clear message.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Keywords can be attached as a CSV/XLSX file (column 'keyword', optional 'search_volume') or typed into the request. At least one is required; there is no silent default keyword list.
- Duplicates are detected ignoring case, punctuation and spacing ('Buy Leather Belt Online' = 'buy leather belt online'); the row with the larger search volume is kept.
- Intent uses a transparent rule lexicon (data/reference/intent_lexicon.json), not an LLM: navigational > informational (question words) > transactional > commercial. A keyword with no signal words is classified commercial with LOW confidence and says so.
- The workbook does not define 'high priority'. Interpretation: intent is transactional or commercial AND the keyword maps to a category AND (search volume >= high_volume_threshold, or no volume was supplied). Informational or unmapped keywords are low; the rest medium.
- The workbook lists no export tool for the 'export results' step; the dataset exporter (writes a CSV into outputs/) implements it and is mapped under the 'CSV reader' entry.
- Category mapping scores each category by how many of its terms appear in the keyword (data/fixtures/categories.csv); unmapped keywords are reported, not forced into a category.

### WF009 · Employee Task Assignment

*Source:* `workflows.xlsx / Workflows row 10`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | Manager asks the agent to assign a task |
| Inputs | Task description; employee list; skills; workload; priority; deadline |
| Steps | Understand task requirements → compare employee skills → check current workload → rank candidates → select employee → generate assignment summary |
| Decision_Logic | Prefer employees with required skills and available capacity; escalate if no suitable employee exists |
| Tools_Required | Employee/task database; ranking logic |
| Expected_Output | Recommended employee, reasoning, priority, deadline, and task summary |

*Excel test question:* “Assign this urgent task to the best available developer.” (Tests ranking and decision logic)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `task_description` | string | yes |  | Task description | What the task is about, e.g. "Fix the checkout payment bug in the React frontend". |
| `required_skills` | list | no |  | skills | Skills the assignee must have (otherwise detected from the task description). |
| `role` | string | no |  |  | Only consider employees with this role, e.g. developer. |
| `priority` | choice | no |  | priority | Task priority. |
| `deadline` | date | no |  | deadline | Deadline (YYYY-MM-DD). |
| `min_available_hours` | number | no | 4 |  | Minimum free hours this week for an employee to count as available. |

*Data source:* Employee/task database (simulated): Employees with skills and weekly capacity, and open tasks with estimated hours. (covers: employee list, workload)

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Understand task requirements | `analyze_task` | `task_analyzer` |  |
| Understand task requirements | `skills_known` | decision | No required skills could be determined: `len(steps.analyze_task.required_skills) == 0` → await_input; Required skills determined: `otherwise` → continue |
| compare employee skills | `load_employees` | `employee_db` | must satisfy `output.count > 0` |
| compare employee skills | `compare_skills` | `table_query` |  |
| check current workload | `load_open_tasks` | `employee_db` |  |
| check current workload | `sum_workload` | `table_aggregate` |  |
| check current workload | `join_workload` | `table_join` |  |
| check current workload | `compute_capacity` | `table_query` |  |
| rank candidates | `rank_candidates` | `table_query` |  |
| select employee | `suitable_candidates` | `table_query` |  |
| select employee | `suitable_exists` | decision | No suitable employee exists: `steps.suitable_candidates.count == 0` → end; At least one suitable employee: `otherwise` → continue |
| select employee | `pick_employee` | `table_query` |  |
| generate assignment summary | `assignment_summary` | `report_builder` |  |

**Tool mapping**

- “Employee/task database” → `employee_db`, `task_analyzer`
- “ranking logic” → `table_query`, `table_aggregate`, `table_join`, `report_builder`

**Decision logic as executable conditions**

- *No required skills could be determined* (`skills_known`): `len(steps.analyze_task.required_skills) == 0` → await_input
- *Required skills determined* (`skills_known`): `otherwise` → continue
- *Suitable candidates* (`suitable_candidates`): `row.eligible`
- *No suitable employee exists* (`suitable_exists`): `steps.suitable_candidates.count == 0` → end
- *At least one suitable employee* (`suitable_exists`): `otherwise` → continue

**Success output**

Outcomes: `completed`, `escalated`. Result fields: `recommended_employee`, `employee_id`, `priority`, `deadline`, `required_skills`, `task_summary`, `ranking`

**Failure and recovery**

- No suitable employee ends the workflow with outcome 'escalated' (a business result, not an error) and explains why each candidate was excluded.
- Database outages are retried (bounded) and then fail the workflow.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Required skills are taken from the request ('skills') or detected by keyword matching against data/reference/skill_lexicon.json - not by an LLM. If none can be determined the workflow asks for them; skills are never guessed.
- Excel decision logic: 'Prefer employees with required skills and available capacity; escalate if no suitable employee exists'. A suitable employee has ALL required skills, is active, and has at least min_available_hours of free capacity this week.
- Free capacity = weekly capacity hours - estimated hours of open tasks assigned to that employee (done tasks do not count). Employees on leave have 0 free hours.
- Ranking score = 100 x (0.6 x skill match + 0.4 x min(free hours / weekly capacity, 1)); candidates that are not suitable are listed below the suitable ones with the reason they were excluded.
- The employee list and workload come from the SIMULATED employee/task database (fixture data).
- Priority and deadline are carried into the assignment summary; the workbook gives no rule that changes ranking based on them, so none is invented.

### WF010 · Workflow Performance Report

*Source:* `workflows.xlsx / Workflows row 11`

**Excel definition (verbatim)**

| Column | Text |
|---|---|
| Trigger | User asks for a performance report |
| Inputs | Workflow execution logs |
| Steps | Load execution logs → calculate success/failure rate → calculate average execution time → identify frequent errors → identify slow steps → generate recommendations |
| Decision_Logic | Flag workflows with failure rate above 10% or average execution time above defined threshold |
| Tools_Required | CSV/database reader; calculator; reporting |
| Expected_Output | Performance summary with metrics, problem areas, and improvement recommendations |

*Excel test question:* “Which workflows are failing most often?” (Tests aggregation and reporting)

**Inputs (executable)**

| Name | Type | Required | Default | Covers (Excel) | Meaning |
|---|---|---|---|---|---|
| `logs_file` | file | no | workflow_execution_logs.csv | Workflow execution logs | Workflow execution log CSV. |
| `failure_rate_threshold_pct` | number | no | 10 |  | Flag workflows whose failure rate is above this percentage. |
| `slow_run_threshold_sec` | number | no | 30 |  | Flag workflows whose average run time is above this many seconds. |
| `slow_step_threshold_sec` | number | no | 5 |  | A step is slow when its average duration is above this many seconds. |
| `top_n` | integer | no | 5 |  | How many frequent errors / slow steps to list. |

**Steps: Excel step → executable steps**

| Excel step | Executable step | Tool | Notes |
|---|---|---|---|
| Load execution logs | `load_logs` | `csv_reader` | must satisfy `output.row_count > 0` |
| calculate success/failure rate | `build_run_table` | `table_aggregate` |  |
| calculate success/failure rate | `mark_failed_runs` | `table_query` |  |
| calculate success/failure rate | `count_runs` | `table_aggregate` |  |
| calculate success/failure rate | `failure_rates` | `table_query` |  |
| calculate average execution time | `average_run_time` | `table_aggregate` |  |
| calculate average execution time | `join_run_time` | `table_join` |  |
| calculate average execution time | `flag_workflows` | `table_query` |  |
| identify frequent errors | `error_rows` | `table_query` |  |
| identify frequent errors | `count_errors` | `table_aggregate` |  |
| identify frequent errors | `frequent_errors` | `table_query` |  |
| identify frequent errors | `top_error_by_workflow` | `table_aggregate` |  |
| identify slow steps | `step_durations` | `table_aggregate` |  |
| identify slow steps | `slow_steps` | `table_query` |  |
| identify slow steps | `slowest_by_workflow` | `table_aggregate` |  |
| generate recommendations | `flagged_only` | `table_query` |  |
| generate recommendations | `attach_top_errors` | `table_join` |  |
| generate recommendations | `attach_slowest_steps` | `table_join` |  |
| generate recommendations | `build_recommendations` | `table_query` |  |

**Tool mapping**

- “CSV/database reader” → `csv_reader`
- “calculator” → `table_aggregate`, `table_query`, `table_join`
- “reporting” → `report_builder`

**Decision logic as executable conditions**

- *Collapse step logs into runs* (`build_run_table`): `row.step_status == 'failed'`
- *Count runs and failures per workflow* (`count_runs`): `row.run_failed`
- *Select failed steps with an error message* (`error_rows`): `row.step_status == 'failed' and not is_blank(row.error_message)`
- *Slow steps (average duration above threshold)* (`slow_steps`): `row.avg_step_sec > vars.slow_sec`
- *Flagged workflows* (`flagged_only`): `row.flagged`

**Success output**

Outcomes: `completed`. Result fields: `thresholds`, `runs_analyzed`, `workflow_metrics`, `recommendations`, `frequent_errors`, `slow_steps`

**Failure and recovery**

- Rows with non-numeric durations are treated as missing and reported; an unreadable or incomplete log file stops the workflow with the missing column names.
- Engine defaults apply to every step: stop on failure and keep collected results; retry only transient tool errors (3 attempts); validation errors are never retried.

**Interpretations (where the Excel is silent or vague)**

- Input is a step-level execution log (run_id, workflow_id, step_name, step_status, step_duration_sec, error_message). A run FAILED if any of its steps failed; run duration = sum of its step durations. The default log is simulated history (data/fixtures/workflow_execution_logs.csv, generated by scripts/generate_fixtures.py).
- Excel decision logic: 'Flag workflows with failure rate above 10% or average execution time above defined threshold'. The failure threshold (10%) is from the workbook; the workbook does not define the time threshold, so slow_run_threshold_sec defaults to 30 seconds and is an overridable input. Comparisons are strict (>).
- A 'slow step' is a step whose average duration exceeds slow_step_threshold_sec (default 5 s, also not defined in the workbook).
- Recommendations are rule-generated from the flags, the most frequent error and the slowest step of each flagged workflow; they are not LLM output.


## 5. Ambiguities, gaps and contradictions in the workbook

These are the places where the workbook is silent or vague and a decision had to be made. Every decision is also stored in
the workflow's `interpretation_notes` (shown in the app's catalog view). Nothing below was decided silently, and no decision
changes what the Excel *does* say.

| # | Where | What the Excel says / omits | Decision taken |
|---|---|---|---|
| 1 | WF001 | "calculate reorder quantity" - no formula | target stock = minimum × `restock_multiplier` (default 2.0); reorder = ceil(target − current). The multiplier is a visible, overridable input |
| 2 | WF001 | "minimum stock threshold" - per product or one global number? | Per-product `minimum_stock` column by default; a threshold in the request overrides it for every product |
| 3 | WF002 | "percentage difference" - relative to which price? | Relative to the **internal** price; signed; rounded to 2 decimals before comparing so float noise cannot flip a result |
| 4 | WF002 | "exceeds 10%" - strict? absolute? | Strictly greater than, on the absolute value (vendor cheaper counts too). Exactly 10.00% is **not** flagged |
| 5 | WF003 | which header names count as SKU / product name? | Synonym lists in the spec; "description" is deliberately not accepted as a product name |
| 6 | WF003 | "cleaned file" - what is cleaned? | Canonical column names, trimmed whitespace, invalid rows removed. Values are not rewritten. A real CSV is written to `outputs/` |
| 7 | WF004 | "required attributes" - which of the six inputs are required? | Only the product name blocks. The other five are *expected*: if missing they are listed under `missing_information`, left out of the copy, and a warning is shown (the Excel rule "do not invent … explicitly mark missing information") |
| 8 | WF004 | "text validation" - what to validate? | Length limits, product name in the SEO title, and no colour/material words that are not in the supplied facts |
| 9 | WF005 | identifier is "Order ID or customer email" - an email can match several orders | All matching orders are returned |
| 10 | WF005 | "If no order is found, ask for another identifier" | The workflow pauses in an *awaiting input* state and the conversation continues with the new identifier |
| 11 | WF006 | "high attribute similarity" is undefined | Weighted similarity ≥ 0.85 (product name 0.7, category 0.15, material 0.15); products that differ in brand, colour or size are variants, not duplicates |
| 12 | WF006 | "assign confidence" | definite = 1.0; possible = lowest similarity in the group, labelled *high* ≥ 0.95 else *medium* |
| 13 | WF007 | "dates" | A start and an end date (ISO). The Excel rule requires the goal and dates; the product list is also required because "summarize products" cannot run without it |
| 14 | WF008 | "high-priority keywords" is undefined | high = transactional/commercial intent AND mapped to a category AND (volume ≥ threshold, or no volume supplied) |
| 15 | WF008 | "LLM/classifier" | A transparent rule lexicon, not an LLM, so results are reproducible; keywords with no signal words are classified *commercial* with **low** confidence and flagged |
| 16 | WF009 | "employee list; skills; workload" are listed as *inputs* | They are data held in the (simulated) employee/task database, so they are modelled as a data source, not user inputs |
| 17 | WF009 | "available capacity", "suitable employee" | Suitable = all required skills, active, and ≥ `min_available_hours` (default 4) free this week. Score = 100 × (0.6 × skill match + 0.4 × free-capacity ratio) |
| 18 | WF010 | "defined threshold" for execution time - not defined | `slow_run_threshold_sec` default 30 s and `slow_step_threshold_sec` default 5 s, both overridable. The 10 % failure threshold is from the Excel |
| 19 | all | no failure / recovery behaviour specified | Engine defaults: fail fast, keep partial results, retry only transient tool errors (3 attempts); individual steps may opt into *continue* (WF005 shipment lookup) |
| 20 | all | tool names are generic phrases | Each phrase maps to registered tools in `tool_map`; generic table tools back "calculator"/"ranking logic" |
| 21 | tests | the `Test_Questions` are terse ("Assign this urgent task…") and often carry no data | The correct behaviour is to **ask** (WF003, WF004, WF007, WF008, WF009) or to use the named system data source (WF001, WF002, WF005, WF006, WF010). Complete versions of each request are provided as examples |

No contradictions between rows were found. The two places that *look* contradictory are not: WF003 and WF008 are triggered by a
user-supplied file whereas WF001/WF002/WF006/WF010 read named system data, which is why the former ask for a file when none is attached.

## 6. Validation result

See `docs/validation_report.md` (generated by `python scripts/validate_workflows.py --write`): all 10 workflows were extracted,
registered and statically validated with zero errors or warnings.
