import json

import pytest

from enrich.client import EnrichClient, ProviderError
from enrich.preextract import extract_candidates
from enrich.prompts import PROMPT_VERSION, build_messages


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int) and PROMPT_VERSION >= 1


def test_system_prompt_is_identical_across_calls():
    """It is ~800 tokens and it must hit DeepSeek's context cache, so it
    cannot interpolate anything per-document. Spec 5.1."""
    a = build_messages(source="ibay", doc_type_prior="shopping", title="A",
                       body="b", candidates=extract_candidates("b"), scraped={})
    b = build_messages(source="gazette", doc_type_prior="job", title="C",
                       body="d", candidates=extract_candidates("d"), scraped={})
    assert a[0]["content"] == b[0]["content"]
    assert a[0]["role"] == "system"


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
