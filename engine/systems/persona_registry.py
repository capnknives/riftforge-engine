"""
persona_registry.py -- generic personality-trait catalog + need multipliers.

Trait tables load from a JSON file whose path the game registers via
``engine.hooks.set_persona_content_path``. Conflict pairs and traveler
reach maps register at boot the same way (SUPERS supplies show-specific
data in ``supers/bootstrap.py``).

SUPERS keeps flavor prose, chargen blurbs, and ``personas.json`` content;
this module owns validation, persistence, trait math, and character trait
mutators with no ``import supers``.
"""

from __future__ import annotations

from engine import content_store as store
from engine import content_validate as cv
from engine import hooks

_KNOWN_MOD_KEYS = frozenset({
    # Survival / lifestyle meters (generic decay_mult(npc, need) consumers).
    "hunger_mult", "thirst_mult", "energy_mult",
    "social_mult", "entertainment_mult", "hygiene_mult", "gym_mult",
    # Slow homesick drip (settled ~1 game-month to SEEK at celestial rates).
    "homesickness_mult",
    # Duty (pack / hunt / vocation) -- tick_duty gain branch.
    "duty_mult",
    # Fear accrual (needs.accrue_fear gain branch only).
    "fear_mult",
    # Spend reluctance / idle wander / ambient chatter / seek threshold.
    "thrift", "wander", "chatter", "seek_mult",
    # Time-of-day energy bands (needs.tick energy).
    "morning_energy_mult", "night_energy_mult",
    # Cadence social lead/follow: summed across traits (higher leads).
    "assertiveness",
})

_DATA = None
TRAITS = {}
_LINES = {}

_TRAIT_CONFLICTS: dict[str, frozenset] = {}
_TRAVELER_LEVEL_FOR_TAG: dict[str, int] = {}
_TRAVELER_KIND_LEVEL: dict[str, int] = {}
_wander_overlay = None


def _build_conflict_map(pairs):
    """Expand symmetric pairs into tag -> frozenset of conflicting tags."""
    mapping = {}
    for a, b in pairs:
        mapping.setdefault(a, set()).add(b)
        mapping.setdefault(b, set()).add(a)
    return {tag: frozenset(others) for tag, others in mapping.items()}


def register_trait_conflicts(pairs):
    """Register symmetric lifestyle trait conflict pairs (idempotent replace)."""
    global _TRAIT_CONFLICTS
    _TRAIT_CONFLICTS = _build_conflict_map(pairs)


def trait_conflict_map():
    """Return the current tag -> conflicting-tags map."""
    return _TRAIT_CONFLICTS


def register_traveler_levels(level_for_tag, kind_level):
    """Register traveler reach tag levels and kind aliases (idempotent)."""
    global _TRAVELER_LEVEL_FOR_TAG, _TRAVELER_KIND_LEVEL
    _TRAVELER_LEVEL_FOR_TAG = dict(level_for_tag)
    _TRAVELER_KIND_LEVEL = dict(kind_level)


def register_wander_overlay(fn):
    """Register fn(npc) -> float overlay for wander_mult (default 1.0)."""
    global _wander_overlay
    _wander_overlay = fn


def _load():
    """Read personas.json from the registered content path."""
    return store.load_json(hooks.persona_content_path())


def validate_trait_entry(tag, mods, *, where=None):
    """Fail loud if one personas.json traits{} row is malformed."""
    where = where or f"personas.json: trait '{tag}'"
    if not isinstance(tag, str) or not tag:
        raise AssertionError(f"{where}: trait tags must be non-empty strings")
    if not isinstance(mods, dict):
        raise AssertionError(f"{where}: trait must be a dict")
    for key, value in mods.items():
        cv.require_in(
            key, _KNOWN_MOD_KEYS, where,
            label="modifier",
        )
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AssertionError(f"{where}.{key} must be a number")


def validate_flavor_event(event, templates, *, where=None):
    """Fail loud if one personas.json lines[event] pool is malformed."""
    where = where or f"personas.json: lines['{event}']"
    if not isinstance(templates, list) or not templates:
        raise AssertionError(f"{where} must be a non-empty list")
    for i, line in enumerate(templates):
        if not isinstance(line, str) or not line.strip():
            raise AssertionError(
                f"{where}[{i}] must be a non-empty string"
            )


def validate_personas_file(data, *, where="personas.json"):
    """Fail loud if traits/lines envelope is missing or malformed."""
    cv.require_keys(data, ("traits", "lines"), where)
    traits = data["traits"]
    lines = data["lines"]
    if not isinstance(traits, dict) or not traits:
        raise AssertionError(f"{where}: 'traits' must be a non-empty dict")
    if not isinstance(lines, dict) or not lines:
        raise AssertionError(f"{where}: 'lines' must be a non-empty dict")
    for tag, mods in traits.items():
        validate_trait_entry(tag, mods, where=f"{where}: traits[{tag!r}]")
    for event, templates in lines.items():
        validate_flavor_event(
            event, templates, where=f"{where}: lines[{event!r}]",
        )


def _validate(data):
    """Fail loud if traits/lines are missing or malformed."""
    validate_personas_file(data)


