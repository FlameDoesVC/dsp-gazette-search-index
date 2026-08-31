import os
import importlib
from django.conf import settings


def test_two_database_aliases_exist():
    assert "default" in settings.DATABASES
    assert "direct" in settings.DATABASES


def test_direct_alias_disables_pooling_behaviour():
    assert settings.DATABASES["direct"]["CONN_MAX_AGE"] == 0


def test_secret_key_comes_from_env(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "from-the-environment")
    import gazette_search.settings as s
    importlib.reload(s)
    assert s.SECRET_KEY == "from-the-environment"


def test_debug_defaults_off_when_env_absent(monkeypatch):
    monkeypatch.delenv("DJANGO_DEBUG", raising=False)
    import gazette_search.settings as s
    importlib.reload(s)
    assert s.DEBUG is False
