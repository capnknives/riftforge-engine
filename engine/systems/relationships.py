"""relationships.py -- the engine's generic directed-tag relationship core.

One-sided social tags between characters live on ``Character.relationships``
as ``{other_key: kind}``. One kind per target (a new tag replaces). Family
is a help category; settable family kinds are sibling and parent (mentor).

Asymmetry is flavor, not a gate. Games layer favorite-person resolution,
hunt/rest buddy pickers, and enemy-tier pursue on top of these primitives
the same way SUPERS keeps franchise cast bonds, easter-egg prose pools, and
Cadence-specific partner rules in ``supers/relationships.py``
(docs/plans/two_repo_purity.md, Phase 7 H7d).

Pure attribute access + game.find_character when resolving favorites: zero
``supers`` imports.
"""

from __future__ import annotations

# Kind ids players may set. Extensible -- keep COMMANDS / help in sync.
# Favorite auto-pick walks FAVORITE_PRIORITY (greatest first).
KINDS = (
    "lover",
    "sibling",
    "parent",
    "best_friend",
    "ashkin",
    "friend",
    "nuisance",
    "rival",
    "enemy",
    "oppressor",
    "nemesis",
    "mortal_enemy",
)
# Greatest → least for resolve_favorite_person when favorite_person unset.
FAVORITE_PRIORITY = KINDS
# Hunt / rest / beckon close ties (never Enemy-tier).
CLOSE_KINDS = frozenset({
    "lover", "sibling", "parent", "best_friend", "ashkin", "friend",
})
# Cadence lethal pursue (rival is competitive only -- not in this set).
ENEMY_TIER = frozenset({
    "enemy", "oppressor", "nemesis", "mortal_enemy",
})
# Help category only -- not a settable kind id.
FAMILY_KINDS = frozenset({"sibling", "parent"})


def ensure_defaults(character):
    """Guarantee relationships dict + favorite_person field exist."""
    if not hasattr(character, "relationships") or character.relationships is None:
        character.relationships = {}
    elif not isinstance(character.relationships, dict):
        character.relationships = {}
    if not hasattr(character, "favorite_person"):
        character.favorite_person = None


def normalize_kind(value):
    """Return a valid kind id, or None if unknown.

    Legacy ``brother`` maps to ``sibling``. ``enemy`` is its own lethal
    kind (no longer an alias for rival).
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    # Soft aliases players might type.
    aliases = {
        "love": "lover",
        "loves": "lover",
        "dating": "lover",
        "buddy": "friend",
        "friends": "friend",
        "bestfriend": "best_friend",
        "best_friends": "best_friend",
        "bf": "best_friend",
        "ashkin": "ashkin",
        "ashbound": "ashkin",
        "purgatory_kin": "ashkin",
        "purgatorykin": "ashkin",
        "brother": "sibling",
        "brothers": "sibling",
        "siblings": "sibling",
        "bro": "sibling",
        "sis": "sibling",
        "sister": "sibling",
        "sisters": "sibling",
        "mentor": "parent",
        "mom": "parent",
        "dad": "parent",
        "mother": "parent",
        "father": "parent",
        "enemies": "enemy",
        "tormentor": "oppressor",
        "oppressors": "oppressor",
        "nemeses": "nemesis",
        "mortalenemy": "mortal_enemy",
        "mortal_enemies": "mortal_enemy",
        "annoyance": "nuisance",
    }
    text = aliases.get(text, text)
    if text in KINDS:
        return text
    return None


def get_kind(character, other):
    """Return the kind character has tagged other with, or None.

    `other` may be a Character or a name key string. Migrates legacy
    ``brother`` stamps to ``sibling`` in place.
    """
    ensure_defaults(character)
    key = other if isinstance(other, str) else getattr(other, "key", None)
    if not key:
        return None
    raw = character.relationships.get(key)
    kind_id = normalize_kind(raw)
    if kind_id is None:
        return None
    # In-place migrate so old brother tags become sibling without a save.
    if raw != kind_id:
        character.relationships[key] = kind_id
    return kind_id


def set_kind(character, other, kind):
    """Set character's one-sided tag toward other. Returns the kind set."""
    ensure_defaults(character)
    kind_id = normalize_kind(kind)
    if kind_id is None:
        raise ValueError(f"unknown relationship kind: {kind!r}")
    key = other if isinstance(other, str) else other.key
    if key == character.key:
        raise ValueError("cannot relate to yourself")
    character.relationships[key] = kind_id
    return kind_id


