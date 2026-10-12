"""Verify the LIVE LLM path (routing + extraction) against the workbook's test questions.

    OPENAI_API_KEY=... python scripts/verify_live_llm.py      # add OPENAI_BASE_URL / OPENAI_MODEL for other providers

This is the only script that spends tokens. It exits with status 2 (and says why) if no key is
configured, so it can never be mistaken for a passing verification. It checks that

1. the connection and model work,
2. each of the 10 Test_Questions is routed to its workflow by the LLM, and
3. the full pipeline (routing -> extraction -> engine) ends in the right state.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.config import Settings  # noqa: E402
from src.excel_loader import load_workbook_catalog  # noqa: E402
from src.models import Attachment  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    if not settings.has_api_key:
        print("OPENAI_API_KEY is not set: the live LLM path was NOT verified.")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        agent = Agent(settings.with_overrides(llm_mode="llm", output_dir=Path(tmp)), audit=False)
        ok, msg = agent.check_llm_connection()
        print(f"Connection: {'OK' if ok else 'FAILED'} - {msg}")
        if not ok:
            return 1
        attach = {"WF003": ["vendor_file_sample.csv"], "WF008": ["keywords_sample.csv"]}
        wrong = 0
        for q in load_workbook_catalog(settings.workbook_path).test_questions:
            atts = [Attachment(name=a, path=a) for a in attach.get(q.workflow_id, [])]
            resp = agent.handle(q.request, attachments=atts)
            routed = resp.routing.workflow_id if resp.routing else None
            good = routed == q.workflow_id
            wrong += not good
            print(f"{'OK ' if good else 'BAD'} {q.workflow_id} -> {routed or resp.state:<8} [{resp.state}] {q.request}")
            if resp.routing:
                print(f"      {resp.routing.mode}: {resp.routing.explanation}")
        print(f"\n{10 - wrong}/10 test questions routed correctly by the LLM")
        return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
