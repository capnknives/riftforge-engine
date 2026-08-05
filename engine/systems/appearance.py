"""appearance.py -- generic slot-based appearance catalog + description builder.

Character creation stores curated slots (hair style/color, eyes, height,
physique, skin tone) on ``Character.appearance`` and derives
``Character.description`` from them for look/examine. Physique is
deliberately NOT named body_type -- that field is the combat-lexicon axis
for creatures (humanoid/quadruped), a different concern.

Freeform description override is game-owned (``Character.desc_override`` +
``setdesc``): when the override flag is set, ``apply_appearance`` leaves
the hand-written description alone. ``setdesc clear`` turns the flag off
and rebuilds from slots when complete.

Catalog JSON paths and kit registries are injected via ``engine.hooks``
(two-repo purity H7b). Pure data + string building: zero ``supers`` imports.
"""

from __future__ import annotations

import json

from engine import content_validate as cv
from engine import hooks as _hooks

# Core look slots -- required for is_complete / auto description.
CORE_SLOTS = (
    "hair_style",
    "hair_color",
    "eye_color",
    "height",
    "physique",
    "skin_tone",
)

# Mortal-only identity extras -- prompted at create; optional on legacy saves.
EXTENDED_SLOTS = (
    "facial_hair",
    "scars",
    "voice",
)

# All slots for the mortal kit (chargen + ``appearance`` listing).
SLOTS = CORE_SLOTS + EXTENDED_SLOTS

# Max length for a player-typed custom slot value (catalog ids are short;
# freeform like "storm-grey" or "sun-bleached wheat" needs a modest cap).
CUSTOM_MAX_LEN = 40

# Room-face short-desc override (setshort) -- shorter than setdesc so
# look listings stay skim-friendly for strangers.
SHORT_DESC_MAX_LEN = 60

# Pronoun ids players may pick (section 7 item 4 / D17).
PRONOUNS = ("he", "she", "they")

# Map pronoun -> noun used in the auto-built look description.
_PERSON_WORD = {
    "he": "man",
    "she": "woman",
    "they": "person",
}


def _load_mortal_catalog():
    """Read the mortal appearance catalog via the registered content path."""
    path = _hooks.appearance_content_path()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def slots_for_kit(kit_id=None):
    """Ordered slot list for chargen / appearance edits in ``kit_id``."""
    if kit_id and kit_id != "mortal":
        return CORE_SLOTS
    return SLOTS


def validate_appearance_slot_entry(entry, *, where):
    """Fail loud if one appearance option row is malformed."""
    cv.require_keys(entry, ("id", "name"), where)
    cv.require_nonempty_str(entry, "id", where)
    cv.require_nonempty_str(entry, "name", where)


def _validate_kit(kit_id, catalog):
    """Sanity-check one appearance kit catalog (same rules as mortal)."""
    required = slots_for_kit(kit_id)
    for slot in required:
        assert slot in catalog, \
            f"appearance kit {kit_id!r}: missing slot '{slot}'"
        entries = catalog[slot]
        assert entries, f"appearance kit {kit_id!r}: slot '{slot}' is empty"
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids)), \
            f"appearance kit {kit_id!r}: slot '{slot}' has duplicate ids"
        for i, entry in enumerate(entries):
            validate_appearance_slot_entry(
                entry, where=f"appearance kit {kit_id!r}:{slot}[{i}]",
            )


def validate_all_kits():
    """Sanity-check every registered appearance kit once at boot."""
    for kit_id, catalog in _hooks.appearance_kits().items():
        _validate_kit(kit_id, catalog)


def default_appearance():
    """Return a fresh appearance dict with every slot unset (None).

    Used by Character.__init__ and by persistence load merges so old saves
    that lack the key still get a complete slot map. Extended mortal slots
    are included so new keys merge cleanly without forcing legacy fills.
    """
    return {slot: None for slot in SLOTS}


def kit_for_character(character):
    """Appearance kit id for chargen / display (mortal or game-registered kit).

    Prefers an explicit ``appearance_kit`` stamp; otherwise consults the
    registered kit resolver hook (SUPERS infers Cosmic Elemental Aspect).
    Everyone else uses the mortal catalog.
    """
    if character is None:
        return "mortal"
    stamped = getattr(character, "appearance_kit", None)
    kits = _hooks.appearance_kits()
    if stamped and stamped in kits:
        return stamped
    resolver = _hooks.kit_for_character_resolver()
    if resolver is not None:
        inferred = resolver(character)
        if inferred and inferred in kits:
            return inferred
    return "mortal"