def ensure_kind(character, other, kind):
    """Stamp `kind` only when character has no tag toward other yet.

    Immersion `ensure_*_bonds` helpers use this so a GM/`relate` edit
    survives reboot -- boot must fill missing canon edges, not overwrite
    rivals back to lovers every Game.__init__.
    """
    existing = get_kind(character, other)
    if existing is not None:
        return existing
    return set_kind(character, other, kind)


def clear(character, other):
    """Remove character's tag toward other. Returns True if something cleared."""
    ensure_defaults(character)
    key = other if isinstance(other, str) else getattr(other, "key", None)
    if not key:
        return False
    return character.relationships.pop(key, None) is not None


def list_of(character):
    """Return [(other_key, kind), ...] sorted by other_key."""
    ensure_defaults(character)
    items = []
    for k, v in list(character.relationships.items()):
        kind_id = normalize_kind(v)
        if kind_id is None:
            continue
        if v != kind_id:
            character.relationships[k] = kind_id
        items.append((k, kind_id))
    items.sort(key=lambda pair: pair[0].lower())
    return items


def list_of_kind(character, kind):
    """Return other_keys tagged with `kind`."""
    kind_id = normalize_kind(kind)
    if kind_id is None:
        return []
    return [k for k, v in list_of(character) if v == kind_id]


def reciprocal_kind(character, other):
    """What `other` has tagged `character` as (or None)."""
    if other is None:
        return None
    return get_kind(other, character)


def asymmetry(character, other):
    """Classify the tag pair for flavor pickers.

    Returns a short id string used as a key into FLAVOR['asymmetry'],
    or None when there is no tag either way.
    """
    a = get_kind(character, other)
    b = reciprocal_kind(character, other)
    if a is None and b is None:
        return None
    if a == b == "lover":
        return "mutual_lover"
    if a == b == "friend":
        return "mutual_friend"
    if a == b == "best_friend":
        return "mutual_friend"
    if a == b == "ashkin":
        return "mutual_ashkin"
    if a == b == "rival":
        return "mutual_rival"
    if a == b == "sibling":
        return "mutual_brother"
    if a == b == "parent":
        return "mutual_friend"
    if a == "lover" and b != "lover":
        return "unrequited_lover"
    if b == "lover" and a != "lover":
        return "unrequited_lover_target"
    if a in ("friend", "best_friend") and b == "rival":
        return "crossed_friend_rival"
    if a == "rival" and b in ("friend", "best_friend"):
        return "crossed_rival_friend"
    if a == "sibling" and b is None:
        return "one_way_brother"
    if a is None and b == "sibling":
        return "one_way_brother"
    if a == "ashkin" and b is None:
        return "one_way_ashkin"
    if a is None and b == "ashkin":
        return "one_way_ashkin"
    if a in ("friend", "best_friend") and b is None:
        return "one_way_friend"
    if a == "rival" and b is None:
        return "one_way_rival"
    if a is None and b in ("friend", "best_friend"):
        return "one_way_friend"
    if a is None and b == "rival":
        return "one_way_rival"
    if a == "lover" or b == "lover":
        return "unrequited_lover" if a == "lover" else "unrequited_lover_target"
    if a == "sibling" or b == "sibling":
        return "one_way_brother"
    if a == "ashkin" or b == "ashkin":
        return "one_way_ashkin"
    if a in ("friend", "best_friend") or b in ("friend", "best_friend"):
        return "one_way_friend"
    if a in ENEMY_TIER or b in ENEMY_TIER:
        return "one_way_rival"
    return "one_way_rival"


def _zone_of(character):
    """Room.zone of character's location, or None."""
    loc = getattr(character, "location", None)
    if loc is None:
        return None
    return getattr(loc, "zone", None)


