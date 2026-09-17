"""
appearance.py -- generic structured appearance slots + description builder.

Catalog data registers at boot via ``register_appearance_kit``. Game-specific
kit resolution (Cosmic Elemental aspects, etc.) uses
``set_appearance_kit_resolver``.

Zero ``supers`` imports.
"""

from __future__ import annotations

CORE_SLOTS = (
    "hair_style",
    "hair_color",
    "eye_color",
    "height",
    "physique",
    "skin_tone",
)

EXTENDED_SLOTS = (
    "facial_hair",
    "scars",
    "voice",
)

SLOTS = CORE_SLOTS + EXTENDED_SLOTS
CUSTOM_MAX_LEN = 40
SHORT_DESC_MAX_LEN = 60
PRONOUNS = ("he", "she", "they", "it")

# Possessive / object spellings players may type at the pronoun verb.
PRONOUN_ALIASES = {
    "its": "it",
}

_PERSON_WORD = {
    "he": "man",
    "she": "woman",
    "they": "person",
    "it": "figure",
}

_APPEARANCE_KITS: dict = {}
_KIT_PERSON_WORD: dict = {}
_KIT_SHORT_NOUN: dict = {}
_NO_CROWN_STYLES: dict = {
    "bald": ("bald", "a bald head"),
    "heat_shimmer": ("heat-shimmered", "a heat-shimmered outline"),
    "still_surface": ("glass-still", "a still glassy surface"),
    "empty_sky": ("sky-empty", "an empty sky outline"),
    "bare_stone": ("bare-stone", "a bare stone crown"),
}

_kit_resolver = None
_age_phrase_fn = None


def set_appearance_kit_resolver(fn):
    """Register fn(character) -> kit id string (default ``mortal``)."""
    global _kit_resolver
    _kit_resolver = fn


def set_appearance_age_phrase(fn):
    """Register fn(age:int) -> optional decade phrase for look prose."""
    global _age_phrase_fn
    _age_phrase_fn = fn


def resolve_apparent_age(character, viewer=None):
    """Return the apparent age (in years) to show for ``character``.

    Today this is a thin pass-through -- it just returns the character's
    real stored ``age`` attribute, the same value every past call site read
    directly. The function exists as a **documented extension seam**: a
    future age-band-concealment feature (a cosmetic "makeup" appearance
    slot, a Hexcraft glamour spell that locks a perceived age band, or a
    mirror/reflection tell that pierces a disguise) can later make the
    apparent age depend on who is looking, without having to hunt down and
    change every place in the codebase that currently calls
    ``getattr(character, "age", None)``.

    ``viewer`` is accepted but intentionally unused for now -- it is not
    dead code by accident, it is reserved for that future branch (e.g.
    "does ``viewer`` have True Sight, or did they cast Discern on
    ``character``?"). See ``docs/plans/age_band_concealment.md`` (parked --
    not implemented) for the design pointer.
    """
    return getattr(character, "age", None)


def register_appearance_kit(
    kit_id,
    catalog,
    *,
    person_words=None,
    short_noun=None,
    no_crown_styles=None,
):
    """Register one appearance kit catalog at boot."""
    _APPEARANCE_KITS[kit_id] = catalog
    if person_words:
        _KIT_PERSON_WORD[kit_id] = dict(person_words)
    if short_noun:
        _KIT_SHORT_NOUN[kit_id] = short_noun
    if no_crown_styles:
        _NO_CROWN_STYLES.update(no_crown_styles)


def appearance_kits():
    """Registered kit id -> catalog map (read-only view)."""
    return dict(_APPEARANCE_KITS)


def validate_all_kits():
    """Fail loud when a registered kit catalog is missing slot tables."""
    for kit_id, catalog in _APPEARANCE_KITS.items():
        if not isinstance(catalog, dict):
            raise ValueError(f"appearance kit {kit_id!r} must be a dict catalog")
        for slot in slots_for_kit(kit_id):
            if slot not in catalog:
                raise ValueError(
                    f"appearance kit {kit_id!r} missing catalog for slot {slot!r}"
                )


def slots_for_kit(kit_id=None):
    """Ordered slot list for chargen / appearance edits in ``kit_id``."""
    if kit_id and kit_id != "mortal":
        return CORE_SLOTS
    return SLOTS


