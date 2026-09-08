"""Language-independent category codes; matching never changes display text."""
from __future__ import annotations

import unicodedata


# Keep the accepted planner's legacy families as an adapter, not translated enums.
CANONICAL_TYPES = {
    "speaker": "pa", "powered_speaker": "pa", "monitor": "other",
    "microphone": "microphone", "mixer": "mixer", "cable": "cable",
    "lighting": "lighting", "power": "power", "vehicle": "transport",
    "furniture": "furniture", "video": "video", "rigging": "rigging",
    "hospitality": "catering", "site": "other", "other": "other",
    **{code: code for code in (
        "di", "backline", "stand", "case", "accessory", "decor", "tool", "display", "barrier"
    )},
}
LEGACY_CODES = {"pa": "speaker", "transport": "vehicle", "catering": "hospitality"}


def normalize_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(
        " " if unicodedata.category(char)[0] in {"P", "Z"} else char
        for char in value
    ).split())


_ALIASES = {
    "speaker": ("speakers", "PA speaker", "רמקול", "רמקולים", "مكبر صوت", "مكبرات الصوت", "altavoz", "altavoces", "haut-parleur", "Lautsprecher"),
    "powered_speaker": ("powered speaker", "active speaker", "רמקולים מוגברים", "مكبر صوت نشط"),
    "microphone": ("microphones", "mic", "מיקרופון", "מיקרופונים", "ميكروفون", "ميكروفونات", "micrófono", "micrófonos", "Mikrofon"),
    "cable": ("cables", "כבל", "כבלים", "كابل", "كابلات", "câble", "Kabel"),
    "furniture": ("tables", "שולחנות", "ריהוט", "أثاث", "طاولات", "muebles", "Möbel"),
    "monitor": ("stage monitor", "מוניטור במה"),
}
BUILTIN_ALIASES = {
    normalize_match(label): code
    for code, labels in _ALIASES.items() for label in labels
}
BUILTIN_ALIASES.update({normalize_match(code): code for code in CANONICAL_TYPES})
BUILTIN_ALIASES.update({normalize_match(code): target for code, target in LEGACY_CODES.items()})


def index_workspace_terms(terms) -> dict[str, str]:
    index = {term["normalized_label"]: term["canonical_code"] for term in terms}
    index.update({normalize_match(term["canonical_code"]): term["canonical_code"]
                  for term in terms if term["kind"] == "category"})
    return index


def resolve_category(label: str, workspace_terms=()) -> dict:
    if not isinstance(label, str) or len(label) > 200 or "\x00" in label:
        raise ValueError("Category label must be text of at most 200 characters.")
    normalized = normalize_match(label)
    # Canonical codes cannot be shadowed by a workspace alias.
    code = label if label in CANONICAL_TYPES else LEGACY_CODES.get(label)
    source = "system"
    if code is None:
        index = workspace_terms if isinstance(workspace_terms, dict) else index_workspace_terms(workspace_terms)
        match = index.get(normalized)
        if match:
            code, source = match, "workspace"
        else:
            code = BUILTIN_ALIASES.get(normalized)
    return {
        "input_label": label, "canonical_type": code,
        "legacy_type": CANONICAL_TYPES.get(code, "other") if code else None,
        "source": source if code else None,
        "confidence": "high" if code else "needs_review",
        "confirmed_by_user": source == "workspace" and code is not None,
    }
