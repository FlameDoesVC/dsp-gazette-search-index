import json

import pytest

from enrich.client import EnrichClient, ProviderError
from enrich.preextract import extract_candidates
from enrich.prompts import PROMPT_VERSION, build_messages


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int) and PROMPT_VERSION >= 1


def test_system_prompt_is_identical_across_calls_of_one_doc_type():
    """It must hit DeepSeek's context cache, so it cannot interpolate anything
    per-document. Spec 5.1.

    The prefix is now per doc type rather than global: 79% of the old prompt was
    the schemas of doc types the call could not use. A pass runs one doc_type at
    a time, so a per-type prefix still hits the cache on every call after the
    first.
    """
    a = build_messages(source="other", doc_type_prior="shopping", title="A",
                       body="b", candidates=extract_candidates("b"), scraped={})
    b = build_messages(source="gazette", doc_type_prior="shopping", title="C",
                       body="d", candidates=extract_candidates("d"),
                       scraped={"office": "Ministry of Example"})
    assert a[0]["content"] == b[0]["content"]
    assert a[0]["role"] == "system"
    # Nothing per-document reached it.
    assert "Ministry of Example" not in b[0]["content"]
    assert "7994400" not in b[0]["content"]


def test_each_doc_type_gets_only_its_own_schema():
    from enrich.prompts import system_prompt

    shopping = system_prompt("shopping")
    job = system_prompt("job")
    assert shopping != job
    # `basic_salary` belongs to the job schema and has no business in a shopping
    # call. It was in every one of them.
    assert "basic_salary" in job
    assert "basic_salary" not in shopping
    # And the reverse, so this cannot pass by sending nothing at all.
    assert "seller_type" in shopping
    assert "seller_type" not in job
    assert len(shopping) < 6000


def test_an_unknown_doc_type_falls_back_rather_than_failing():
    from enrich.prompts import system_prompt

    assert system_prompt("nonsense") == system_prompt("news")
    assert system_prompt("") == system_prompt("news")