def _ensure_loaded():
    """Load catalog on first use after bootstrap registers the content path."""
    global _DATA, TRAITS, _LINES
    if _DATA is not None:
        return
    reload()


def content_path():
    """Absolute path of personas.json."""
    return hooks.persona_content_path()


def _set_content_path_for_tests(path):
    """Point the catalog at a different JSON file (smoke tests only)."""
    hooks.set_persona_content_path(lambda: path)
    reload()


def known_traits():
    """Frozen set of valid trait tag ids (for NPC roster validation)."""
    _ensure_loaded()
    return frozenset(TRAITS)


def known_mod_keys():
    """Frozen set of allowed trait modifier keys."""
    return _KNOWN_MOD_KEYS


def all_traits():
    """Yield (tag, mods) sorted by tag."""
    _ensure_loaded()
    for tag in sorted(TRAITS):
        yield tag, TRAITS[tag]


def save():
    """Validate and atomically rewrite personas.json."""
    _ensure_loaded()
    _validate(_DATA)
    store.save_json(hooks.persona_content_path(), _DATA)


def reload():
    """Re-read personas.json into the live tables."""
    global _DATA
    _DATA = _load()
    _validate(_DATA)
    TRAITS.clear()
    TRAITS.update(_DATA["traits"])
    _LINES.clear()
    _LINES.update(_DATA["lines"])


def add_trait(tag, mods=None):
    """Create a trait tag (optional modifier dict) and persist."""
    _ensure_loaded()
    store.require_snake_id(tag, what="Trait tag")
    if tag in TRAITS:
        raise ValueError(f"Trait '{tag}' is already defined.")
    mods = dict(mods or {})
    for key, value in mods.items():
        if key not in _KNOWN_MOD_KEYS:
            raise ValueError(
                f"Unknown modifier {key!r}. Known: "
                + ", ".join(sorted(_KNOWN_MOD_KEYS))
            )
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"modifier {key!r} must be a number.")
    TRAITS[tag] = mods
    try:
        save()
    except Exception:
        TRAITS.pop(tag, None)
        raise
    return mods


