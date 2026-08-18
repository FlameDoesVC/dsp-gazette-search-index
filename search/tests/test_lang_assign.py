import pytest

from search.lang.assign import route_bilingual


def test_english_goes_to_en_dhivehi_to_dv():
    assert route_bilingual("Administrative Officer") == ("Administrative Officer", "")
    assert route_bilingual("ވަޒީފާގެ ފުރުޞަތު") == ("", "ވަޒީފާގެ ފުރުޞަތު")


def test_both_are_kept_when_both_are_given():
    en, dv = route_bilingual("Officer", "އޮފިސަރ")
    assert (en, dv) == ("Officer", "އޮފިސަރ")


def test_arguments_arriving_in_the_wrong_order_are_corrected():
    """The concrete bug: three gazette rows have Thaana in title_en and Latin
    in title_dv. Routing by content makes that unrepresentable."""
    en, dv = route_bilingual("ވަޒީފާގެ ފުރުޞަތު", "Job Opportunity")
    assert en == "Job Opportunity"
    assert dv == "ވަޒީފާގެ ފުރުޞަތު"


def test_latin_dhivehi_counts_as_english_side_not_dhivehi():
    """`kudhin bahattaden` is Dhivehi in language but Latin in script. It
    renders LTR and belongs on the Latin side; direction follows script."""
    en, dv = route_bilingual("Vazeefaa ah dhaa firihen kudhin bahattaden")
    assert en.startswith("Vazeefaa")
    assert dv == ""


def test_a_mixed_string_lands_on_the_dhivehi_side():
    en, dv = route_bilingual("GS3 ގްރޭޑް")
    assert dv == "GS3 ގްރޭޑް"


def test_empty_and_none_are_safe():
    assert route_bilingual() == ("", "")
    assert route_bilingual("", None) == ("", "")


def test_the_first_non_empty_of_each_script_wins():
    en, dv = route_bilingual("First", "Second", "ފުރަތަމަ")
    assert en == "First" and dv == "ފުރަތަމަ"
