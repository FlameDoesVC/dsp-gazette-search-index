import pytest

from search.filters import Filter, FilterError, filter_sql, parse_filters

# parse_filters resolves unknown keys against the SpecKey registry (P7), so
# the whole module needs a DB connection now.
pytestmark = pytest.mark.django_db


def test_enum_filter():
    fs = parse_filters(["job_category:Accounting"], "job")
    assert fs == [Filter(key="job_category", op="eq", values=["Accounting"])]


def test_repeated_key_becomes_an_or_within_the_key():
    fs = parse_filters(["brand:Apple", "brand:Samsung"], "shopping")
    assert len(fs) == 1
    assert fs[0].values == ["Apple", "Samsung"]


def test_range_filter():
    fs = parse_filters(["price:1000..5000"], "shopping")
    assert fs[0].op == "range"
    assert fs[0].lo == 1000.0 and fs[0].hi == 5000.0


def test_open_ended_ranges():
    assert parse_filters(["price:1000.."], "shopping")[0].hi is None
    assert parse_filters(["price:..5000"], "shopping")[0].lo is None


def test_toggle_filter():
    fs = parse_filters(["has_lift:true"], "property")
    assert fs[0].op == "bool" and fs[0].values == [True]


def test_an_unknown_key_is_rejected():
    """Whitelisted against the facet registry. An unknown key must be a 400,
    never a query fragment."""
    with pytest.raises(FilterError) as exc:
        parse_filters(["'; DROP TABLE search_searchdocument; --:x"], "job")
    assert "unknown filter" in str(exc.value)


def test_a_key_valid_for_another_type_is_rejected_for_this_one():
    with pytest.raises(FilterError):
        parse_filters(["bedrooms:3"], "job")


def test_a_malformed_range_is_rejected():
    with pytest.raises(FilterError):
        parse_filters(["price:cheap..expensive"], "shopping")


def test_a_filter_with_no_colon_is_rejected():
    with pytest.raises(FilterError):
        parse_filters(["price"], "shopping")


def test_values_never_reach_sql_as_text():
    """Every value must arrive as a bound parameter. The generated SQL must
    contain no literal from the user."""
    sql, params = filter_sql(parse_filters(["job_category:O'Brien & Co"], "job"))
    assert "O'Brien" not in sql
    assert "O'Brien & Co" in list(params.values())[0]


def test_filter_sql_for_a_column_backed_facet():
    sql, params = filter_sql(parse_filters(["price:1000..5000"], "shopping"))
    assert "d.price" in sql
    assert 1000.0 in params.values() and 5000.0 in params.values()


def test_filter_sql_for_a_jsonb_backed_facet():
    sql, params = filter_sql(parse_filters(["job_category:Accounting"], "job"))
    assert "attrs" in sql
    assert "->>" in sql


def test_filter_sql_for_an_array_backed_facet():
    """tenant_preference is a JSON array; membership, not equality."""
    sql, _ = filter_sql(parse_filters(["tenant_preference:family"], "property"))
    assert "jsonb_array_elements_text" in sql or "?|" in sql


def test_no_filters_is_an_empty_clause_not_a_syntax_error():
    sql, params = filter_sql([])
    assert sql.strip() == ""
    assert params == {}
