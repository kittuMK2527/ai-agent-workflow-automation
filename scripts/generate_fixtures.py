"""Regenerate the deterministic generated fixtures.

* data/fixtures/workflow_execution_logs.csv  - simulated step-level execution history (WF010 input)
* data/fixtures/vendor_file_sample.xlsx      - XLSX variant of the messy vendor file (WF003 input)

Everything is seeded, so running this twice produces byte-identical files.
The hand-written fixtures (inventory.csv, orders.json, ...) are not touched.

    python scripts/generate_fixtures.py
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
SEED = 20261012
RUNS_PER_WORKFLOW = 30

# workflow id -> (name, [(step, mean seconds)], failures [(step index, message, count)])
PROFILE = {
    "WF001": ("Inventory Restock Check", [("Load inventory", 1.2), ("Compare stock with threshold", 0.8), ("Identify low-stock products", 0.5), ("Calculate reorder quantity", 0.6), ("Generate restock list", 0.9)],
              [(0, "Inventory file contained non-numeric stock values", 1)]),
    "WF002": ("Product Price Validation", [("Load product prices", 1.1), ("Match products by SKU", 0.9), ("Compare internal and vendor prices", 0.7), ("Calculate percentage difference", 0.6), ("Flag exceptions", 0.8)],
              [(0, "Vendor price list missing SKU column", 2), (1, "Duplicate SKUs in vendor price list", 1)]),
    "WF003": ("Vendor File Processing", [("Read file", 2.1), ("Detect columns", 1.0), ("Normalize column names", 0.9), ("Validate required fields", 1.4), ("Identify invalid rows", 2.2), ("Produce cleaned dataset", 6.2)],
              [(0, "Unsupported file encoding", 2), (3, "Required column not found: sku", 2)]),
    "WF004": ("Product Description Generator", [("Validate required attributes", 0.4), ("Create product description", 3.8), ("Generate short description", 2.9), ("Generate SEO title", 2.4), ("Generate meta description", 3.1)],
              [(1, "LLM rate limit exceeded", 1), (3, "Generated text exceeded length limit", 1)]),
    "WF005": ("Customer Order Status", [("Validate identifier", 0.2), ("Search order data", 1.4), ("Retrieve order status", 1.1), ("Retrieve shipment information", 9.0), ("Summarize current status", 0.5)],
              [(3, "Shipment API timeout after 3 attempts", 5), (1, "Order API returned HTTP 503", 1)]),
    "WF006": ("Duplicate Product Detection", [("Load products", 1.8), ("Normalize names/SKUs", 1.1), ("Compare identifiers", 2.4), ("Compare product attributes", 27.5), ("Group likely duplicates", 1.6), ("Assign confidence", 0.7)],
              [(3, "Catalog too large for pairwise comparison", 1)]),
    "WF007": ("Marketing Campaign Brief", [("Validate inputs", 0.3), ("Identify campaign objective", 3.0), ("Summarize products", 1.2), ("Create messaging", 4.2), ("Create channel recommendations", 3.5), ("Create campaign checklist", 3.9)],
              [(3, "LLM request timed out", 2)]),
    "WF008": ("SEO Keyword Classification", [("Read keywords", 0.9), ("Remove duplicates", 0.4), ("Classify search intent", 2.8), ("Map keywords to categories", 1.9), ("Identify high-priority keywords", 0.5), ("Export results", 0.8)],
              [(0, "Keyword column not found", 2)]),
    "WF009": ("Employee Task Assignment", [("Understand task requirements", 1.2), ("Compare employee skills", 0.9), ("Check current workload", 2.0), ("Rank candidates", 0.6), ("Select employee", 0.3), ("Generate assignment summary", 0.5)],
              [(2, "Employee database connection timeout", 3), (0, "Required skills could not be determined", 1)]),
    "WF010": ("Workflow Performance Report", [("Load execution logs", 2.2), ("Calculate success/failure rate", 1.5), ("Calculate average execution time", 0.8), ("Identify frequent errors", 1.1), ("Identify slow steps", 1.0), ("Generate recommendations", 0.9)],
              []),
}


def write_logs() -> int:
    rng = random.Random(SEED)
    start = datetime(2026, 9, 14, 8, 0)
    rows = []
    for w_index, (wid, (name, steps, failures)) in enumerate(PROFILE.items()):
        plan: dict[int, tuple[int, str]] = {}
        fail_runs = rng.sample(range(RUNS_PER_WORKFLOW), sum(c for _, _, c in failures))
        k = 0
        for step_idx, message, count in failures:
            for _ in range(count):
                plan[fail_runs[k]] = (step_idx, message)
                k += 1
        for r in range(RUNS_PER_WORKFLOW):
            run_id = f"RUN-{wid}-{r + 1:04d}"
            t = start + timedelta(hours=r * 21 + w_index * 3, minutes=rng.randint(0, 59))
            for s_idx, (step, mean) in enumerate(steps):
                fails_here = r in plan and plan[r][0] == s_idx
                dur = max(0.05, rng.gauss(mean, mean * 0.12)) * (1.6 if fails_here else 1.0)
                rows.append([run_id, wid, name, step, "failed" if fails_here else "completed", f"{dur:.2f}",
                             plan[r][1] if fails_here else "", t.strftime("%Y-%m-%d %H:%M:%S")])
                t += timedelta(seconds=dur)
                if fails_here:
                    break
    path = FIXTURES / "workflow_execution_logs.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["run_id", "workflow_id", "workflow_name", "step_name", "step_status", "step_duration_sec", "error_message", "started_at"])
        w.writerows(rows)
    return len(rows)


def write_vendor_xlsx() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendor Products"
    ws.append(["Part Number", "Product Name", "Wholesale Price", "Stock"])
    for row in [
        ["ACM-100", "Steel Water Flask 1L", 11.40, 80],
        ["ACM-101", "Insulated Lunch Box", 9.95, 55],
        [None, "Foldable Camp Stool", 14.00, 20],
        ["ACM-103", "Travel Mug 400ml", 6.50, 140],
        ["ACM-104", None, 3.20, 10],
        ["ACM-105", "Bamboo Fibre Plate Set", 8.75, 95],
    ]:
        ws.append(row)
    wb.save(FIXTURES / "vendor_file_sample.xlsx")


if __name__ == "__main__":
    n = write_logs()
    write_vendor_xlsx()
    print(f"wrote {n} log rows and vendor_file_sample.xlsx to {FIXTURES}")
