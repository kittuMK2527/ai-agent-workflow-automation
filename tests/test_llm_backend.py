"""OpenAI backend: request shape, structured-output parsing, error mapping, secret handling.

A fake client stands in for the network; real ``openai`` exception classes are used so the
error mapping is tested against the SDK's own types. The live provider path itself is covered
only by tests marked ``live`` (skipped without OPENAI_API_KEY).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import openai
import pytest

from src.errors import (
    ErrorCategory, LLMAuthError, LLMError, LLMRateLimitError, LLMResponseError, LLMTimeoutError, LLMUnavailableError,
)
from src.agent import Agent
from src.errors import LLMPausedError
from src.llm import PAUSE_MAX, PAUSE_MIN, OpenAIBackend, build_backend, nullable, obj_schema

REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def resp(status: int):
    return httpx.Response(status, request=REQ)


def completion(name="fn", args=None, raw_args=None):
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=raw_args if raw_args is not None else json.dumps(args or {})))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])


class FakeClient:
    def __init__(self, result=None, *, models=None):
        self.calls: list[dict] = []
        self._result = result

        def create(**kw):
            self.calls.append(kw)
            if isinstance(self._result, BaseException):
                raise self._result
            return self._result

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        self.models = SimpleNamespace(retrieve=models or (lambda m: SimpleNamespace(id=m)))


@pytest.fixture
def backend_for(settings):
    s = settings.with_overrides(openai_api_key="sk-test-not-a-real-key-123456", openai_model="my-model")
    return lambda result, **kw: OpenAIBackend(s, client=FakeClient(result, **kw))


SCHEMA = obj_schema({"a": {"type": "string"}, "b": nullable({"type": "integer"})})


def call(b):
    return b.call_function(system="SYS", user="USR", function_name="fn", description="desc", parameters=SCHEMA)


# ------------------------------------------------------------------ request
def test_request_uses_forced_strict_function_calling(backend_for):
    b = backend_for(completion(args={"a": "x", "b": None}))
    assert call(b) == {"a": "x", "b": None}
    kw = b.client.calls[0]
    assert kw["model"] == "my-model" and kw["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}]
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "fn"}}
    fn = kw["tools"][0]["function"]
    assert fn["name"] == "fn" and fn["strict"] is True and fn["parameters"] == SCHEMA and kw["tools"][0]["type"] == "function"
    assert "temperature" not in kw and "max_tokens" not in kw  # no params that some models reject


def test_schema_helpers_build_strict_objects():
    s = obj_schema({"x": {"type": "string"}, "y": {"type": "number"}})
    assert s == {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "number"}}, "required": ["x", "y"], "additionalProperties": False}
    assert nullable({"type": "string"})["type"] == ["string", "null"] and nullable({"type": ["string"]})["type"] == ["string", "null"]


# ----------------------------------------------------------------- responses
@pytest.mark.parametrize("bad", [
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None))]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))]),
    SimpleNamespace(choices=[]),
    completion(name="other_function", args={"a": 1}),
    completion(raw_args="{not json"),
    completion(raw_args="[1, 2]"),
    SimpleNamespace(),
])
def test_malformed_provider_answers_raise_a_response_error(backend_for, bad):
    with pytest.raises(LLMResponseError) as e:
        call(backend_for(bad))
    assert e.value.category == ErrorCategory.TERMINAL and "structured answer" in e.value.user_message


# -------------------------------------------------------------- error mapping
@pytest.mark.parametrize("exc,cls,fragment", [
    (openai.APITimeoutError(request=REQ), LLMTimeoutError, "took too long"),
    (openai.RateLimitError("rate limit exceeded", response=resp(429), body=None), LLMRateLimitError, "rate limit"),
    (openai.AuthenticationError("bad key", response=resp(401), body=None), LLMAuthError, "credentials were rejected"),
    (openai.PermissionDeniedError("nope", response=resp(403), body=None), LLMAuthError, "credentials were rejected"),
    (openai.NotFoundError("no model", response=resp(404), body=None), LLMAuthError, "my-model"),
    (openai.BadRequestError("bad", response=resp(400), body=None), LLMResponseError, "function calling"),
    (openai.APIConnectionError(request=REQ), LLMUnavailableError, "Could not reach"),
    (openai.InternalServerError("boom", response=resp(500), body=None), LLMError, "returned an error"),
    (RuntimeError("something odd"), LLMError, "failed unexpectedly"),
])
def test_provider_errors_map_to_friendly_typed_errors(backend_for, exc, cls, fragment):
    with pytest.raises(cls) as e:
        call(backend_for(exc))
    assert fragment in e.value.user_message
    assert type(e.value) is cls


def test_error_categories_distinguish_retryable_from_terminal(backend_for):
    with pytest.raises(LLMError) as rate:
        call(backend_for(openai.RateLimitError("x", response=resp(429), body=None)))
    with pytest.raises(LLMError) as auth:
        call(backend_for(openai.AuthenticationError("x", response=resp(401), body=None)))
    assert rate.value.category == ErrorCategory.RECOVERABLE and auth.value.category == ErrorCategory.TERMINAL


def test_secrets_in_provider_errors_never_reach_user_messages_or_logs(backend_for, caplog):
    secret = "sk-test-not-a-real-key-123456"
    exc = openai.AuthenticationError(f"Incorrect API key provided: {secret}. Authorization: Bearer {secret}", response=resp(401), body=None)
    with caplog.at_level("DEBUG"):
        with pytest.raises(LLMAuthError) as e:
            call(backend_for(exc))
    assert secret not in e.value.user_message
    assert secret not in (e.value.detail or "") and secret not in caplog.text


# ------------------------------------------------------------------ connection
def test_check_connection_proves_a_real_structured_answer(backend_for):
    b = backend_for(completion(name="connection_test", args={"ok": True}))
    ok, msg = b.check_connection()
    assert ok and "my-model" in msg and "test function call" in msg
    kw = b.client.calls[0]  # the same forced, strict request shape as real calls; nothing else is spent
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "connection_test"}} and kw["tools"][0]["function"]["strict"] is True


def test_check_connection_reports_why_it_failed(backend_for):
    """A model that is listed but cannot answer (quota, retired, no function calling) must not look healthy."""
    cases = [
        (openai.AuthenticationError("bad", response=resp(401), body=None), "credentials were rejected"),
        (openai.RateLimitError("quota", response=resp(429), body=None), "rate limit"),
        (openai.NotFoundError("gone", response=resp(404), body=None), "not available for this API key"),
        (openai.BadRequestError("no tools", response=resp(400), body=None), "function calling"),
        (openai.InternalServerError("busy", response=resp(503), body=None), "returned an error"),
    ]
    for exc, expected in cases:
        ok, msg = backend_for(exc).check_connection()
        assert not ok and expected in msg, (type(exc).__name__, msg)
    ok, msg = backend_for(completion(name="something_else")).check_connection()  # an answer that is not the function call
    assert not ok and "structured answer" in msg


# ------------------------------------------------------------------ pause after a limit / outage
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def rate_limit(delay=None, headers=None):
    """A 429 shaped like Google's: the wait is in the body (RetryInfo) or in a Retry-After header."""
    msg = "Error code: 429 - You exceeded your current quota"
    if delay:
        msg = f"Error code: 429 - [{{'error': {{'details': [{{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '{delay}'}}]}}}}]"
    return openai.RateLimitError(msg, response=httpx.Response(429, request=REQ, headers=headers or {}), body=None)