def default_appearance():
    """Return a fresh appearance dict with every slot unset (None)."""
    return {slot: None for slot in SLOTS}


def kit_for_character(character):
    """Appearance kit id for chargen / display (default ``mortal``)."""
    if _kit_resolver is not None:
        try:
            kit = _kit_resolver(character)
            if kit and kit in _APPEARANCE_KITS:
                return kit
        except Exception:
            pass
    return "mortal"


def short_noun_for(kit=None):
    """Room-face noun for anonymous short-descs."""
    return _KIT_SHORT_NOUN.get(kit or "mortal", "figure")


def catalog_for(kit_id=None):
    """Return the slot->entries dict for ``kit_id`` (default mortal)."""
    if kit_id and kit_id in _APPEARANCE_KITS:
        return _APPEARANCE_KITS[kit_id]
    return _APPEARANCE_KITS.get("mortal", {})


def valid_ids(slot, kit=None):
    """Return the set of valid option ids for `slot` in ``kit``."""
    entries = catalog_for(kit).get(slot)
    if not entries:
        return set()
    return {e["id"] for e in entries}


def normalize_custom(text):
    """Clean a freeform slot value typed by the player."""
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if len(cleaned) > CUSTOM_MAX_LEN:
        return None
    return cleaned


def display(slot, option_id, *, kit=None):
    """Return the player-facing display name for a slot value."""
    if not option_id:
        return None
    for entry in catalog_for(kit).get(slot, []):
        if entry["id"] == option_id:
            return entry["name"]
    for other_id, catalog in _APPEARANCE_KITS.items():
        if other_id == (kit or "mortal"):
            continue
        for entry in catalog.get(slot, []):
            if entry["id"] == option_id:
                return entry["name"]
    return str(option_id)


def list_options(slot, kit=None):
    """Return the list of {id, name} dicts for `slot` in ``kit``."""
    return list(catalog_for(kit).get(slot, []))


def normalize_pronoun(raw):
    """Map a typed pronoun (he / she / they / it, or its) onto PRONOUNS.

    Returns the canonical id, or None when the token is unknown.
    """
    text = str(raw or "").strip().lower()
    text = PRONOUN_ALIASES.get(text, text)
    if text in PRONOUNS:
        return text
    return None


def person_word_for(pronoun, kit=None):
    """Noun used in auto-built look prose."""
    kit_map = _KIT_PERSON_WORD.get(kit or "mortal") or {}
    if pronoun in kit_map:
        return kit_map[pronoun]
    return _PERSON_WORD.get(pronoun, "person")


def _no_crown_bits(hair_style, style_shown):
    """Return (short_bit, full_bit) when style means no hair/crown, else None."""
    if hair_style in _NO_CROWN_STYLES:
        return _NO_CROWN_STYLES[hair_style]
    if style_shown and str(style_shown).lower() == "bald":
        return _NO_CROWN_STYLES["bald"]
    return None


def a_or_an(word):
    """Pick 'a' or 'an' from the first letter of *word*."""
    w = (word or "").strip().lower()
    if not w:
        return "a"
    if w[0] in "aeiou":
        return "an"
    return "a"


def with_article(rest, *, capitalize=False):
    """Prefix *rest* with a/an based on its first word.

    Catalog keys and combat weapon lines often ship with a leading article
    already (``a steel guandao``, ``an angel blade``). Leave those alone so
    templates that wrap ``with {…}`` do not emit ``an a steel …`` doubles.
    """
    cleaned = " ".join(str(rest or "").split())
    if not cleaned:
        article = "a"
        return article.capitalize() if capitalize else article
    low = cleaned.lower()
    for prefix in ("a ", "an ", "the "):
        if low.startswith(prefix):
            if capitalize:
                return cleaned[0].upper() + cleaned[1:]
            return cleaned
    first = cleaned.split(None, 1)[0]
    article = a_or_an(first)
    if capitalize:
        article = article.capitalize()
    return f"{article} {cleaned}"


def _skin_short_bit(skin, kit=None):
    """Skin (or elemental matter) fragment for anonymous short-descs."""
    if not skin:
        return None
    skin_l = str(skin).lower()
    if (kit or "").startswith("elemental_"):
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


