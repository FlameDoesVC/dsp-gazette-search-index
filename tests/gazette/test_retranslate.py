"""`--only-missing` exists so the durable translation can be filled in safely.

An English gazette title lives in `Iulaan.translated_title`, which is what the
adapter reads and therefore what survives a reindex. `fill_bilingual` writes
`SearchDocument` instead, so its translations are erased by the next reindex --
silently, since title_en is in indexing's _UPDATE_FIELDS and the adapter rebuilds
it from an empty source field.

Retranslating everything unconditionally was the only option before this flag,
which meant a long run could not be resumed and redid work it had already paid
for.
"""

import pytest
from django.core.management import call_command

from gazette.models import Iulaan


@pytest.fixture
def iulaans(db):
    done = Iulaan.objects.create(
        id="1", title="ފުރަތަމަ", translated_title="Already done",
        translated_body="Already done body", body="<p>ބޮޑީ</p>",
        additional_info={}, attachments={})
    todo = Iulaan.objects.create(
        id="2", title="ދެވަނަ", translated_title="", translated_body="",
        body="<p>ބޮޑީ ދެ</p>", additional_info={}, attachments={})
    return done, todo


def test_only_missing_leaves_an_existing_translation_alone(iulaans, monkeypatch):
    done, todo = iulaans
    calls = []

    def fake(text):
        calls.append(text)
        return f"EN({text})"

    monkeypatch.setattr(
        "gazette.management.commands.retranslate_gazette._translate", fake)
    call_command("retranslate_gazette", iulaans=True, only_missing=True)

    done.refresh_from_db()
    todo.refresh_from_db()
    assert done.translated_title == "Already done"
    assert todo.translated_title == "EN(ދެވަނަ)"
    assert "ފުރަތަމަ" not in calls


def test_without_the_flag_everything_is_retranslated(iulaans, monkeypatch):
    """The command's original purpose. A prompt or model change is a real reason
    to redo work that already exists."""
    done, todo = iulaans
    monkeypatch.setattr(
        "gazette.management.commands.retranslate_gazette._translate",
        lambda text: f"EN({text})")
    call_command("retranslate_gazette", iulaans=True)

    done.refresh_from_db()
    assert done.translated_title == "EN(ފުރަތަމަ)"


def test_one_unsaveable_row_does_not_lose_the_whole_pass(iulaans, monkeypatch):
    """`translated_title` was 255 chars against a 512-char `title`, so a long
    translation raised DataError -- and with no per-row handling it aborted the
    command, committing none of the rows already translated. 157 of 187 iulaan
    had no English title as a result."""
    from django.db import DatabaseError

    done, todo = iulaans
    third = Iulaan.objects.create(
        id="3", title="ތިންވަނަ", translated_title="", translated_body="",
        body="<p>ބޮޑީ ތިން</p>", additional_info={}, attachments={})

    monkeypatch.setattr(
        "gazette.management.commands.retranslate_gazette._translate",
        lambda text: f"EN({text})")

    real_save = Iulaan.save

    def flaky_save(self, *args, **kwargs):
        if self.id == "2":
            raise DatabaseError("value too long for type character varying(255)")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Iulaan, "save", flaky_save)
    call_command("retranslate_gazette", iulaans=True, only_missing=True)

    third.refresh_from_db()
    assert third.translated_title == "EN(ތިންވަނަ)"


def test_the_translation_target_is_at_least_as_wide_as_its_source():
    """A translation cannot be guaranteed to fit a field narrower than the text
    it translates, and Thaana writes vowels as diacritics so its English
    rendering is usually LONGER in characters."""
    for model_path, source, target in (
        ("gazette.models.Iulaan", "title", "translated_title"),
        ("gazette.models.Office", "name", "translated_name"),
        ("gazette.models.IulaanType", "name", "translated_name"),
    ):
        module, _, name = model_path.rpartition(".")
        model = getattr(__import__(module, fromlist=[name]), name)
        src = model._meta.get_field(source).max_length
        dst = model._meta.get_field(target).max_length
        assert dst >= src, f"{name}.{target} ({dst}) is narrower than {source} ({src})"
