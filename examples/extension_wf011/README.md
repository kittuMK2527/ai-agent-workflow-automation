# Extension demo: WF011 Inventory Valuation Summary

This folder demonstrates how an **11th workflow** is added without touching the agent, router,
engine or tools. It is *not* one of the assignment's ten workflows and is not loaded by default.

| File | Purpose |
|---|---|
| `workflows_extension.xlsx` | One new row (WF011) in the same format as the assignment workbook |
| `specs/WF011.yaml` | The executable interpretation: inputs, steps, rules, result |

Load it by pointing `EXTRA_CATALOG_DIRS` at this folder (or tick "Load demo extension" in the app's sidebar):

```bash
EXTRA_CATALOG_DIRS=examples/extension_wf011 streamlit run app.py
```

See "How to add an 11th workflow" in the main README for the full procedure.
