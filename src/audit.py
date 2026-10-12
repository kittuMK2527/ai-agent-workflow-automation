"""Append-only audit log of workflow executions (JSON lines, redacted)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .logging_utils import mask_email, redact
from .models import ExecutionRecord

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: ExecutionRecord) -> None:
        """Write a one-line summary. Never raises: auditing must not break a run."""
        failed = next((s for s in record.steps if s.status.value == "failed"), None)
        entry = {
            "run_id": record.run_id,
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
            "workflow_id": record.workflow_id,
            "status": record.status.value,
            "duration_ms": record.duration_ms,
            "request": mask_email(redact(record.request))[:300],
            "steps_completed": len(record.completed_step_ids),
            "steps_total": len(record.steps),
            "failed_step": failed.step_id if failed else None,
            "error_code": failed.error.code if failed and failed.error else None,
            "simulated_tools": sorted({t.tool for t in record.tools_used if t.simulated}),
            "llm_used": record.llm_used_in_tools,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("could not write audit log: %s", exc)