def say_voice_phrase(appearance, *, kit=None):
    """Third-person ``say`` flavor from the voice appearance slot (not mechanical).

    Returns a phrase like ``in a gravelly voice`` for room listeners, or ``None``
    when the slot is unset, ``none``, or the kit catalog has no voice row.
    """
    value = (appearance or {}).get("voice")
    if not value or value in ("none",):
        return None
    shown = display("voice", value, kit=kit)
    if not shown:
        return None
    return f"in a {shown.lower()} voice"


def is_complete(appearance):
    """True when every core appearance slot has a filled value."""
    if not appearance:
        return False
    # Empty strings persist after a partial chargen / copyover and must
    # not pass -- ``display()`` returns None for falsy ids, then
    # ``build_description`` would ``.lower()`` that None.
    return all(appearance.get(slot) not in (None, "") for slot in CORE_SLOTS)


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
    """Anonymous short face from filled look slots."""
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
    noun = short_noun_for(kit)
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


def build_hood_short_desc(appearance, pronoun="they", *, kit=None):
    """Hood-up face: height + physique + skin; no hair, no eyes."""
    appearance = appearance or {}
    height = display("height", appearance.get("height"), kit=kit)
    physique = display("physique", appearance.get("physique"), kit=kit)
    skin = display("skin_tone", appearance.get("skin_tone"), kit=kit)
    bits = []
    if height:
        bits.append(str(height).lower())
    if physique:
        bits.append(str(physique).lower())
    skin_bit = _skin_short_bit(skin, kit=kit)
    if skin_bit:
        bits.append(skin_bit)
    if not bits:
        return None
    noun = short_noun_for(kit)
    return with_article(f"{' '.join(bits)} {noun}")


def build_mask_short_desc(appearance, pronoun="they", *, kit=None):
    """Mask-on face: height + hair + eyes visible through the mask."""
    appearance = appearance or {}
    height = display("height", appearance.get("height"), kit=kit)
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
    if no_crown:
        bits.append(no_crown[0])
    elif hair_color:
        bits.append(f"{str(hair_color).lower()}-haired")
    elif hair_style and style_shown:
        bits.append(str(style_shown).lower())
    if not bits and not eyes:
        return None
    if not bits:
        bits.append("masked")
    noun = short_noun_for(kit)
    face = with_article(f"{' '.join(bits)} {noun}")
    if eyes:
        face = f"{face} with {str(eyes).lower()} eyes"
    return face


def build_description(appearance, pronoun, age=None, *, kit=None):
    """Build one look/examine sentence from structured slots + pronoun."""
    if not is_complete(appearance):
        return None
    person = person_word_for(pronoun, kit=kit)
    height = (display("height", appearance["height"], kit=kit) or "").lower()
    physique = (display("physique", appearance["physique"], kit=kit) or "").lower()
    skin = (display("skin_tone", appearance["skin_tone"], kit=kit) or "").lower()
    eyes = (display("eye_color", appearance["eye_color"], kit=kit) or "").lower()
    hair_style = appearance["hair_style"]
    style_shown = display("hair_style", hair_style, kit=kit)
    age_bit = ""
    if age is not None and _age_phrase_fn is not None:
        try:
            phrase = _age_phrase_fn(int(age))
            if phrase:
                age_bit = f" {phrase}"
        except Exception:
            age_bit = ""
    matter = "skin"
    crown = "hair"
    if (kit or "").startswith("elemental_"):
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
    hair_color = (
        display("hair_color", appearance["hair_color"], kit=kit) or ""
    ).lower()
    style = (style_shown or "").lower()
    return (
        f"{lead}, {physique} {person}{age_bit} with {skin} {matter}, "
        f"{hair_color} {style} {crown}, and {eyes} eyes{ext_suffix}."
    )


def apply_appearance(character):
    """Rewrite character.description from slots when complete and allowed."""
    if getattr(character, "desc_override", False):
        return
    age = resolve_apparent_age(character)
    kit = kit_for_character(character)
    text = build_description(
        character.appearance, character.pronoun, age=age, kit=kit
    )
    if text is not None:
        character.description = text
