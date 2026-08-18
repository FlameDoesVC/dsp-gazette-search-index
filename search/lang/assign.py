"""Route text to the correct language field by its script. Spec 10.

A field named `_en` holds English and a field named `_dv` holds Dhivehi. That
is decided here, from the content, so an adapter cannot get it wrong by
assuming its source's default language -- which is exactly how three gazette
rows ended up with Thaana in `title_en`.

Latin-script Dhivehi stays on the English side on purpose: `kudhin bahattaden`
is Dhivehi in language but Latin in script, it renders left-to-right, and
`vector_latin` is where it is searched from. Direction follows script.
"""

from __future__ import annotations

from search.lang.normalize import contains_thaana


def route_bilingual(*texts: str | None) -> tuple[str, str]:
    """Returns (english_side, dhivehi_side). First non-empty of each wins."""
    en = dv = ""
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        if contains_thaana(text):
            dv = dv or text
        else:
            en = en or text
    return en, dv
