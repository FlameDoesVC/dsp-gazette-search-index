from unittest.mock import patch

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_dry_run_lists_every_step_without_calling_any_of_them(capsys):
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--dry-run")
    mocked.assert_not_called()
    out = capsys.readouterr().out
    for name in ("compilemessages", "sync_gazette", "fill_entity_translations",
                 "retranslate_gazette", "extract_attachments", "reindex",
                 "enrich_documents", "translate_card_vocab", "fill_bilingual",
                 "backfill_phones", "dedupe_listings", "sync_specs",
                 "rebuild_suggest_terms"):
        assert name in out


@pytest.mark.django_db
def test_default_run_calls_every_step_in_order():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline")
    names = [c.args[0] for c in mocked.call_args_list]
    assert names == [
        "compilemessages", "sync_gazette", "fill_entity_translations",
        "retranslate_gazette", "extract_attachments", "reindex",
        "enrich_documents", "translate_card_vocab", "reindex", "fill_bilingual",
        "backfill_phones", "dedupe_listings", "sync_specs", "sync_specs",
        "sync_specs", "rebuild_suggest_terms",
    ]


@pytest.mark.django_db
def test_skip_flags_omit_their_steps():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--skip-sync", "--skip-translate",
                     "--skip-attachments", "--skip-enrich")
    names = [c.args[0] for c in mocked.call_args_list]
    assert names == [
        "compilemessages", "reindex", "fill_bilingual", "backfill_phones",
        "dedupe_listings", "sync_specs", "sync_specs", "sync_specs",
        "rebuild_suggest_terms",
    ]


@pytest.mark.django_db
def test_full_sync_flag_passes_full_to_sync_gazette():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--full-sync", "--skip-translate",
                     "--skip-attachments", "--skip-enrich")
    sync_call = next(c for c in mocked.call_args_list if c.args[0] == "sync_gazette")
    assert sync_call.kwargs == {"full": True}


@pytest.mark.django_db
def test_enrich_provider_override_is_passed_through():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--skip-sync", "--skip-translate",
                     "--skip-attachments", "--enrich-provider", "deepseek")
    enrich_call = next(c for c in mocked.call_args_list if c.args[0] == "enrich_documents")
    assert enrich_call.kwargs == {"source": "gazette", "cold_pass": True,
                                  "provider": "deepseek"}


@pytest.mark.django_db
def test_translate_card_vocab_runs_after_enrich_with_skip_enrich_gating_it():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--skip-sync", "--skip-translate",
                     "--skip-attachments")
    names = [c.args[0] for c in mocked.call_args_list]
    assert names.index("translate_card_vocab") == names.index("enrich_documents") + 1

    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--skip-sync", "--skip-translate",
                     "--skip-attachments", "--skip-enrich")
    names = [c.args[0] for c in mocked.call_args_list]
    assert "translate_card_vocab" not in names


@pytest.mark.django_db
def test_sync_specs_uses_doc_type_dest_not_type():
    with patch("search.management.commands.run_pipeline.call_command") as mocked:
        call_command("run_pipeline", "--skip-sync", "--skip-translate",
                     "--skip-attachments", "--skip-enrich")
    spec_calls = [c for c in mocked.call_args_list if c.args[0] == "sync_specs"]
    assert [c.kwargs["doc_type"] for c in spec_calls] == ["job", "property", "news"]
