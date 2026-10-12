"""The agent: request -> route -> extract -> validate -> execute -> result.

The LLM (when available) is used for exactly two *proposals*: which workflow
fits, and which parameter values the request states. Everything after that is
deterministic and validated: the workflow ID must exist in the registry, input
values are typed/validated against the workflow definition, and only the
generic engine executes steps, using only registered tools.

Mode handling (``LLM_MODE``):

* ``auto``          use the LLM when a key is configured; if the provider fails
                    (rate limit, timeout, outage) fall back to the deterministic
                    router/extractor and SAY SO in the response.
* ``llm``           strict: provider problems become errors; no silent fallback.
* ``deterministic`` never call the LLM.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

from .audit import AuditLog
from .config import Settings
from .errors import ErrorCategory, LLMError, LLMUnavailableError, WorkflowAgentError
from .execution_engine import ExecutionEngine
from .extraction import DeterministicExtractor, ExtractionResult, LLMExtractor
from .inputs import resolve_inputs
from .llm import LLMBackend, build_backend, format_wait
from .logging_utils import log_exception, redact
from .models import (
    AgentResponse,
    Attachment,
    ErrorRecord,
    ExecutionRecord,
    ExecutionStatus,
    InputResolution,
    InputType,
    PendingState,
    RoutingDecision,
    SelectionInfo,
    WorkflowDefinition,
)
from .router import DeterministicRouter, LLMRouter
from .tool_registry import FaultInjector, ToolRegistry
from .tools import build_default_registry
from .workflow_registry import WorkflowRegistry

log = logging.getLogger(__name__)

MAX_REQUEST_CHARS = 4000
_UNSET: Any = object()


def _host(url: str) -> str:
    """Just the host of a base URL (never a path or credentials), for display."""
    from urllib.parse import urlparse

    return urlparse(url).hostname or "the configured endpoint"


class Agent:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: WorkflowRegistry | None = None,
        tools: ToolRegistry | None = None,
        llm: LLMBackend | None = _UNSET,
        faults: FaultInjector | None = None,
        sleep: Callable[[float], None] = time.sleep,
        audit: bool = True,
        today: date | None = None,
    ):
        self.settings = settings or Settings.from_env()
        self.tools = tools or (registry.tools if registry else build_default_registry())
        self.registry = registry or WorkflowRegistry(self.settings, self.tools)
        if self.registry.report is None:
            self.registry.load()
        self.faults = faults or FaultInjector()
        self.backend: LLMBackend | None = build_backend(self.settings) if llm is _UNSET else llm
        self.today = today
        self.engine = ExecutionEngine(self.tools, self.settings, llm=self.backend if self.llm_active else None,
                                      faults=self.faults, sleep=sleep)
        self.det_router = DeterministicRouter(self.registry)
        self.det_extractor = DeterministicExtractor()
        self.llm_router = LLMRouter(self.backend, self.registry) if self.backend else None
        self.llm_extractor = LLMExtractor(self.backend) if self.backend else None
        self.audit = AuditLog(self.settings.output_dir / "audit_log.jsonl") if audit else None

    # ------------------------------------------------------------------ mode
    @property
    def llm_active(self) -> bool:
        return self.backend is not None and self.settings.llm_mode != "deterministic"

    def mode_info(self) -> dict[str, Any]:
        s = self.settings
        if s.llm_mode == "deterministic":
            effective, reason = "deterministic", "LLM_MODE=deterministic: the language model is never called."
        elif self.backend is None:
            effective = "unavailable" if s.llm_mode == "llm" else "deterministic"
            reason = ("LLM_MODE=llm but OPENAI_API_KEY is not configured: requests will fail until it is set."
                      if s.llm_mode == "llm" else "No OPENAI_API_KEY configured: using the deterministic fallback (not an LLM).")
        else:
            effective, reason = "llm", f"Using model '{s.openai_model}'" + (f" via {_host(s.openai_base_url)}." if s.openai_base_url else ".")
        paused = float(getattr(self.backend, "paused_for", 0.0) or 0.0) if effective == "llm" else 0.0
        if paused:
            effective = "paused"
            why = getattr(self.backend, "pause_reason", "") or "the provider reported a problem"
            reason = (f"The language model is paused for about {format_wait(paused)} because {why}. "
                      + ("Answers use the deterministic fallback meanwhile (not an LLM); it resumes by itself." if s.llm_mode == "auto"
                         else "Requests fail until it resumes: switch to Auto to keep going with the fallback."))
        return {"configured_mode": s.llm_mode, "effective_mode": effective, "reason": reason, "model": s.openai_model,
                "key_configured": s.has_api_key or self.backend is not None, "paused_seconds": round(paused)}

    def check_llm_connection(self) -> tuple[bool, str]:
        if self.backend is None:
            return False, "No API key is configured (set OPENAI_API_KEY)."
        return self.backend.check_connection()

    def reload_catalog(self):
        report = self.registry.load()
        self.det_router.rebuild()
        return report

    # ------------------------------------------------------------- examples
    def examples(self) -> list[dict[str, Any]]:
        out = []
        for d in self.registry.all():
            for ex in d.examples:
                out.append({"workflow_id": d.id, "workflow_name": d.name, "title": ex.title, "request": ex.request,
                            "attachments": ex.attachments, "expect_state": ex.expect_state, "note": ex.note,
                            "from_excel": any(t.request == ex.request for t in d.test_requests), "extension": d.is_extension})
        return out

    # ===================================================================== handle
    def handle(
        self,
        request: str,
        *,
        attachments: list[Attachment] | None = None,
        pending: PendingState | None = None,
        workflow_id: str | None = None,
        listener: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResponse:
        """Handle one user message. ``workflow_id`` is the user's own choice after an
        "ambiguous" answer; it must still exist in the registry."""
        t0 = time.perf_counter()
        attachments = attachments or []
        text = (request or "").strip()
        if not text:
            return self._done(AgentResponse(state="error", request=request or "", message="Please type a request first."), t0)
        if len(text) > MAX_REQUEST_CHARS:
            return self._done(AgentResponse(state="error", request=text[:200] + "…",
                                            message=f"That request is too long ({len(text):,} characters). Please keep it under {MAX_REQUEST_CHARS:,}."), t0)
        try:
            if pending is not None:
                if not self.registry.has(pending.workflow_id):
                    return self._done(AgentResponse(state="error", request=text, message="The previous request refers to a workflow that is no longer available. Please start again."), t0)
                definition = self.registry.get(pending.workflow_id)
                routing = RoutingDecision(decision="selected", workflow_id=definition.id, mode="continuation", confidence=1.0,
                                          explanation="Continuing your previous request with the information you just provided.")
            elif workflow_id is not None:
                if not self.registry.has(workflow_id):
                    return self._done(AgentResponse(state="error", request=text, message=f"'{workflow_id}' is not a registered workflow."), t0)
                definition = self.registry.get(workflow_id)
                routing = RoutingDecision(decision="selected", workflow_id=workflow_id, mode="direct", confidence=1.0,
                                          explanation="You chose this workflow.")
            else:
                routing = self._route(text, attachments)
                if routing.decision == "no_match":
                    return self._done(AgentResponse(state="no_match", request=text, routing=routing, message=self._no_match_message(attachments)), t0)
                if routing.decision == "ambiguous":
                    return self._done(AgentResponse(state="ambiguous", request=text, routing=routing,
                                                    message=routing.clarification_question or "Which workflow do you mean?"), t0)
                if not self.registry.has(routing.workflow_id or ""):  # defence in depth: never run an unknown ID
                    return self._done(AgentResponse(state="error", request=text, routing=routing, message="The selected workflow is not registered."), t0)
                definition = self.registry.get(routing.workflow_id)
            return self._done(self._run(definition, text, routing, attachments, pending, listener), t0)
        except LLMError as exc:
            log.warning("LLM failure surfaced to the user: %s %s", exc.code, redact(exc.detail or ""))
            return self._done(AgentResponse(state="error", request=text, message=exc.user_message,
                                            error=ErrorRecord(code=exc.code, category=exc.category, message=exc.user_message, retryable=exc.category == ErrorCategory.RECOVERABLE)), t0)
        except WorkflowAgentError as exc:
            return self._done(AgentResponse(state="error", request=text, message=exc.user_message,
                                            error=ErrorRecord(code=exc.code, category=exc.category, message=exc.user_message)), t0)
        except Exception as exc:  # last resort: never show a stack trace
            log_exception(log, "unexpected agent error")
            log.error("detail: %s", redact(f"{type(exc).__name__}: {exc}"))
            return self._done(AgentResponse(state="error", request=text,
                                            message="Something unexpected went wrong. The details were written to the log.",
                                            error=ErrorRecord(code="agent_internal_error", category=ErrorCategory.TERMINAL,
                                                              message="Unexpected internal error.")), t0)

    def run_workflow(self, workflow_id: str, inputs: dict[str, Any] | None = None, *, attachments: list[Attachment] | None = None,
                     request: str = "", listener: Callable[[dict[str, Any]], None] | None = None) -> AgentResponse:
        """Execute a workflow directly (no routing): used by scripts, tests and the extension demo."""
        t0 = time.perf_counter()
        definition = self.registry.get(workflow_id)
        routing = RoutingDecision(decision="selected", workflow_id=workflow_id, mode="direct", confidence=1.0,
                                  explanation="Workflow chosen explicitly by the caller (routing skipped).")
        resolution = resolve_inputs(definition, inputs or {}, attachments=attachments, data=self.engine.data)
        return self._done(self._execute(definition, request or f"[direct run of {workflow_id}]", routing, resolution, listener), t0)

    # ------------------------------------------------------------------ steps
    def _route(self, text: str, attachments: list[Attachment]) -> RoutingDecision:
        s = self.settings
        if s.llm_mode == "llm" and self.backend is None:
            raise LLMUnavailableError("LLM mode is selected, but no API key is configured. Set OPENAI_API_KEY or switch LLM_MODE to auto.", code="llm_no_key")
        if self.llm_active:
            try:
                return self.llm_router.route(text, attachments)
            except LLMError as exc:
                if s.llm_mode == "llm":
                    raise
                decision = self.det_router.route(text, attachments)
                decision.fallback_reason = f"{exc.user_message} Used the deterministic fallback router instead (not an LLM)."
                return decision
        decision = self.det_router.route(text, attachments)
        decision.fallback_reason = self.mode_info()["reason"]
        return decision

    def _extract(self, definition: WorkflowDefinition, text: str, attachments: list[Attachment], known: dict[str, Any]) -> ExtractionResult:
        s = self.settings
        if self.llm_active:
            try:
                return self.llm_extractor.extract(definition, text, attachments, known, self.today)
            except LLMError as exc:
                if s.llm_mode == "llm":
                    raise
                res = self.det_extractor.extract(definition, text, attachments, known, self.today)
                res.note = f"{exc.user_message} Used the deterministic fallback extractor instead (not an LLM)."
                return res
        return self.det_extractor.extract(definition, text, attachments, known, self.today)

    def _run(self, definition, text, routing, attachments, pending, listener) -> AgentResponse:
        known = dict(pending.collected_inputs) if pending else {}
        extraction = self._extract(definition, text, attachments, known)
        values = {**known, **extraction.values}
        sources = {**(pending.collected_sources if pending else {}), **{k: ("reply" if pending else "request") for k in extraction.values}}
        # A one-word/one-line reply to "what is the X?" answers that single question.
        if pending and len(pending.missing) == 1 and pending.missing[0] not in extraction.values:
            spec = definition.input(pending.missing[0])
            if spec and spec.type in (InputType.STRING, InputType.LIST) and not attachments:
                values[spec.name], sources[spec.name] = text, "reply"
        request_text = f"{pending.original_request}\n{text}" if pending else text
        resolution = resolve_inputs(definition, values, sources=sources, attachments=attachments, data=self.engine.data, request_text=request_text)
        resolution.extraction_mode = extraction.mode
        resolution.extraction_note = extraction.note
        return self._execute(definition, request_text, routing, resolution, listener, pending=pending)

    def _execute(self, definition, request_text, routing, resolution: InputResolution, listener, pending: PendingState | None = None) -> AgentResponse:
        if not resolution.complete:
            return AgentResponse(
                state="needs_input", request=request_text, routing=routing, resolution=resolution,
                message=self._ask_message(definition, resolution), warnings=list(resolution.warnings),
                pending=self._pending(definition, resolution, request_text, [m.name for m in resolution.missing] + [i.name for i in resolution.invalid], "inputs"),
            )
        selection = SelectionInfo(explanation=routing.explanation, confidence=routing.confidence, mode=routing.mode, fallback_reason=routing.fallback_reason)
        record = self.engine.run(definition, resolution.values, request=request_text, input_sources=resolution.sources, selection=selection, listener=listener)
        if self.audit:
            self.audit.append(record)
        return self._from_record(definition, record, routing, resolution, request_text)

    def _from_record(self, definition, record: ExecutionRecord, routing, resolution, request_text) -> AgentResponse:
        base = dict(request=request_text, routing=routing, resolution=resolution, record=record, warnings=[*resolution.warnings, *(record.final_result.warnings if record.final_result else [])])
        if record.status == ExecutionStatus.AWAITING_INPUT:
            aw = record.awaiting
            return AgentResponse(state="needs_input", message=aw.message, pending=self._pending(definition, resolution, request_text, aw.missing, aw.message), **base)
        if record.status == ExecutionStatus.FAILED:
            failed = next((s for s in record.steps if s.error), None)
            msg = (f"'{definition.name}' stopped at step '{failed.name}': {failed.error.message}" if failed
                   else (record.errors[-1].message if record.errors else "The workflow failed."))
            return AgentResponse(state="error", message=msg, error=record.errors[-1] if record.errors else None, **base)
        return AgentResponse(state="executed", message=record.final_result.headline if record.final_result else "Done.", **base)

    # --------------------------------------------------------------- messages
    def _pending(self, definition, resolution, request_text, missing_names, reason) -> PendingState:
        drop = set(missing_names)
        keep = {k: v for k, v in resolution.values.items() if v is not None and k not in drop and resolution.sources.get(k) != "default"}
        return PendingState(workflow_id=definition.id, collected_inputs=keep,
                            collected_sources={k: resolution.sources[k] for k in keep if k in resolution.sources},
                            original_request=request_text, reason=reason if isinstance(reason, str) else "", missing=list(missing_names))

    def _ask_message(self, definition: WorkflowDefinition, res: InputResolution) -> str:
        lines = []
        for m in res.missing:
            lines.append(f"- {m.description}")
        for i in res.invalid:
            lines.append(f"- {i.name.replace('_', ' ')}: {i.reason}")
        return f"To run '{definition.name}' I still need:\n" + "\n".join(lines)

    def _no_match_message(self, attachments: list[Attachment] | None = None) -> str:
        names = ", ".join(f"{d.name}" for d in self.registry.all())
        got = (f" I received your attached file ({', '.join(a.name for a in attachments)}), but nothing in the request says what to do with it."
               if attachments else "")
        return (f"I could not match that request to any of my workflows, so I did not run anything.{got} "
                f"I can help with: {names}. Tell me which task you want (for example “process this vendor spreadsheet and show invalid rows”).")

    def _done(self, response: AgentResponse, t0: float) -> AgentResponse:
        response.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return response
