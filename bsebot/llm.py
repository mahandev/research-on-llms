"""LLM router: provider chain fallback + truncation continuation + call logging.

Wraps litellm for raw completion calls, and instructor for structured (Pydantic) output.
External calls are routed through module-level shims (`_completion`, `_instructor_create`)
so tests can monkeypatch them without hitting the network.
"""

from __future__ import annotations

import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from bsebot import db
from bsebot.config import AppConfig, ProviderConfig


T = TypeVar("T", bound=BaseModel)


# Continuation prompt per spec.
_CONTINUATION_PROMPT = (
    "Continue exactly where you left off. Do not repeat anything. No preamble."
)


# Per-provider quirks. Extendable as new providers are added.
PROVIDER_QUIRKS: dict[str, dict[str, Any]] = {
    "gemini": {"supports_system_message": True},
    "cerebras": {"supports_system_message": True, "max_context": 8192},
    "groq": {"supports_system_message": True},
    "github_models": {"supports_system_message": True, "rate_limit_per_week": 50},
}


# Rough USD cost per 1K tokens. Conservative defaults; refine later.
_COST_PER_1K = {
    "gemini": (0.00015, 0.0006),     # (input, output)
    "cerebras": (0.0, 0.0),          # free tier
    "groq": (0.0, 0.0),              # free tier
    "github_models": (0.0, 0.0),     # free tier (rate-limited)
}


class LLMError(RuntimeError):
    """Base class for router errors."""


class RateLimitError(LLMError):
    """Raised by underlying provider when rate-limited (HTTP 429 etc)."""


class APIError(LLMError):
    """Raised on transient API failure (5xx, network)."""


class AllProvidersFailedError(LLMError):
    """All providers in the chain raised an error."""


# ---------- shims so tests can patch them ----------

def _completion(**kwargs: Any) -> Any:
    """Thin wrapper over litellm.completion. Patched in tests."""
    import litellm  # local import keeps module importable without network

    try:
        return litellm.completion(**kwargs)
    except Exception as e:  # normalize provider errors
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "quota" in msg:
            raise RateLimitError(str(e)) from e
        raise APIError(str(e)) from e


