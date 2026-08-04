"""
languages.py -- known languages and room speech understandability (catalog).

``speak <language>`` sets your active tongue; ``say`` broadcasts in that
language. Listeners who do not know the language see a plain can't-understand
line instead of the words.

Game registers the catalog via ``set_languages_catalog`` in bootstrap.
``deliver_say`` stays in ``supers/languages.py`` (drunk + god-kind hooks).
"""

from __future__ import annotations

_DEFAULT_LANG = "english"
_LANGUAGES: dict = {}


def set_languages_catalog(catalog):
    """Register the language id -> entry dict (called from bootstrap)."""
    global _LANGUAGES
    if catalog is None:
        _LANGUAGES = {}
    else:
        _LANGUAGES = dict(catalog)


def languages_catalog():
    """Return the registered id -> entry dict (empty until bootstrap)."""
    return _LANGUAGES


def default_languages():
    """New characters start with English."""
    return [_DEFAULT_LANG]


def valid_ids():
    return set(_LANGUAGES.keys())


def display(lang_id):
    entry = _LANGUAGES.get(lang_id)
    return entry["name"] if entry else str(lang_id)


def active_language(character):
    """Language id used for the next ``say`` (default English)."""
    raw = getattr(character, "speaking_language", None) or _DEFAULT_LANG
    lang_id = str(raw).lower().replace(" ", "_")
    if lang_id not in valid_ids():
        return _DEFAULT_LANG
    return lang_id


def knows(character, lang_id):
    """True when ``character`` understands ``lang_id``."""
    if not lang_id:
        return True
    lang_id = str(lang_id).lower().replace(" ", "_")
    if lang_id == _DEFAULT_LANG:
        return True
    known = getattr(character, "languages", None) or default_languages()
    return lang_id in known


def parse_speak_set(args):
    """Parse ``speak spanish`` -> (lang_id, error)."""
    raw = (args or "").strip()
    if not raw:
        return None, None
    lang_id = raw.lower().replace(" ", "_")
    if lang_id in ("clear", "reset", "default"):
        return _DEFAULT_LANG, None
    if lang_id not in valid_ids():
        options = ", ".join(sorted(valid_ids()))
        return None, f"Unknown language '{raw}'. Known: {options}"
    return lang_id, None


def set_speaking_language(character, lang_id):
    """Switch active tongue if known; returns (ok, message)."""
    if lang_id == _DEFAULT_LANG:
        character.speaking_language = _DEFAULT_LANG
        return True, "You switch to speaking English."
    if not knows(character, lang_id):
        return False, (
            f"You don't know {display(lang_id)} well enough to speak it."
        )
    character.speaking_language = lang_id
    return True, f"You switch to speaking {display(lang_id)}. Use say to talk."
