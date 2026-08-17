import pytest
from django.test import override_settings

from search import indexing
from search.adapters.base import DocumentDraft


def _tag_overlay(draft):
    draft.title_en = draft.title_en + " [OVERLAID]"
    return draft


@override_settings(SEARCH_DRAFT_OVERLAYS=["tests.search.test_overlay_hook._tag_overlay"])
def test_overlays_are_applied_in_order():
    d = DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                      url="https://x", title_en="Thing")
    out = indexing.apply_overlays(d)
    assert out.title_en == "Thing [OVERLAID]"


@override_settings(SEARCH_DRAFT_OVERLAYS=[])
def test_no_overlays_configured_is_a_no_op():
    d = DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                      url="https://x", title_en="Thing")
    assert indexing.apply_overlays(d) is d