def set_trait_mod(tag, mod_key, value):
    """Set one numeric modifier on a trait and persist."""
    _ensure_loaded()
    if tag not in TRAITS:
        raise ValueError(f"No trait '{tag}' is defined.")
    mod_key = (mod_key or "").strip()
    if mod_key not in _KNOWN_MOD_KEYS:
        raise ValueError(
            f"Unknown modifier {mod_key!r}. Known: "
            + ", ".join(sorted(_KNOWN_MOD_KEYS))
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("modifier value must be a number.") from exc
    previous = dict(TRAITS[tag])
    TRAITS[tag][mod_key] = number
    try:
        save()
    except Exception:
        TRAITS[tag] = previous
        raise
    return TRAITS[tag]


def clear_trait_mod(tag, mod_key):
    """Remove one modifier key from a trait and persist."""
    _ensure_loaded()
    if tag not in TRAITS:
        raise ValueError(f"No trait '{tag}' is defined.")
    mod_key = (mod_key or "").strip()
    if mod_key not in TRAITS[tag]:
        raise ValueError(f"Trait '{tag}' has no modifier {mod_key!r}.")
    previous = dict(TRAITS[tag])
    del TRAITS[tag][mod_key]
    try:
        save()
    except Exception:
        TRAITS[tag] = previous
        raise
    return TRAITS[tag]


def remove_trait(tag):
    """Delete a trait tag and persist (refuses if last trait)."""
    _ensure_loaded()
    if tag not in TRAITS:
        raise ValueError(f"No trait '{tag}' is defined.")
    if len(TRAITS) <= 1:
        raise ValueError(
            "Cannot remove the last trait -- the table must stay non-empty."
        )
    removed = TRAITS.pop(tag)
    try:
        save()
    except Exception:
        TRAITS[tag] = removed
        raise
    return removed


def add_line(event, template):
    """Append a flavor template line for an event and persist."""
    _ensure_loaded()
    event = (event or "").strip()
    template = (template or "").strip()
    if not event or not template:
        raise ValueError("event and template text are required.")
    previous = None
    if event in _LINES:
        previous = list(_LINES[event])
        _LINES[event] = previous + [template]
    else:
        _LINES[event] = [template]
    try:
        save()
    except Exception:
        if previous is None:
            _LINES.pop(event, None)
        else:
            _LINES[event] = previous
        raise
    return _LINES[event]


def _mods(npc):
    """Yield each known trait-modifier dict this NPC carries."""
    _ensure_loaded()
    for tag in getattr(npc, "traits", ()):
        mods = TRAITS.get(tag)
        if mods is not None:
            yield mods


def decay_mult(npc, need):
    """Combined decay-rate multiplier for one need across traits."""
    key = need + "_mult"
    mult = 1.0
    for mods in _mods(npc):
        mult *= mods.get(key, 1.0)
    return mult


def _clamped_product(npc, key, lo, hi):
    """Product of ``key`` across carried traits, clamped to [lo, hi]."""
    mult = 1.0
    for mods in _mods(npc):
        mult *= float(mods.get(key, 1.0) or 1.0)
    return max(lo, min(hi, mult))


def thrift(npc):
    """Spend-reluctance multiplier (product across traits), clamped [0.2, 3.0]."""
    return _clamped_product(npc, "thrift", 0.2, 3.0)


def wander_mult(npc):
    """Idle-wander probability multiplier, clamped [0.05, 3.0]."""
    base = _clamped_product(npc, "wander", 0.05, 3.0)
    overlay = 1.0
    if _wander_overlay is not None:
        try:
            overlay = float(_wander_overlay(npc))
        except Exception:
            overlay = 1.0
    return max(0.05, min(3.0, base * overlay))


def chatter_mult(npc):
    """Ambient talk.py chatter chance multiplier, clamped [0.2, 3.0]."""
    return _clamped_product(npc, "chatter", 0.2, 3.0)


def seek_mult(npc):
    """Need-seek threshold multiplier, clamped [0.7, 1.3]."""
    return _clamped_product(npc, "seek_mult", 0.7, 1.3)


def assertiveness(npc):
    """Sum of trait ``assertiveness`` mods (Cadence social lead/follow)."""
    total = 0.0
    for mods in _mods(npc):
        total += float(mods.get("assertiveness", 0.0) or 0.0)
    return total


def has(npc, tag):
    """Convenience: does this NPC carry a given trait tag?"""
    return tag in getattr(npc, "traits", ())


def traits_conflict(a, b):
    """True when lifestyle tags ``a`` and ``b`` are mutually exclusive."""
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b or a == b:
        return False
    return b in _TRAIT_CONFLICTS.get(a, ())


def conflicting_held_trait(character, tag):
    """Return a held trait that conflicts with ``tag``, or None."""
    tag = (tag or "").strip().lower()
    blockers = _TRAIT_CONFLICTS.get(tag)
    if not blockers:
        return None
    for held in getattr(character, "traits", ()) or ():
        if held in blockers:
            return held
    return None


def traveler_level(character):
    """Highest traveler reach on the character (0 = homebound)."""
    tags = getattr(character, "traits", None) or ()
    level = 0
    for tag in tags:
        level = max(level, _TRAVELER_LEVEL_FOR_TAG.get(tag, 0))
    return level


def can_travel(character, kind):
    """True when traveler reach is high enough for town|map|realm."""
    need = _TRAVELER_KIND_LEVEL.get((kind or "").lower())
    if need is None:
        return False
    return traveler_level(character) >= need


def set_traveler(character, kind):
    """Set traveler reach to town|map|realm, or none/0 to clear."""
    traits = list(getattr(character, "traits", None) or [])
    traits = [t for t in traits if t not in _TRAVELER_LEVEL_FOR_TAG]
    kind = (kind or "").strip().lower()
    if kind in ("none", "0", "off", "clear", ""):
        character.traits = traits
        return 0
    tag = {
        "town": "traveler_town",
        "1": "traveler_town",
        "map": "traveler_map",
        "2": "traveler_map",
        "realm": "traveler_realm",
        "3": "traveler_realm",
        "planar": "traveler_realm",
    }.get(kind)
    if tag is None:
        raise ValueError(
            "traveler level must be town, map, realm, or none."
        )
    traits.append(tag)
    character.traits = traits
    return traveler_level(character)


def try_add_character_trait(character, tag):
    """Add a catalog trait when safe; return True if held afterward."""
    _ensure_loaded()
    tag = (tag or "").strip().lower()
    if not tag or tag not in TRAITS:
        return False
    if tag in _TRAVELER_LEVEL_FOR_TAG:
        return False
    if has(character, tag):
        return True
    if conflicting_held_trait(character, tag) is not None:
        return False
    try:
        add_character_trait(character, tag)
    except ValueError:
        return False
    return has(character, tag)


def add_character_trait(character, tag):
    """Add a catalog trait to the character (idempotent). Raises on unknown."""
    _ensure_loaded()
    tag = (tag or "").strip().lower()
    if tag not in TRAITS:
        raise ValueError(
            f"Unknown trait {tag!r}. Available: "
            + ", ".join(sorted(TRAITS))
        )
    if tag in _TRAVELER_LEVEL_FOR_TAG:
        return set_traveler(
            character,
            {1: "town", 2: "map", 3: "realm"}[_TRAVELER_LEVEL_FOR_TAG[tag]],
        )
    held_block = conflicting_held_trait(character, tag)
    if held_block is not None:
        raise ValueError(
            f"Trait {tag!r} conflicts with held trait {held_block!r}."
        )
    traits = list(getattr(character, "traits", None) or [])
    if tag not in traits:
        traits.append(tag)
    character.traits = traits
    return traits


def remove_character_trait(character, tag):
    """Remove a trait tag from the character. Raises if not present."""
    tag = (tag or "").strip().lower()
    traits = list(getattr(character, "traits", None) or [])
    if tag not in traits:
        raise ValueError(f"You don't have the trait {tag!r}.")
    traits.remove(tag)
    character.traits = traits
    return traits