def _short_noun_for(kit=None):
    """Room-face noun for anonymous short-descs (figure / fire-being / …)."""
    kit_id = kit or "mortal"
    nouns = _hooks.appearance_kit_short_nouns()
    return nouns.get(kit_id, "figure")


def _non_skin_kit(kit=None):
    """True when the kit uses matter wording instead of mortal skin."""
    kit_id = kit or "mortal"
    return kit_id in _hooks.appearance_kit_short_nouns()


def catalog_for(kit_id=None):
    """Return the slot→entries dict for ``kit_id`` (default mortal)."""
    kits = _hooks.appearance_kits()
    if kit_id and kit_id in kits:
        return kits[kit_id]
    return kits["mortal"]


def valid_ids(slot, kit=None):
    """Return the set of valid option ids for `slot` in ``kit``."""
    entries = catalog_for(kit).get(slot)
    if not entries:
        return set()
    return {e["id"] for e in entries}


def normalize_custom(text):
    """Clean a freeform slot value typed by the player.

    Returns the cleaned string, or None if empty / too long after strip.
    Keeps spaces and light punctuation so "storm grey" and "blue-green"
    stay readable in the auto-built look sentence.
    """
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if len(cleaned) > CUSTOM_MAX_LEN:
        return None
    return cleaned


def display(slot, option_id, *, kit=None):
    """Return the player-facing display name for a slot value.

    Catalog ids resolve to their `name` field (kit first, then any kit).
    Custom freeform values are returned as-is. Returns None only when
    `option_id` is empty/None.
    """
    if not option_id:
        return None
    # Prefer the active kit so Aspect ids resolve to Aspect names.
    for entry in catalog_for(kit).get(slot, []):
        if entry["id"] == option_id:
            return entry["name"]
    # Search other kits (legacy / mentor stamps / mixed saves).
    kits = _hooks.appearance_kits()
    for other_id, catalog in kits.items():
        if other_id == (kit or "mortal"):
            continue
        for entry in catalog.get(slot, []):
            if entry["id"] == option_id:
                return entry["name"]
    # Custom value -- already player-facing text.
    return str(option_id)


def list_options(slot, kit=None):
    """Return the list of {id, name} dicts for `slot` in ``kit``.

    Callers iterate this for chargen prompts and help text -- the catalog
    order in JSON is the presentation order.
    """
    return list(catalog_for(kit).get(slot, []))


def person_word_for(pronoun, kit=None):
    """Noun used in auto-built look prose (man/woman/person or *-being)."""
    kit_id = kit or "mortal"
    kit_map = _hooks.appearance_kit_person_words().get(kit_id) or {}
    if pronoun in kit_map:
        return kit_map[pronoun]
    return _PERSON_WORD.get(pronoun, "person")


def _no_crown_bits(hair_style, style_shown):
    """Return (short_bit, full_bit) when style means no hair/crown, else None."""
    styles = _hooks.appearance_no_crown_styles()
    if hair_style in styles:
        return styles[hair_style]
    if style_shown and str(style_shown).lower() == "bald":
        bald = styles.get("bald")
        if bald:
            return bald
    return None


def a_or_an(word):
    """Pick 'a' or 'an' from the first letter of *word* (simple English)."""
    w = (word or "").strip().lower()
    if not w:
        return "a"
    if w[0] in "aeiou":
        return "an"
    return "a"


def with_article(rest, *, capitalize=False):
    """Prefix *rest* with a/an based on its first word."""
    cleaned = " ".join(str(rest or "").split())
    if not cleaned:
        article = "a"
        return article.capitalize() if capitalize else article
    first = cleaned.split(None, 1)[0]
    article = a_or_an(first)
    if capitalize:
        article = article.capitalize()
    return f"{article} {cleaned}"


def _skin_short_bit(skin, kit=None):
    """Skin (or non-skin matter) fragment for anonymous short-descs."""
    if not skin:
        return None
    skin_l = str(skin).lower()
    if _non_skin_kit(kit):
        return skin_l
    if skin_l.endswith("skinned"):
        return skin_l
    return f"{skin_l}-skinned"


def _extended_phrase(slot, appearance, *, kit=None):
    """Optional prose fragment for extended slots (mortal kit only)."""
    value = appearance.get(slot)
    if not value or value in ("none",):
        return None
    shown = display(slot, value, kit=kit)
    if not shown:
        return None
    if slot == "facial_hair":
        return f"{shown.lower()} facial hair"
    if slot == "scars":
        return shown.lower()
    if slot == "voice":
        return f"a {shown.lower()} voice"
    return shown.lower()


