from search.interleave import interleave
from search.query import SearchResult


def _r(i, doc_type):
    return SearchResult(id=i, source="ibay", source_key=str(i), doc_type=doc_type,
                        url="https://x", title="t", summary="s", card={},
                        score=1.0 / i, matched_lang="en")


def test_no_more_than_three_consecutive_results_of_one_type():
    """Spec 8: 16k shopping listings must not bury 306 iulaan.

    The cap holds while a choice exists. When one type is exhausted the
    remaining tail is emitted as-is: with 20 shopping and 4 jobs a strict run
    cap of 3 is arithmetically impossible (4 jobs open 5 gaps of 3 = 15 slots),
    and dropping the overflow would contradict spec 12.6's nothing-destroyed
    rule."""
    results = [_r(i, "shopping") for i in range(1, 21)]
    results += [_r(i, "job") for i in range(21, 25)]
    out = interleave(results, cap=3)
    run, prev = 0, None
    for idx, r in enumerate(out):
        run = run + 1 if r.doc_type == prev else 1
        prev = r.doc_type
        if run > 3:
            # The only legal overrun is the terminal homogeneous tail.
            assert all(x.doc_type == prev for x in out[idx:])


def test_interleaving_preserves_every_result():
    results = [_r(i, "shopping") for i in range(1, 11)] + [_r(11, "job")]
    assert len(interleave(results)) == 11
    assert {r.id for r in interleave(results)} == {r.id for r in results}


def test_relative_order_within_a_type_is_preserved():
    results = [_r(i, "shopping") for i in range(1, 8)] + [_r(8, "job")]
    out = [r.id for r in interleave(results, cap=3) if r.doc_type == "shopping"]
    assert out == sorted(out)


def test_a_single_type_result_set_is_returned_unchanged():
    results = [_r(i, "news") for i in range(1, 6)]
    assert [r.id for r in interleave(results, cap=3)] == [1, 2, 3, 4, 5]
