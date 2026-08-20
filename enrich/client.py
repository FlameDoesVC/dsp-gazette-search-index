"""The four-stage provider chain. Spec 5.1, 5.2 layers 1 and 2.

| Stage | Provider | Model                  | When                          |
|-------|----------|------------------------|-------------------------------|
| 1     | DeepSeek | deepseek-v4-flash      | default                       |
| 2     | DeepSeek | deepseek-v4-flash      | repair retry, error fed back  |
| 3     | DeepSeek | deepseek-v4-pro        | records that failed stage 2   |
| 4     | Ollama   | qwen3.5:4b             | offline, dev, or unavailable  |

This mirrors the escalation ladder core/translate.py already implements, so
the enrichment client follows an idiom the codebase has rather than inventing
a second one.

DeepSeek's JSON mode guarantees parseable JSON, not schema conformance, and
its docs acknowledge occasional empty-content responses. Both are handled here
as failed attempts; schema conformance is task 7's problem.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Every stage of the chain failed."""


def _extract_usage(provider: str, payload: dict) -> dict:
    """Token counts as the provider reported them.

    These were being discarded entirely, which is why the only cost figures
    anyone had were estimates from character counts. DeepSeek reports cache hits
    and misses separately and the two differ by roughly thirty times in price,
    so an estimate that cannot see the split is not an estimate of anything.

    Ollama does not use OpenAI's shape: there is no `usage` object, the counts
    are `prompt_eval_count` and `eval_count` at the top level, and there is no
    cache. Reading DeepSeek's names against an ollama reply produced "300 calls,
    0 input tokens, 0% cache hit" -- three numbers that look measured and are
    not. `reported` says whether anything was actually counted, so a caller can
    print nothing rather than print a zero.
    """
    if provider == "ollama":
        prompt = payload.get("prompt_eval_count") or 0
        completion = payload.get("eval_count") or 0
        return {
            "calls": 1,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "reported": 1 if (prompt or completion) else 0,
            "cache_reported": 0,
        }
    usage = payload.get("usage") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    return {
        "calls": 1,
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "cache_hit_tokens": hit or 0,
        "cache_miss_tokens": miss or 0,
        "reported": 1 if usage else 0,
        "cache_reported": 1 if (hit is not None or miss is not None) else 0,
    }


def _extract_content(provider: str, payload: dict) -> str:
    if provider == "ollama":
        return (payload.get("message") or {}).get("content", "")
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content", "") or ""


class EnrichClient:
    def __init__(self, http=None):
        self._http = http
        self._owns_http = http is None
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cache_hit_tokens": 0, "cache_miss_tokens": 0,
                      "reported": 0, "cache_reported": 0}

    def _record_usage(self, provider: str, payload: dict) -> None:
        for key, value in _extract_usage(provider, payload).items():
            self.usage[key] = self.usage.get(key, 0) + value

    async def _client(self):
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=settings.ENRICH_TIMEOUT)
        return self._http

    async def aclose(self):
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    async def _call_deepseek(self, messages: list[dict], model: str) -> str:
        http = await self._client()
        r = await http.post(
            f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        r.raise_for_status()
        payload = r.json()
        self._record_usage("deepseek", payload)
        return _extract_content("deepseek", payload)

    @staticmethod
    def _warn_if_prompt_overflows(messages: list[dict], model: str) -> None:
        """Ollama cuts an oversized prompt without saying so, and a prompt
        missing its schema or its body still returns confident-looking JSON. A
        warning is the only thing standing between that and a silently degraded
        corpus."""
        chars = sum(len(m.get("content") or "") for m in messages)
        budget = settings.ENRICH_LOCAL_NUM_CTX * settings.ENRICH_CHARS_PER_TOKEN
        if chars > budget:
            logger.warning(
                "prompt is %d chars, over the ~%d that fit %s's %d-token "
                "context; ollama will truncate it silently",
                chars, int(budget), model, settings.ENRICH_LOCAL_NUM_CTX)

    async def _call_ollama(self, messages: list[dict], model: str) -> str:
        self._warn_if_prompt_overflows(messages, model)
        http = await self._client()
        r = await http.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "format": "json",
                "stream": False,
                "think": False,
                # Ollama ignores what the model advertises (qwen2.5:7b
                # loaded at 4,096 against a declared 32,768) and truncates a
                # longer prompt silently, so the ceiling is set explicitly and
                # _warn_if_prompt_overflows says so when a prompt exceeds it.
                # See ENRICH_LOCAL_NUM_CTX for the measurements.
                "options": {"temperature": 0, "top_k": 1, "seed": 42,
                            "num_ctx": settings.ENRICH_LOCAL_NUM_CTX},
            },
        )
        r.raise_for_status()
        payload = r.json()
        self._record_usage("ollama", payload)
        return _extract_content("ollama", payload)

    async def complete(self, messages: list[dict], *, provider: str, model: str) -> dict:
        """One attempt. Raises ProviderError on anything unusable."""
        try:
            if provider == "ollama":
                content = await self._call_ollama(messages, model)
            else:
                content = await self._call_deepseek(messages, model)
        except Exception as exc:                       # network, 4xx, 5xx
            raise ProviderError(f"{provider}/{model}: {exc}") from exc

        if not content.strip():
            raise ProviderError(f"{provider}/{model}: empty content")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{provider}/{model}: response was not valid JSON: {exc}"
            ) from exc

    def _stages(self) -> list[tuple[str, str]]:
        head = settings.ENRICH_PROVIDER
        if head == "ollama":
            return [("ollama", settings.ENRICH_MODEL_LOCAL)] * 2
        stages = [
            ("deepseek", settings.ENRICH_MODEL),
            ("deepseek", settings.ENRICH_MODEL),           # repair retry
            ("deepseek", settings.ENRICH_MODEL_ESCALATION),
        ]
        if getattr(settings, "OLLAMA_URL", ""):
            stages.append(("ollama", settings.ENRICH_MODEL_LOCAL))
        return stages

    async def run_chain(
        self, messages: list[dict], *, rebuild=None
    ) -> tuple[dict, str]:
        """Walk the ladder. Returns (parsed_json, model_name).

        `rebuild(error_text) -> messages` lets the caller re-render the prompt
        with the validation error appended; without it the same messages are
        re-sent, which is still worth one attempt against a transient failure.
        """
        last: Exception | None = None
        current = messages
        for attempt, (provider, model) in enumerate(self._stages()):
            try:
                return await self.complete(current, provider=provider, model=model), model
            except ProviderError as exc:
                last = exc
                logger.warning("enrich attempt %d failed: %s", attempt + 1, exc)
                if rebuild is not None:
                    current = rebuild(str(exc))
                else:
                    current = _append_repair_error(messages, str(exc))
                # Backoff only between network-ish failures; a JSON parse
                # failure is instant to retry.
                if "429" in str(exc) or "timeout" in str(exc).lower():
                    await asyncio.sleep(2 ** attempt)
        raise ProviderError(f"all stages failed; last error: {last}")


def _append_repair_error(messages: list[dict], error: str) -> list[dict]:
    """Default repair: append the failure reason to the last user turn so the
    retry sees it, without rebuilding the whole prompt."""
    out = [dict(m) for m in messages]
    if out and out[-1]["role"] == "user":
        out[-1] = {
            **out[-1],
            "content": (
                out[-1]["content"]
                + "\n\nYour previous response could not be used. Fix exactly "
                f"this and return the corrected JSON object:\n{error}"
            ),
        }
    return out
