"""LLM access: one small interface, one OpenAI implementation.

The rest of the system only depends on :class:`LLMBackend.call_function`: send
a system + user message and force the model to answer by calling one function
whose JSON schema we define (OpenAI function/tool calling). The returned
arguments are plain data; callers validate them before use. The LLM never
executes anything - the deterministic engine does.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Protocol

from .config import Settings
from .errors import (
    LLMAuthError,
    LLMError,
    LLMPausedError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from .logging_utils import redact

log = logging.getLogger(__name__)

#: After the provider reports its limit / a busy or unreachable service, it is left alone for a while so that
#: (a) the rest of the request does not wait through the same slow failure again and (b) a rate-limited key is not
#: hammered. In ``auto`` mode the callers answer with the labelled deterministic fallback meanwhile.
PAUSE_RATE_LIMIT_DEFAULT = 60.0  # seconds, when the provider does not say how long
PAUSE_OUTAGE = 30.0
PAUSE_MIN, PAUSE_MAX = 15.0, 600.0

_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def format_wait(seconds: float) -> str:
    seconds = max(1, round(seconds))
    return f"{seconds} s" if seconds < 90 else f"{round(seconds / 60)} min"


def _retry_delay(exc: Exception) -> float | None:
    """Seconds the provider asked us to wait: ``Retry-After`` header or Google's ``RetryInfo.retryDelay``."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = headers.get("retry-after") if headers is not None else None
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass
    m = _RETRY_DELAY.search(str(exc))
    return float(m.group(1)) if m else None


