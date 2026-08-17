"""Parse gazette HTML tables into label/value pairs. Spec 5.2.

Gazette bodies are Word-exported HTML and the tables inside them are already
labelled key-value pairs -- `<td>އަސާސީ މުސާރަ:</td><td>މަހަކު 10,750 ރުފިޔާ</td>`.
Structure the source gave us for free should not be re-derived by a language
model, so it is parsed here and handed to P4's extraction as pairs rather than
as markup.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_TRAILING = re.compile(r"[:\s]+$")

MAX_LABEL_CHARS = 120


def _text(node) -> str:
    parts: list[str] = []
    for item in node.iter():
        if item.tag == "li" and item.text_content().strip():
            parts.append(item.text_content().strip())
    if parts:
        return " | ".join(parts)
    return _WS.sub(" ", node.text_content()).strip()


def parse_label_value_pairs(html: str) -> list[tuple[str, str]]:
    if not html or "<" not in html:
        return []
    try:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    pairs: list[tuple[str, str]] = []
    for row in tree.iter("tr"):
        cells = list(row.iter("td"))
        if len(cells) < 2:
            continue
        label = _TRAILING.sub("", _WS.sub(" ", cells[0].text_content()).strip())
        value = _text(cells[1])
        if not label or not value or len(label) > MAX_LABEL_CHARS:
            continue
        pairs.append((label, value))
    return pairs
