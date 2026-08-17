"""Backwards-compatible shim. The translation client now lives in `core`,
because enrichment and search need it too -- see spec section 3."""

from core.translate import (  # noqa: F401
    is_dhivehi,
    sentence_boundary,
    translate_auto,
    translate_auto_sync,
    translate_dv_to_en,
    translate_dv_to_en_sync,
    translate_en_to_dv,
    translate_en_to_dv_sync,
)
