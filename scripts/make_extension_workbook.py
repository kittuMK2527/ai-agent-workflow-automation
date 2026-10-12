"""Build examples/extension_wf011/workflows_extension.xlsx.

The extension workbook uses exactly the same format as the assignment workbook
(sheets `Workflows` and `Test_Questions`, same column headers) and adds ONE new
workflow, WF011. It is a demonstration only: it is not part of the assignment's
ten workflows and is never loaded unless EXTRA_CATALOG_DIRS points at it.

    python scripts/make_extension_workbook.py
"""

from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent.parent / "examples" / "extension_wf011" / "workflows_extension.xlsx"

HEADERS = ["Workflow_ID", "Workflow_Name", "Trigger", "Inputs", "Steps", "Decision_Logic", "Tools_Required", "Expected_Output"]
ROW = [
    "WF011",
    "Inventory Valuation Summary",
    "User asks for the value of the current inventory",
    "Product inventory CSV",
    "Load inventory → calculate stock value per product → total value by category → flag high-value categories → summarize valuation",
    "Flag a category when it holds more than 40% of the total inventory value",
    "CSV reader; calculator",
    "Inventory value by category with its share of the total, and the categories flagged as concentrated",
]

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Workflows"
ws.append(HEADERS)
ws.append(ROW)
tq = wb.create_sheet("Test_Questions")
tq.append(["Workflow_ID", "Test_Request", "What_To_Check"])
tq.append(["WF011", "What is the total value of our inventory by category?", "Tests the 11th-workflow extension path"])
OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print("wrote", OUT)