def is_complete(appearance):
    """True when every core appearance slot has a non-None value."""
    if not appearance:
        return False
    return all(appearance.get(slot) is not None for slot in CORE_SLOTS)


def normalize_short_desc(text):
    """Clean a player-typed room-face short-desc (setshort)."""
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if len(cleaned) > SHORT_DESC_MAX_LEN:
        return None
    return cleaned


def build_short_desc(appearance, pronoun="they", *, kit=None):
    """Anonymous short face from filled look slots (until setshort)."""
    appearance = appearance or {}
    height = display("height", appearance.get("height"), kit=kit)
    physique = display("physique", appearance.get("physique"), kit=kit)
    skin = display("skin_tone", appearance.get("skin_tone"), kit=kit)
    hair_color = display("hair_color", appearance.get("hair_color"), kit=kit)
    hair_style = appearance.get("hair_style")
    style_shown = (
        display("hair_style", hair_style, kit=kit) if hair_style else ""
    )
    eyes = display("eye_color", appearance.get("eye_color"), kit=kit)
    no_crown = _no_crown_bits(hair_style, style_shown)
    bits = []
    if height:
        bits.append(str(height).lower())
    if physique:
        bits.append(str(physique).lower())
    skin_bit = _skin_short_bit(skin, kit=kit)
    if skin_bit:
        bits.append(skin_bit)
    if no_crown:
        bits.append(no_crown[0])
    elif hair_color:
        bits.append(f"{str(hair_color).lower()}-haired")
    elif hair_style and style_shown:
        bits.append(str(style_shown).lower())
    noun = _short_noun_for(kit)
    if bits or eyes:
        if not bits:
            return with_article(
                f"{noun} with {str(eyes).lower()} eyes"
            )
        face = with_article(f"{' '.join(bits)} {noun}")
        if eyes:
            face = f"{face} with {str(eyes).lower()} eyes"
        return face
    return with_article(person_word_for(pronoun, kit=kit))


def build_description(appearance, pronoun, age=None, *, kit=None):
    """Build one look/examine sentence from structured slots + pronoun."""
    if not is_complete(appearance):
        return None
    person = person_word_for(pronoun, kit=kit)
    height = display("height", appearance["height"], kit=kit).lower()
    physique = display("physique", appearance["physique"], kit=kit).lower()
    skin = display("skin_tone", appearance["skin_tone"], kit=kit).lower()
    eyes = display("eye_color", appearance["eye_color"], kit=kit).lower()
    hair_style = appearance["hair_style"]
    style_shown = display("hair_style", hair_style, kit=kit)
    age_bit = ""
    if age is not None:
        phrase_fn = _hooks.appearance_age_phrase_fn()
        if phrase_fn is not None:
            try:
                phrase = phrase_fn(int(age))
                if phrase:
                    age_bit = f" {phrase}"
            except Exception:
                age_bit = ""
    matter = "skin"
    crown = "hair"
    if _non_skin_kit(kit):
        matter = "matter"
        crown = "crown"
    lead = with_article(height, capitalize=True)
    no_crown = _no_crown_bits(hair_style, style_shown)
    extended = []
    for slot in EXTENDED_SLOTS:
        phrase = _extended_phrase(slot, appearance, kit=kit)
        if phrase:
            extended.append(phrase)
    ext_suffix = ""
    if extended:
        ext_suffix = f"; {'; '.join(extended)}"

    if no_crown:
        return (
            f"{lead}, {physique} {person}{age_bit} with {skin} {matter}, "
            f"{no_crown[1]}, and {eyes} eyes{ext_suffix}."
        )
    hair_color = display(
        "hair_color", appearance["hair_color"], kit=kit
    ).lower()
    style = style_shown.lower()
    return (
        f"{lead}, {physique} {person}{age_bit} with {skin} {matter}, "
        f"{hair_color} {style} {crown}, and {eyes} eyes{ext_suffix}."
    )


def apply_appearance(character):
    """If ``character.appearance`` is complete, rewrite ``character.description``.

    No-op when incomplete or when ``character.desc_override`` is True.
    """
    if getattr(character, "desc_override", False):
        return
    age = getattr(character, "age", None)
    kit = kit_for_character(character)
    text = build_description(
        character.appearance, character.pronoun, age=age, kit=kit
    )
    if text is not None:
        character.description = text
