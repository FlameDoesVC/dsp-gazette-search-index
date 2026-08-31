"""The guard against a runaway crawl.

Both syncs cap pages with `5 if settings.DEBUG else <unlimited>`, and
DJANGO_DEBUG defaults to 0 in this repo -- tests_settings asserts that it does.
So the cap resolved to 3500 pages for gazette and no cap at all for other on any
machine that had not opted in, and a sync loop running every 5 minutes crawled
the whole site each cycle. These checks cover the mechanism that actually stops
it, which needs no configuration to work.
"""

import pytest

from gazette.sync_service import seen_streak


def test_no_pages_means_no_streak():
    assert seen_streak({}) == 0


def test_a_run_of_fully_seen_pages_counts():
    assert seen_streak({1: True, 2: True, 3: True}) == 3


def test_a_new_listing_breaks_the_run():
    assert seen_streak({1: True, 2: False, 3: True}) == 1


def test_the_longest_run_in_the_prefix_wins():
    assert seen_streak({1: True, 2: False, 3: True, 4: True}) == 2


def test_pages_completing_out_of_order_do_not_count_across_a_gap():
    """Three page workers run concurrently, so completions arrive out of order.
    A naive last-N-completions counter would fire on pages 4, 9 and 12."""
    assert seen_streak({1: True, 4: True, 9: True, 12: True}) == 1


def test_a_page_that_failed_is_absent_and_stops_the_walk():
    """A page that raised is never recorded, so an HTTP error returning zero
    links cannot pass for 'nothing new here' and end the crawl."""
    assert seen_streak({1: True, 3: True, 4: True}) == 1


def test_a_full_prefix_of_new_material_never_stops():
    assert seen_streak({1: False, 2: False, 3: False}) == 0


def test_gazette_sync_stops_after_seen_pages_by_default():
    """The default must be on. A guard that needs an env var set is the same
    mistake as the DEBUG-keyed cap it replaces."""
    from gazette import sync_service as m
    assert m.STOP_AFTER_SEEN_PAGES >= 1
    assert hasattr(m, "MAX_INDEX_PAGES")


def test_full_flag_exists_to_override():
    """A backfill has to remain possible, explicitly."""
    from django.core.management import load_command_class
    parser = load_command_class("gazette", "sync_gazette").create_parser(
        "manage.py", "sync_gazette")
    flags = {a.dest for a in parser._actions}
    assert {"full", "max_pages", "stop_after_seen"} <= flags