def _same_zone(a, b):
    """True when both have locations in the same non-None zone."""
    za, zb = _zone_of(a), _zone_of(b)
    return za is not None and za == zb


def pick_nearest(actor, candidates):
    """Prefer co-located, then same-zone; else first candidate. Or None."""
    living = [
        c for c in candidates
        if c is not None
        and c is not actor
        and getattr(c, "hp", 0) > 0
        and not getattr(c, "spirit", False)
    ]
    if not living:
        return None
    here = getattr(actor, "location", None)
    colocated = [
        c for c in living
        if getattr(c, "location", None) is here
    ]
    if colocated:
        return colocated[0]
    same = [c for c in living if _same_zone(actor, c)]
    if same:
        return same[0]
    return living[0]


def are_siblings(a, b):
    """True when both have tagged each other as sibling (mutual)."""
    if a is None or b is None or a is b:
        return False
    return get_kind(a, b) == "sibling" and get_kind(b, a) == "sibling"


def are_brothers(a, b):
    """Alias for are_siblings (legacy call sites / smoke)."""
    return are_siblings(a, b)


def is_enemy_tier(kind):
    """True when `kind` is a lethal Enemy-tier relationship id."""
    return normalize_kind(kind) in ENEMY_TIER


def is_close_kind(kind):
    """True when `kind` is a hunt/rest/beckon close-tie id."""
    return normalize_kind(kind) in CLOSE_KINDS


def get_favorite_person_key(character):
    """Return explicit favorite_person key if set, else None."""
    ensure_defaults(character)
    key = getattr(character, "favorite_person", None)
    if key is None:
        return None
    text = str(key).strip()
    return text or None


def set_favorite_person(character, other):
    """Set explicit favorite_person to other's key. Returns the key.

    Pass None / 'clear' / '' to clear the override (Cadence falls back to
    ladder auto-resolve).
    """
    ensure_defaults(character)
    if other is None:
        character.favorite_person = None
        return None
    if isinstance(other, str):
        text = other.strip()
        if not text or text.lower() in ("clear", "none", "off"):
            character.favorite_person = None
            return None
        if text == character.key:
            raise ValueError("cannot set yourself as favorite person")
        character.favorite_person = text
        return text
    key = getattr(other, "key", None)
    if not key:
        raise ValueError("favorite person needs a character key")
    if key == character.key:
        raise ValueError("cannot set yourself as favorite person")
    character.favorite_person = key
    return key


def resolve_favorite_person(character, game):
    """Return the Character this actor favors, or None.

    Explicit ``favorite_person`` wins when that body still exists and is
    living. Otherwise scan tags by FAVORITE_PRIORITY (lover first … mortal
    enemy last); among the same kind prefer co-located, then same-zone.
    """
    ensure_defaults(character)
    if game is None:
        return None
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return None

    def _living(ch):
        if ch is None or ch is character:
            return False
        if int(getattr(ch, "hp", 0) or 0) <= 0:
            return False
        if getattr(ch, "spirit", False):
            return False
        return True

    explicit = get_favorite_person_key(character)
    if explicit:
        body = finder(explicit)
        if _living(body):
            return body
        # Stale override -- fall through to ladder (do not clear; player set it).

    # Collect living tagged targets by kind.
    by_kind = {k: [] for k in FAVORITE_PRIORITY}
    for other_key, kind in list_of(character):
        if kind not in by_kind:
            continue
        other = finder(other_key)
        if _living(other):
            by_kind[kind].append(other)

    for kind in FAVORITE_PRIORITY:
        pool = by_kind.get(kind) or []
        picked = pick_nearest(character, pool)
        if picked is not None:
            return picked
    return None


def enemy_tier_targets(actor, game):
    """Living characters actor has tagged Enemy-tier (may be empty)."""
    ensure_defaults(actor)
    if game is None:
        return []
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return []
    out = []
    for other_key, kind in list_of(actor):
        if kind not in ENEMY_TIER:
            continue
        other = finder(other_key)
        if other is None or other is actor:
            continue
        if int(getattr(other, "hp", 0) or 0) <= 0:
            continue
        if getattr(other, "spirit", False):
            continue
        out.append(other)
    return out