def _instructor_create(**kwargs: Any) -> tuple[BaseModel, Any]:
    """Wrapper over instructor.from_litellm + completion. Patched in tests.

    Must return (parsed_model_instance, raw_response). The raw_response is
    used only for usage/finish_reason logging."""
    import instructor
    import litellm

    client = instructor.from_litellm(litellm.completion)
    response_model = kwargs.pop("response_model")
    raw = {}

    def _capture(**ck):
        out = _completion(**ck)
        raw["resp"] = out
        return out

    # instructor uses the same kwargs as litellm
    try:
        parsed = client.create(response_model=response_model, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "quota" in msg:
            raise RateLimitError(str(e)) from e
        raise APIError(str(e)) from e
    return parsed, raw.get("resp")


# ---------- core router ----------


class LLMRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ----- public API -----

    def reason(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Run a reasoning call. Returns (text, meta)."""
        chain = self.config.llm.providers_for_role("reason")
        if not chain:
            raise LLMError("no providers configured for role=reason")
        return self._run_chain(
            chain=chain,
            role="reason",
            messages=messages,
            tools=tools,
            max_tokens=self.config.llm.reason_max_tokens,
        )

    def extract(
        self,
        prompt: str,
        schema: Type[T],
    ) -> tuple[T, dict[str, Any]]:
        """Run a structured extraction. Returns (validated_model, meta)."""
        chain = self.config.llm.providers_for_role("extract")
        if not chain:
            raise LLMError("no providers configured for role=extract")
        last_err: Exception | None = None
        for provider in chain:
            t0 = time.time()
            try:
                parsed, raw = _instructor_create(
                    model=provider.model,
                    api_key=provider.api_key,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.config.llm.extract_max_tokens,
                    response_model=schema,
                )
            except (RateLimitError, APIError) as e:
                self._log(
                    provider=provider, role="extract",
                    in_t=0, out_t=0, finish=None,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=0, error=str(e),
                )
                last_err = e
                continue
            in_t, out_t, finish = _usage(raw)
            self._log(
                provider=provider, role="extract",
                in_t=in_t, out_t=out_t, finish=finish,
                duration_ms=int((time.time() - t0) * 1000),
                continuation_count=0, error=None,
            )
            meta = {
                "provider": provider.name,
                "model": provider.model,
                "continuation_count": 0,
                "finish_reason": finish,
            }
            return parsed, meta
        raise AllProvidersFailedError(f"extract failed; last={last_err}")

    # ----- internals -----

    def _run_chain(
        self,
        chain: list[ProviderConfig],
        role: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        last_err: Exception | None = None
        for provider in chain:
            try:
                return self._call_with_continuation(
                    provider=provider, role=role,
                    messages=messages, tools=tools, max_tokens=max_tokens,
                )
            except (RateLimitError, APIError) as e:
                last_err = e
                continue
            except _TruncationGaveUp as e:
                last_err = e
                continue
        raise AllProvidersFailedError(f"{role} failed; last={last_err}")

    def _call_with_continuation(
        self,
        provider: ProviderConfig,
        role: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        running_messages = list(messages)
        accumulated = ""
        continuation_count = 0
        in_t_total = 0
        out_t_total = 0
        t0 = time.time()
        last_finish: str | None = None

        max_attempts = self.config.llm.continuation_max_attempts

        for attempt in range(max_attempts + 1):
            kwargs: dict[str, Any] = {
                "model": provider.model,
                "api_key": provider.api_key,
                "messages": running_messages,
                "max_tokens": max_tokens,
            }
            if tools:
                kwargs["tools"] = tools

            try:
                resp = _completion(**kwargs)
            except (RateLimitError, APIError) as e:
                self._log(
                    provider=provider, role=role,
                    in_t=in_t_total, out_t=out_t_total, finish=last_finish,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=continuation_count, error=str(e),
                )
                raise

            in_t, out_t, finish = _usage(resp)
            in_t_total += in_t
            out_t_total += out_t
            last_finish = finish
            content = _content(resp) or ""
            accumulated += content

            if finish == "length" and attempt < max_attempts:
                continuation_count += 1
                running_messages = list(messages) + [
                    {"role": "assistant", "content": accumulated},
                    {"role": "user", "content": _CONTINUATION_PROMPT},
                ]
                continue

            if finish == "length":
                # exhausted continuations; log and bail to next provider
                self._log(
                    provider=provider, role=role,
                    in_t=in_t_total, out_t=out_t_total, finish=finish,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=continuation_count,
                    error="continuation exhausted",
                )
                raise _TruncationGaveUp(provider.name)

            self._log(
                provider=provider, role=role,
                in_t=in_t_total, out_t=out_t_total, finish=finish,
                duration_ms=int((time.time() - t0) * 1000),
                continuation_count=continuation_count, error=None,
            )
            return accumulated, {
                "provider": provider.name,
                "model": provider.model,
                "continuation_count": continuation_count,
                "finish_reason": finish,
            }

        # safety net (should be unreachable)
        raise _TruncationGaveUp(provider.name)

    def _log(
        self,
        *,
        provider: ProviderConfig,
        role: str,
        in_t: int,
        out_t: int,
        finish: str | None,
        duration_ms: int,
        continuation_count: int,
        error: str | None,
    ) -> None:
        cost_in, cost_out = _COST_PER_1K.get(provider.name, (0.0, 0.0))
        cost = (in_t / 1000.0) * cost_in + (out_t / 1000.0) * cost_out
        conn = db.connect(self.config.database_path)
        try:
            conn.execute(
                "INSERT INTO llm_call_log "
                "(provider, model, role, input_tokens, output_tokens, "
                " finish_reason, duration_ms, continuation_count, "
                " cost_estimate_usd, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider.name, provider.model, role,
                    int(in_t), int(out_t), finish, int(duration_ms),
                    int(continuation_count), float(cost), error,
                ),
            )
            conn.commit()
        finally:
            conn.close()


class _TruncationGaveUp(LLMError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(f"truncation continuation exhausted for {provider_name}")


def _usage(resp: Any) -> tuple[int, int, str | None]:
    """Pull (input_tokens, output_tokens, finish_reason) out of a litellm response."""
    in_t = 0
    out_t = 0
    finish: str | None = None
    usage = getattr(resp, "usage", None)
    if usage is not None:
        in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_t = int(getattr(usage, "completion_tokens", 0) or 0)
    choices = getattr(resp, "choices", None) or []
    if choices:
        finish = getattr(choices[0], "finish_reason", None)
    return in_t, out_t, finish


def _content(resp: Any) -> str | None:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return None
    msg = getattr(choices[0], "message", None)
    if msg is None:
        return None
    return getattr(msg, "content", None)