@pytest.fixture
def make_backend(settings):
    s = settings.with_overrides(openai_api_key="sk-test-not-a-real-key-123456", openai_model="my-model")

    def build(result):
        clock = Clock()
        return OpenAIBackend(s, client=FakeClient(result), clock=clock), clock

    return build


def test_a_rate_limit_pauses_requests_for_the_time_the_provider_asked(make_backend):
    b, clock = make_backend(rate_limit("42s"))
    with pytest.raises(LLMRateLimitError):
        call(b)
    assert b.paused_for == 42 and "rate limit" in b.pause_reason
    sent = len(b.client.calls)
    with pytest.raises(LLMPausedError) as e:
        call(b)  # no network round trip, no waiting through another slow failure
    assert len(b.client.calls) == sent and "paused for about 42 s" in e.value.user_message and e.value.code == "llm_paused"
    clock.now += 43  # the pause ends by itself
    b.client._result = completion(args={"a": "x", "b": None})
    assert call(b) == {"a": "x", "b": None} and b.paused_for == 0 and b.pause_reason == ""


@pytest.mark.parametrize("exc,seconds", [
    (rate_limit(), 60),                                  # provider did not say: a sensible default
    (rate_limit("1s"), PAUSE_MIN),                       # never hammer
    (rate_limit("24393s"), PAUSE_MAX),                   # a daily quota is re-checked, not waited out for hours
    (rate_limit(headers={"retry-after": "90"}), 90),     # standard header
    (openai.APITimeoutError(request=REQ), 30),
    (openai.APIConnectionError(request=REQ), 30),
    (openai.InternalServerError("busy", response=resp(503), body=None), 30),
])
def test_pause_length_for_each_kind_of_provider_problem(make_backend, exc, seconds):
    b, _ = make_backend(exc)
    with pytest.raises(LLMError):
        call(b)
    assert b.paused_for == seconds


@pytest.mark.parametrize("exc", [
    openai.AuthenticationError("bad", response=resp(401), body=None),
    openai.NotFoundError("gone", response=resp(404), body=None),
    openai.BadRequestError("no tools", response=resp(400), body=None),
])
def test_problems_that_waiting_cannot_fix_do_not_pause(make_backend, exc):
    b, _ = make_backend(exc)
    with pytest.raises(LLMError):
        call(b)
    assert b.paused_for == 0
    with pytest.raises(LLMError) as e:
        call(b)
    assert not isinstance(e.value, LLMPausedError) and len(b.client.calls) == 2  # still tried (and reported) every time


