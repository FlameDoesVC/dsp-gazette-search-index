import pytest


@pytest.mark.django_db
def test_meta_lists_active_sources_with_icons(api, sources):
    r = api.get("/api/v1/meta")
    assert r.status_code == 200
    body = r.json()
    keys = [s["key"] for s in body["sources"]]
    assert keys == ["gazette", "other"]          # ordered, deterministic
    gazette = body["sources"][0]
    assert gazette["label_dv"] == "ގެޒެޓް"
    assert gazette["icon"] == "/sources/gazette.png"
    assert gazette["icon_fallback_text"] == "ގ"
    assert gazette["site_url"].startswith("https://")


@pytest.mark.django_db
def test_meta_omits_inactive_sources(api, sources):
    assert "retired" not in [s["key"] for s in api.get("/api/v1/meta").json()["sources"]]


@pytest.mark.django_db
def test_meta_lists_the_six_tabs_in_order(api, sources):
    tabs = api.get("/api/v1/meta").json()["tabs"]
    assert [t["key"] for t in tabs] == [
        "all", "shopping", "job", "property", "news", "images"
    ]
    assert all(t["label_en"] and t["label_dv"] for t in tabs)


@pytest.mark.django_db
def test_meta_is_cacheable_and_carries_no_per_request_state(api, sources):
    r = api.get("/api/v1/meta")
    assert "no-store" not in r.headers.get("Cache-Control", "")


@pytest.mark.django_db
def test_openapi_schema_is_served(api):
    assert api.get("/api/v1/openapi.json").status_code == 200