def test_user_prompt_carries_prior_candidates_and_scraped_truth():
    c = extract_candidates("Call 7994400, 10,750 rufiyaa")
    msgs = build_messages(
        source="gazette", doc_type_prior="job", title="Officer",
        body="Call 7994400, 10,750 rufiyaa", candidates=c,
        scraped={"office": "Ministry of Example"},
    )
    user = msgs[1]["content"]
    assert "job" in user
    assert "7994400" in user
    assert "Ministry of Example" in user


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHTTP:
    """Records every call so the test can assert on the escalation ladder."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    async def aclose(self):
        pass


def _deepseek_reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}]}


@pytest.mark.asyncio
async def test_happy_path_uses_flash_once(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([_deepseek_reply({"doc_type": "job"})])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "job"}
    assert model == settings.ENRICH_MODEL
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_unparseable_json_triggers_a_repair_retry_on_the_same_model(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": "not json at all"}}]},
        _deepseek_reply({"doc_type": "news"}),
    ])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}
    assert model == settings.ENRICH_MODEL
    assert len(http.calls) == 2
    # the repair call must carry the error text back in
    assert "not valid JSON" in json.dumps(http.calls[1][1])


@pytest.mark.asyncio
async def test_empty_content_is_a_failed_attempt_not_an_empty_result(settings):
    """DeepSeek documents occasional empty-content responses. Treating one as
    a valid empty extraction would silently blank a record. Spec 5.2 layer 2."""
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": ""}}]},
        _deepseek_reply({"doc_type": "news"}),
    ])
    client = EnrichClient(http=http)
    payload, _ = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}


@pytest.mark.asyncio
async def test_two_failures_escalate_to_pro(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": "junk"}}]},
        {"choices": [{"message": {"content": "junk again"}}]},
        _deepseek_reply({"doc_type": "property"}),
    ])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "property"}
    assert model == settings.ENRICH_MODEL_ESCALATION


@pytest.mark.asyncio
async def test_everything_failing_raises_provider_error(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    settings.OLLAMA_URL = ""
    http = _FakeHTTP([{"choices": [{"message": {"content": "junk"}}]}] * 3)
    client = EnrichClient(http=http)
    with pytest.raises(ProviderError):
        await client.run_chain([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_ollama_provider_sends_deterministic_options(settings):
    settings.ENRICH_PROVIDER = "ollama"
    settings.OLLAMA_URL = "http://gpu:11434"
    http = _FakeHTTP([{"message": {"content": json.dumps({"doc_type": "news"})}}])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}
    opts = http.calls[0][1]["json"]["options"]
    assert opts["temperature"] == 0
    assert opts["top_k"] == 1
    assert opts["seed"] == 42
    assert http.calls[0][1]["json"]["think"] is False


def test_ollama_token_counts_are_read_from_ollamas_own_field_names():
    """Ollama has no `usage` object: the counts are prompt_eval_count and
    eval_count at the top level. Reading DeepSeek's names against an ollama
    reply produced '300 calls, 0 input tokens, 0% cache hit' after a real
    300-document pass -- three numbers that look measured and are not."""
    from enrich.client import _extract_usage

    reply = {"model": "mistral:latest", "prompt_eval_count": 1180,
             "eval_count": 267, "done": True}
    usage = _extract_usage("ollama", reply)
    assert usage["prompt_tokens"] == 1180
    assert usage["completion_tokens"] == 267
    assert usage["reported"] == 1
    # There is no cache, so the caller must not print a cache percentage.
    assert usage["cache_reported"] == 0


def test_deepseek_cache_split_is_kept():
    from enrich.client import _extract_usage

    reply = {"usage": {"prompt_tokens": 3300, "completion_tokens": 267,
                       "prompt_cache_hit_tokens": 3100,
                       "prompt_cache_miss_tokens": 200}}
    usage = _extract_usage("deepseek", reply)
    assert usage["cache_hit_tokens"] == 3100
    assert usage["cache_miss_tokens"] == 200
    assert usage["cache_reported"] == 1


def test_a_provider_that_reports_nothing_says_so():
    """The distinction that matters: 'reported' separates a real zero from an
    absent measurement."""
    from enrich.client import _extract_usage

    assert _extract_usage("ollama", {"done": True})["reported"] == 0
    assert _extract_usage("deepseek", {})["reported"] == 0


def test_an_error_names_its_exception_type():
    """str(httpx.ReadTimeout()) is "", so formatting the exception alone gave
    'ollama/mistral:latest: ' with nothing after the colon. 35 timeouts arrived
    looking identical to empty replies, which sent the diagnosis after the model
    instead of the timeout."""
    import httpx
    import pytest as _pytest
    from enrich.client import EnrichClient, ProviderError

    class _Boom:
        async def post(self, url, **kwargs):
            raise httpx.ReadTimeout("")

        async def aclose(self):
            pass

    async def _run():
        client = EnrichClient(http=_Boom())
        with _pytest.raises(ProviderError) as excinfo:
            await client.complete([{"role": "user", "content": "x"}],
                                  provider="ollama", model="m")
        return str(excinfo.value)

    import asyncio
    message = asyncio.run(_run())
    assert "ReadTimeout" in message
    # And it must not end in a bare dangling colon.
    assert not message.rstrip().endswith(":")


def test_num_ctx_is_per_client_not_global(settings):
    """Enrichment and profiling differ by 5x in prompt size. One global value
    serves the worse of both, and ollama's per-slot KV cache makes the oversized
    choice actively harmful rather than merely wasteful."""
    from enrich.client import EnrichClient

    settings.ENRICH_LOCAL_NUM_CTX = 4096
    assert EnrichClient().num_ctx == 4096
    assert EnrichClient(num_ctx=16384).num_ctx == 16384
