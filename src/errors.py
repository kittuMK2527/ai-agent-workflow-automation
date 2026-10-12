"""Typed errors shared across the system.

Every error carries a *category* so callers (engine, agent, UI) can distinguish
problems the user can fix from transient tool failures and terminal failures:

* ``USER_CORRECTABLE`` - the user must supply/fix something (missing input,
  unknown order, bad file).
* ``RECOVERABLE``      - a transient tool problem; bounded retries are allowed.
* ``TERMINAL``         - a definition bug, unexpected tool output or any other
  failure that retrying or asking the user cannot fix.

``user_message`` is always safe to show in the UI (no secrets, no stack traces).
``detail`` is for logs only and is passed through redaction before logging.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    USER_CORRECTABLE = "user_correctable"
    RECOVERABLE = "recoverable"
    TERMINAL = "terminal"


class WorkflowAgentError(Exception):
    """Base class for every error raised intentionally by this project."""

    code = "workflow_agent_error"
    category = ErrorCategory.TERMINAL

    def __init__(self, user_message: str, *, detail: str | None = None, code: str | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        if code:
            self.code = code


# --------------------------------------------------------------------- catalog
class CatalogError(WorkflowAgentError):
    """The Excel workbook or an execution spec is invalid / unsupported."""

    code = "catalog_error"


# ----------------------------------------------------------------- expressions
class ExpressionError(WorkflowAgentError):
    """A condition / expression is malformed or cannot be evaluated."""

    code = "expression_error"


class ExpressionSyntaxError(ExpressionError):
    code = "expression_syntax_error"


class MissingValueError(ExpressionError):
    """A referenced path (input, step output, row column) does not exist."""

    code = "missing_value"


# ----------------------------------------------------------------------- tools
class ToolError(WorkflowAgentError):
    code = "tool_error"


class UnknownToolError(ToolError):
    code = "unknown_tool"


class ToolInputError(ToolError):
    """The tool received input that is invalid *because of the user's data*
    (bad file, unknown column, malformed value). The user can fix it."""

    code = "invalid_tool_input"
    category = ErrorCategory.USER_CORRECTABLE


class ToolParameterError(ToolError):
    """Parameters do not satisfy the tool's input schema (definition/mapping bug)."""

    code = "invalid_tool_parameters"
    category = ErrorCategory.TERMINAL


class TransientToolError(ToolError):
    """Timeouts, rate limits and outages: safe to retry (bounded)."""

    code = "transient_tool_error"
    category = ErrorCategory.RECOVERABLE


class ToolExecutionError(ToolError):
    code = "tool_execution_error"


class UnexpectedToolOutput(ToolError):
    """The tool returned something that violates its declared output schema."""

    code = "unexpected_tool_output"


# ------------------------------------------------------------------------- LLM
class LLMError(WorkflowAgentError):
    code = "llm_error"
    category = ErrorCategory.RECOVERABLE


class LLMUnavailableError(LLMError):
    """No credentials / SDK problem: the LLM cannot be used at all."""

    code = "llm_unavailable"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"


class LLMPausedError(LLMError):
    """The provider recently reported a limit or an outage, so nothing is sent until the pause ends."""

    code = "llm_paused"


class LLMAuthError(LLMError):
    code = "llm_auth_failed"
    category = ErrorCategory.TERMINAL


class LLMResponseError(LLMError):
    """The model answered, but not with the structured output we required."""

    code = "llm_invalid_response"
    category = ErrorCategory.TERMINAL
