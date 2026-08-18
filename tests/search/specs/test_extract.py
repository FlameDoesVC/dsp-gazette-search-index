import pytest

from search.models import SpecKey
from search.specs.extract import extract_units, unit_vocabulary


@pytest.fixture
def keys(db):
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["volt", "volts", "v"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="current", label_en="Current", datatype="numeric",
                           unit="A", unit_aliases=["amp", "amps", "a"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="power", label_en="Power", datatype="numeric",
                           unit="W", unit_aliases=["watt", "watts", "w"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="storage_gb", label_en="Storage", datatype="numeric",
                           unit="GB", unit_aliases=["gb", "gigabyte"],
                           widget="range", is_facetable=True)


@pytest.mark.django_db
def test_the_vocabulary_comes_from_the_registry(keys):
    """P4 hardcoded a list; P7 replaces it with the curated one so adding a
    unit is an admin row, not a deploy."""
    vocab = unit_vocabulary()
    assert "V" in vocab and "GB" in vocab
    assert vocab == sorted(vocab, key=len, reverse=True), (
        "longest-first, or 'A' shadows 'mAh'"
    )


@pytest.mark.django_db
def test_a_compact_spec_title(keys):
    got = extract_units("KICO METAL POWER SUPPLY 24V-5A-120W / 7884445")
    assert {(u["key"], u["value"]) for u in got} == {
        ("voltage", 24.0), ("current", 5.0), ("power", 120.0)
    }


@pytest.mark.django_db
def test_the_trailing_phone_number_is_not_a_spec(keys):
    got = extract_units("POWER SUPPLY 24V / 7884445")
    assert 7884445.0 not in [u["value"] for u in got]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text,key,value",
    [
        ("128GB storage", "storage_gb", 128.0),
        ("128 GB", "storage_gb", 128.0),
        ("24 volts DC", "voltage", 24.0),
        ("5 amps", "current", 5.0),
        ("1.5W", "power", 1.5),
    ],
)
def test_alias_and_spacing_variants(keys, text, key, value):
    got = extract_units(text)
    assert (key, value) in {(u["key"], u["value"]) for u in got}


@pytest.mark.django_db
def test_a_unit_with_no_registered_key_is_still_captured_as_key_raw(keys):
    """Extraction is open: an unregistered unit becomes a key_raw row so it can
    surface in the promotion queue. It just is not facetable."""
    got = extract_units("5000mAh battery")
    assert got and got[0]["key"] is None
    assert got[0]["key_raw"] == "mah"


@pytest.mark.django_db
def test_a_bare_year_is_not_a_spec(keys):
    assert extract_units("Model year 2019") == []


@pytest.mark.django_db
def test_extraction_is_deduplicated(keys):
    got = extract_units("24V power supply, 24V input")
    assert len([u for u in got if u["key"] == "voltage"]) == 1


@pytest.mark.django_db
def test_no_registry_rows_yields_no_units_rather_than_crashing(db):
    assert extract_units("24V-5A") == []
