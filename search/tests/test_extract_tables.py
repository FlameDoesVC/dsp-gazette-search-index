from search.extract.tables import parse_label_value_pairs

REAL_BODY = """
<table><tr>
<td width="150"><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>
<td width="509"><p dir="RTL"> މަހަކު 10,750 ރުފިޔާ</p></td>
</tr><tr>
<td valign="top" width="150"><p dir="RTL"><strong>އެލަވަންސް/އިނާޔަތްތައް:</strong></p></td>
<td width="509"><ul>
<li>ހާޒިރީ އެލަވަންސްގެ ގޮތުގައި ހަމަޖެހިފައިވާ އުސޫލުން މަހަކު 4,400 ރުފިޔާ</li>
<li>ލިވިންގ އެލަވަންސް</li>
</ul></td>
</tr></table>
"""


def test_extracts_label_value_pairs():
    pairs = dict(parse_label_value_pairs(REAL_BODY))
    assert "އަސާސީ މުސާރަ" in " ".join(pairs)
    assert any("10,750" in v for v in pairs.values())


def test_list_items_are_preserved_within_a_value():
    pairs = dict(parse_label_value_pairs(REAL_BODY))
    allowances = next(v for k, v in pairs.items() if "އެލަވަންސް" in k)
    assert "4,400" in allowances
    assert "ލިވިންގ" in allowances


def test_no_markup_survives():
    for _label, value in parse_label_value_pairs(REAL_BODY):
        for token in ("<td", "dir=", "<li", "valign", "<strong"):
            assert token not in value


def test_trailing_colons_are_stripped_from_labels():
    for label, _value in parse_label_value_pairs(REAL_BODY):
        assert not label.endswith(":")


def test_non_table_html_yields_nothing():
    assert parse_label_value_pairs("<p>just a paragraph</p>") == []


def test_empty_input_is_safe():
    assert parse_label_value_pairs("") == []
    assert parse_label_value_pairs(None) == []
