import hashlib
from io import StringIO
from unittest.mock import patch

import pytest

from core.models import TranslationCache
from enrich.management.commands.translate_card_vocab import _harvest
from enrich.models import EnrichedRecord


def _job_record(**attrs_kw):
    attrs = {
        "role": "Laboratory Technician",
        "qualifications": ["A degree in a related field"],
        "required_documents": ["Updated Curriculum Vitae (CV)"],
        "compensation": {"allowances": [{"label_raw": "Service Allowance"}]},
        "apply_methods": [{"kind": "form", "label_en": "Online via form link"}],
    }
    attrs.update(attrs_kw)
    return EnrichedRecord.objects.create(
        source="gazette", source_key="1", content_hash="h",
        doc_type="job", status="ok", attrs=attrs,
    )


def test_harvest_collects_every_free_text_field():
    attrs = {
        "role": "Laboratory Technician",
        "employer": "The Maldives National University",
        "qualifications": ["A degree"],
        "required_documents": ["A CV"],
        "compensation": {"allowances": [{"label_raw": "Service Allowance"}]},
        "apply_methods": [{"label_en": "Online via form link"}],
    }
    assert _harvest(attrs) == {
        "Laboratory Technician", "The Maldives National University",
        "A degree", "A CV", "Service Allowance", "Online via form link",
    }


def test_harvest_ignores_blank_and_short_strings():
    attrs = {"role": "", "qualifications": ["yes", "AB"], "required_documents": []}
    assert _harvest(attrs) == {"yes"}


@pytest.mark.django_db
def test_translates_only_strings_missing_from_the_cache():
    _job_record()
    TranslationCache.objects.create(
        source_hash=hashlib.sha256("Laboratory Technician".encode()).hexdigest(),
        translated_text="already cached")

    with patch("core.translate.translate_batch_sync") as mocked:
        mocked.return_value = ["x"] * 4
        from django.core.management import call_command
        call_command("translate_card_vocab", stdout=StringIO())

    sent = mocked.call_args.args[0]
    assert "Laboratory Technician" not in sent
    assert "Service Allowance" in sent
    assert "Updated Curriculum Vitae (CV)" in sent


@pytest.mark.django_db
def test_dry_run_translates_nothing():
    _job_record()
    with patch("core.translate.translate_batch_sync") as mocked:
        from django.core.management import call_command
        call_command("translate_card_vocab", "--dry-run", stdout=StringIO())
    mocked.assert_not_called()


@pytest.mark.django_db
def test_source_filter_is_respected():
    _job_record()
    EnrichedRecord.objects.create(
        source="other", source_key="2", content_hash="h", doc_type="job",
        status="ok", attrs={"role": "Should not be harvested"},
    )
    with patch("core.translate.translate_batch_sync") as mocked:
        mocked.return_value = ["x"] * 5
        from django.core.management import call_command
        call_command("translate_card_vocab", "--source", "gazette", stdout=StringIO())
    sent = mocked.call_args.args[0]
    assert "Should not be harvested" not in sent


@pytest.mark.django_db
def test_uses_the_general_ladder_not_a_dedicated_model():
    """A dedicated small translation model was tried and measured to produce
    script-valid but semantically wrong Dhivehi on a large fraction of both
    short labels and long sentences -- there is no reliable subset left to
    hand it, so this always goes through the general ladder (no override)."""
    _job_record()
    with patch("core.translate.translate_batch_sync") as mocked:
        mocked.return_value = ["x"] * 5
        from django.core.management import call_command
        call_command("translate_card_vocab", stdout=StringIO())
    assert "model" not in mocked.call_args.kwargs
