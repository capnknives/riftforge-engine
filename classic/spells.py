"""spells.py -- spell list loaded from validated catalog JSON."""

from classic.content import load_spells_catalog

SPELLS = {}


def _ensure_loaded():
    global SPELLS
    if SPELLS:
        return
    SPELLS.update(load_spells_catalog())


def spell_ids_for_class(class_id):
    """Sorted spell ids this class may cast."""
    _ensure_loaded()
    out = []
    for spell_id, row in SPELLS.items():
        if class_id in row.get("classes", ()):
            out.append(spell_id)
    return sorted(out)


def get_spell(spell_id):
    """Return spell row or None."""
    _ensure_loaded()
    return SPELLS.get(str(spell_id or "").strip().lower())


def known_spells_for_class(class_id):
    """Player-facing spell lines for score/sheet."""
    _ensure_loaded()
    lines = []
    for spell_id in spell_ids_for_class(class_id):
        row = SPELLS[spell_id]
        lines.append(f"  {spell_id} -- {row['help']}")
    return lines