def test_check_connection_ignores_a_pause_and_ends_it_when_the_model_answers(make_backend):
    b, _ = make_backend(rate_limit("300s"))
    with pytest.raises(LLMError):
        call(b)
    assert b.paused_for == 300
    b.client._result = completion(name="connection_test", args={"ok": True})
    ok, _msg = b.check_connection()  # pressing "Check LLM connection" means "try again now"
    assert ok and b.paused_for == 0


def _agent(settings, tools, registry, mode, result):
    s = settings.with_overrides(llm_mode=mode, openai_api_key="sk-test-not-a-real-key-123456", openai_model="m")
    backend = OpenAIBackend(s, client=FakeClient(result))
    return Agent(s, tools=tools, registry=registry, llm=backend, audit=False, sleep=lambda _s: None), backend


BOTTLE = ("Generate SEO content for the Trail Water Bottle. Category: Outdoor; material: stainless steel; color: green; "
          "target audience: hikers; attributes: leak-proof, vacuum insulated")


def test_auto_mode_goes_offline_after_one_failed_call_and_stays_there_while_paused(settings, tools, registry):
    """The point of Auto: when the limit is reached the user still gets an answer, fast, and is told how."""
    agent, backend = _agent(settings, tools, registry, "auto", rate_limit("40s"))
    r = agent.handle(BOTTLE)
    assert r.state == "executed" and r.routing.mode == "deterministic" and "rate limit" in r.routing.fallback_reason
    assert len(backend.client.calls) == 1  # one failed call in total, not one per routing/extraction/content step
    content = [st for st in r.record.steps if st.tool == "content_generator"]
    assert content and all("mode: template" in (st.output_summary or "") for st in content)
    info = agent.mode_info()
    assert info["effective_mode"] == "paused" and 0 < info["paused_seconds"] <= 40 and "deterministic fallback" in info["reason"]
    again = agent.handle("Where is order ORD-1001?")
    assert again.state == "executed" and "ORD-1001" in again.record.final_result.headline
    assert len(backend.client.calls) == 1 and "paused" in again.routing.fallback_reason  # nothing was sent while paused


def test_llm_only_mode_still_reports_the_limit_and_does_not_wait_again(settings, tools, registry):
    agent, backend = _agent(settings, tools, registry, "llm", rate_limit("40s"))
    first = agent.handle("Where is order ORD-1001?")
    assert first.state == "error" and first.error.code == "llm_rate_limited"
    second = agent.handle("Where is order ORD-1001?")
    assert second.state == "error" and second.error.code == "llm_paused" and len(backend.client.calls) == 1
    assert agent.mode_info()["effective_mode"] == "paused" and "switch to Auto" in agent.mode_info()["reason"]


# ------------------------------------------------------------------ construction
def test_backend_requires_a_key(settings):
    with pytest.raises(LLMUnavailableError) as e:
        OpenAIBackend(settings.with_overrides(openai_api_key=""))
    assert "OPENAI_API_KEY" in e.value.user_message and e.value.code == "llm_no_key"


def test_build_backend_respects_the_mode_and_key(settings):
    assert build_backend(settings.with_overrides(llm_mode="deterministic", openai_api_key="sk-x-123456789")) is None
    assert build_backend(settings.with_overrides(llm_mode="auto", openai_api_key="")) is None
    b = build_backend(settings.with_overrides(llm_mode="auto", openai_api_key="sk-test-123456789", openai_model="m1"))
    assert isinstance(b, OpenAIBackend) and b.model == "m1"  # constructing the SDK client makes no network call


def test_settings_never_expose_the_key(settings):
    s = settings.with_overrides(openai_api_key="sk-secret-value-123456")
    assert "sk-secret" not in repr(s) and "sk-secret" not in json.dumps(s.public_summary())
    assert s.public_summary()["api_key_configured"] is True


def test_settings_from_env(monkeypatch):
    from src.config import Settings
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-abc  ")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    monkeypatch.setenv("LLM_MODE", "bogus")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "abc")
    s = Settings.from_env(load_dotenv_file=False)
    assert s.openai_api_key == "sk-abc" and s.openai_model == "gpt-x" and s.llm_mode == "auto" and s.openai_timeout_seconds == 30.0
    monkeypatch.setenv("LLM_MODE", "DETERMINISTIC")
    monkeypatch.delenv("OPENAI_MODEL")
    s2 = Settings.from_env(load_dotenv_file=False)
    assert s2.llm_mode == "deterministic" and s2.openai_model == "gpt-4o-mini" and not s2.llm_enabled


# ---------------------------------------------------------- live (needs a key)
@pytest.mark.live
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set: the live provider path was not exercised")
def test_live_routing_of_the_ten_excel_questions():
    from src.config import PROJECT_ROOT, Settings
    from src.excel_loader import load_workbook_catalog
    agent = Agent(Settings.from_env(load_dotenv_file=False).with_overrides(llm_mode="llm"), audit=False)
    wrong = []
    for q in load_workbook_catalog(PROJECT_ROOT / "data" / "workflows.xlsx").test_questions:
        d = agent.llm_router.route(q.request)
        if d.workflow_id != q.workflow_id:
            wrong.append((q.workflow_id, d.workflow_id, q.request))
    assert not wrong, wrong