class LLMBackend(Protocol):
    model: str

    def call_function(self, *, system: str, user: str, function_name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Force the model to call ``function_name`` and return its arguments."""
        ...

    def check_connection(self) -> tuple[bool, str]:
        ...


# ------------------------------------------------------------- schema helpers
def obj_schema(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    """Strict-mode object schema (every property required, no extras)."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    t = schema["type"]
    out = dict(schema)
    out["type"] = [*t, "null"] if isinstance(t, list) else [t, "null"]
    return out


class OpenAIBackend:
    """OpenAI Chat Completions with forced function calling."""

    def __init__(self, settings: Settings, client: Any | None = None, *, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.model = settings.openai_model
        self._clock = clock
        self._paused_until = 0.0
        self._pause_reason = ""
        if client is None:
            if not settings.has_api_key:
                raise LLMUnavailableError("No API key is configured (set OPENAI_API_KEY).", code="llm_no_key")
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMUnavailableError("The 'openai' package is not installed.", detail=str(exc)) from exc
            kwargs: dict[str, Any] = {
                "api_key": settings.openai_api_key,
                "timeout": settings.openai_timeout_seconds,
                "max_retries": settings.openai_max_retries,
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            client = OpenAI(**kwargs)
        self.client = client

    # ------------------------------------------------------------------ pause
    @property
    def paused_for(self) -> float:
        """Seconds left before requests are sent again (0 when the provider is in use)."""
        return max(0.0, self._paused_until - self._clock())

    @property
    def pause_reason(self) -> str:
        return self._pause_reason if self.paused_for else ""

    def _pause_after(self, exc: Exception) -> None:
        import openai

        if isinstance(exc, openai.RateLimitError):
            seconds, why = _retry_delay(exc) or PAUSE_RATE_LIMIT_DEFAULT, "its rate limit or quota was reached"
        elif isinstance(exc, openai.APITimeoutError):
            seconds, why = PAUSE_OUTAGE, "it did not answer in time"
        elif isinstance(exc, openai.APIConnectionError):
            seconds, why = PAUSE_OUTAGE, "it could not be reached"
        elif isinstance(exc, openai.InternalServerError):
            seconds, why = PAUSE_OUTAGE, "the service was overloaded"
        else:
            return
        seconds = min(max(seconds, PAUSE_MIN), PAUSE_MAX)
        self._paused_until, self._pause_reason = self._clock() + seconds, why
        log.warning("LLM paused for %s: %s", format_wait(seconds), why)

    # ------------------------------------------------------------------ calls
    def call_function(self, *, system: str, user: str, function_name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        remaining = self.paused_for
        if remaining:
            raise LLMPausedError(
                f"The language model is paused for about {format_wait(remaining)} because {self._pause_reason}. Nothing is sent to it until then.",
                detail="circuit open",
            )
        return self._request(system=system, user=user, function_name=function_name, description=description, parameters=parameters)

    def _request(self, *, system: str, user: str, function_name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=[{"type": "function", "function": {"name": function_name, "description": description, "parameters": parameters, "strict": True}}],
                tool_choice={"type": "function", "function": {"name": function_name}},
            )
        except Exception as exc:  # mapped below; never leak provider details to the UI
            raise self._map_error(exc) from exc
        try:
            message = resp.choices[0].message
            calls = message.tool_calls or []
            call = next(c for c in calls if c.function.name == function_name)
            args = json.loads(call.function.arguments)
        except (StopIteration, AttributeError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError(
                "The language model did not return the structured answer that was required.", detail=f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(args, dict):
            raise LLMResponseError("The language model returned a malformed structured answer.")
        self._paused_until = 0.0  # it answered: whatever was wrong has passed
        return args

    def check_connection(self) -> tuple[bool, str]:
        """Send one tiny forced function call: the same path real requests use.

        Looking the model up is not enough (a model can be listed yet retired for new keys, out of quota,
        or lack function calling), so this proves that an actual answer comes back.
        """
        try:
            self._request(  # deliberately ignores a pause: pressing the button is how a person says "try again now"
                system="This is a connection test. Call the function with ok=true.",
                user="ping",
                function_name="connection_test",
                description="Confirms that the model can answer with a structured function call.",
                parameters=obj_schema({"ok": {"type": "boolean"}}),
            )
        except LLMError as exc:
            return False, exc.user_message
        return True, f"Connected - model '{self.model}' answered a test function call."

    # ----------------------------------------------------------------- errors
    def _map_error(self, exc: Exception) -> LLMError:
        try:
            import openai
        except ImportError:  # pragma: no cover
            return LLMError("The language model request failed.", detail=repr(exc))
        detail = redact(f"{type(exc).__name__}: {exc}")
        log.warning("LLM call failed: %s", detail)
        self._pause_after(exc)
        if isinstance(exc, openai.APITimeoutError):
            return LLMTimeoutError("The language model took too long to respond. Try again in a moment.", detail=detail)
        if isinstance(exc, openai.RateLimitError):
            return LLMRateLimitError("The language model rate limit was reached. Wait a moment and try again.", detail=detail)
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMAuthError("The language-model credentials were rejected. Check OPENAI_API_KEY (and OPENAI_BASE_URL) and the key's permissions.", detail=detail)
        if isinstance(exc, openai.NotFoundError):
            return LLMAuthError(f"The model '{self.model}' is not available for this API key. Check OPENAI_MODEL.", detail=detail)
        if isinstance(exc, openai.BadRequestError):
            return LLMResponseError("The language model rejected the request. The configured model may not support function calling.", detail=detail)
        if isinstance(exc, openai.APIConnectionError):
            return LLMUnavailableError("Could not reach the language model service. Check your network connection.", detail=detail)
        if isinstance(exc, openai.APIStatusError):
            return LLMError("The language model service returned an error. Try again shortly.", detail=detail)
        return LLMError("The language model request failed unexpectedly.", detail=detail)


def build_backend(settings: Settings) -> OpenAIBackend | None:
    """Backend for ``settings`` or ``None`` when the LLM must not / cannot be used."""
    if not settings.llm_enabled:
        return None
    try:
        return OpenAIBackend(settings)
    except LLMError as exc:
        log.warning("LLM backend unavailable: %s", exc.user_message)
        return None
