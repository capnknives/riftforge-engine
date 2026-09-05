"""
relationships.py -- generic one-sided social tags between characters.

Storage is ``Character.relationships``: ``{other_key: kind}``. One kind per
target (a new tag replaces). Game-specific flavor pools, cast bond ensures,
and franchise egg prose register via hooks (wired in ``supers/bootstrap.py``).

Zero ``supers`` imports.
"""

from __future__ import annotations

# Kind ids players may set. Extensible -- keep COMMANDS / help in sync.
# Favorite auto-pick walks FAVORITE_PRIORITY (greatest first).
KINDS = (
    "spouse",
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
# Greatest -> least for resolve_favorite_person when favorite_person unset.
FAVORITE_PRIORITY = KINDS
# Hunt / rest / beckon close ties (never Enemy-tier).
CLOSE_KINDS = frozenset({
    "spouse", "lover", "sibling", "parent", "best_friend", "ashkin", "friend",
})
# Mutual ashkin side-by-side soak (incoming damage mult cut).
ASHKIN_SOAK = 0.05
# Help category only -- not a settable kind id.
FAMILY_KINDS = frozenset({"spouse", "sibling", "parent"})
# Cadence lethal pursue (rival is competitive only -- not in this set).
ENEMY_TIER = frozenset({
    "enemy", "oppressor", "nemesis", "mortal_enemy",
})

# ---------------------------------------------------------------------------
# Game hook slots
# ---------------------------------------------------------------------------

_wants_solo_hunt = None
_is_grumpy_for_partners = None
_social_lead_follow_grumpy_gate = None
_reluctant_hangout_follow = None


def set_relationship_wants_solo_hunt(fn):
    """Register fn(actor) -> bool; True skips hunt-partner pick."""
    global _wants_solo_hunt
    _wants_solo_hunt = fn


def set_relationship_is_grumpy_for_partners(fn):
    """Register fn(character) -> bool; grumpy actors skip lover hunt picks."""
    global _is_grumpy_for_partners
    _is_grumpy_for_partners = fn


def set_relationship_social_lead_follow_grumpy_gate(fn):
    """Register fn(follower) -> bool; False blocks a grumpy hangout follow."""
    global _social_lead_follow_grumpy_gate
    _social_lead_follow_grumpy_gate = fn


def set_relationship_reluctant_hangout_follow(fn):
    """Register fn(follower) -> True when Cadence should skip hangout follow.

    SUPERS uses this for grumpy / hermit / loner (critical-social exception).
    When unset, the old grumpy-only gate still applies.
    """
    global _reluctant_hangout_follow
    _reluctant_hangout_follow = fn


def actor_has_purgatory_scar(character) -> bool:
    """True when *character* carries the Purgatory veteran scar (flag or trait)."""
    if getattr(character, "purgatory_scarred", False):
        return True
    traits = getattr(character, "traits", None) or []
    return "purgatory_scarred" in traits


def ashkin_tag_blocked_message() -> str:
    """Player-facing reject when the tagger lacks purgatory_scarred."""
    return (
        "Ashkin names a Purgatory war-bond -- you need your own scar on that "
        "plane before you can tag someone that way. (See help ashkin.)"
    )


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
    aliases = {
        "love": "lover",
        "loves": "lover",
        "dating": "lover",
        "spouse": "spouse",
        "husband": "spouse",
        "wife": "spouse",
        "married": "spouse",
        "partner": "spouse",
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
    """Return the kind character has tagged other with, or None."""
    ensure_defaults(character)
    key = other if isinstance(other, str) else getattr(other, "key", None)
    if not key:
        return None
    raw = character.relationships.get(key)
    kind_id = normalize_kind(raw)
    if kind_id is None:
        return None
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
    """Stamp `kind` only when character has no tag toward other yet."""
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

    Returns a short id string used as a key into game flavor pools,
    or None when there is no tag either way.
    """
    a = get_kind(character, other)
    b = reciprocal_kind(character, other)
    if a is None and b is None:
        return None
    if a == b == "spouse":
        return "mutual_spouse"
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


def is_grumpy_for_partners(character):
    """True when partner preference should skip forcing a lover."""
    if _is_grumpy_for_partners is not None:
        return bool(_is_grumpy_for_partners(character))
    traits = getattr(character, "traits", None) or []
    return "grumpy" in traits


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


def pick_hunt_partner(actor, candidates, *, grumpy=None):
    """Choose a hunt buddy from CLOSE_KINDS (lover first unless grumpy)."""
    if _wants_solo_hunt is not None and _wants_solo_hunt(actor):
        return None
    if grumpy is None:
        grumpy = is_grumpy_for_partners(actor)
    ensure_defaults(actor)
    order = (
        "spouse", "lover", "sibling", "parent", "best_friend", "ashkin", "friend",
    )
    for kind in order:
        if kind == "lover" and grumpy:
            continue
        pool = [c for c in candidates if get_kind(actor, c) == kind]
        picked = pick_nearest(actor, pool)
        if picked is not None:
            return picked
    return None


def pick_rest_bar_buddy(actor, candidates, *, grumpy=None, game=None):
    """Prefer favorite_person, else CLOSE_KINDS ladder for rest/bar."""
    if grumpy is None:
        grumpy = is_grumpy_for_partners(actor)
    ensure_defaults(actor)
    if game is not None:
        fav = resolve_favorite_person(actor, game)
        if fav is not None and fav in candidates:
            if not (grumpy and get_kind(actor, fav) == "lover"):
                return fav
    order = (
        "spouse", "lover", "sibling", "parent", "best_friend", "ashkin", "friend",
    )
    for kind in order:
        if kind == "lover" and grumpy:
            continue
        pool = [c for c in candidates if get_kind(actor, c) == kind]
        picked = pick_nearest(actor, pool)
        if picked is not None:
            return picked
    return None


def are_siblings(a, b):
    """True when both have tagged each other as sibling (mutual)."""
    if a is None or b is None or a is b:
        return False
    return get_kind(a, b) == "sibling" and get_kind(b, a) == "sibling"


def are_brothers(a, b):
    """Alias for are_siblings (legacy call sites / smoke)."""
    return are_siblings(a, b)


def are_ashkin(a, b):
    """True when both have tagged each other as ashkin (mutual war-bond)."""
    if a is None or b is None or a is b:
        return False
    return get_kind(a, b) == "ashkin" and get_kind(b, a) == "ashkin"


def ashkin_partners_in_room(character, *, exclude=None):
    """Living mutual-ashkin partners sharing character's room."""
    if character is None:
        return []
    room = getattr(character, "location", None)
    if room is None:
        return []
    found = []
    for other in room.characters():
        if other is character or other is exclude:
            continue
        if getattr(other, "hp", 0) <= 0:
            continue
        if getattr(other, "spirit", False):
            continue
        if are_ashkin(character, other):
            found.append(other)
    return found


def ashkin_incoming_mult(defender, attacker=None):
    """Incoming damage mult: 1.0 - ASHKIN_SOAK when an ashkin ally is present."""
    if not ashkin_partners_in_room(defender, exclude=attacker):
        return 1.0
    return max(0.0, 1.0 - ASHKIN_SOAK)


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
    """Set explicit favorite_person to other's key. Returns the key."""
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
    """Return the Character this actor favors, or None."""
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


def social_lead_follow_roles(a, b):
    """Decide (leader, follower) for a social hangout pair."""
    if a is None or b is None or a is b:
        return (None, None)
    from engine.systems import persona_registry as personas_mod

    assert_a = personas_mod.assertiveness(a)
    assert_b = personas_mod.assertiveness(b)
    if assert_a > assert_b:
        leader, follower = a, b
    elif assert_b > assert_a:
        leader, follower = b, a
    else:
        social_a = float(getattr(a, "social", 0.0) or 0.0)
        social_b = float(getattr(b, "social", 0.0) or 0.0)
        if social_a > social_b:
            leader, follower = b, a
        elif social_b > social_a:
            leader, follower = a, b
        else:
            if (getattr(a, "key", "") or "") <= (getattr(b, "key", "") or ""):
                leader, follower = a, b
            else:
                leader, follower = b, a

    if _reluctant_hangout_follow is not None:
        if _reluctant_hangout_follow(follower):
            return (None, None)
        return (leader, follower)
    traits = getattr(follower, "traits", None) or []
    if "grumpy" in traits:
        if _social_lead_follow_grumpy_gate is not None:
            if not _social_lead_follow_grumpy_gate(follower):
                return (None, None)
        else:
            return (None, None)
    return (leader, follower)


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


def can_brother_rescue(actor):
    """True when actor may run kinship Limbo / afterlife rescue."""
    traits = getattr(actor, "traits", None) or []
    if "loyal" not in traits:
        return False
    return "reckless" in traits or "resolute" in traits


def can_ashkin_rescue(actor):
    """True when actor may run the ashkin afterlife rescue."""
    return can_brother_rescue(actor)


def _is_living_embodied(character):
    """True when character can act in the world (not spirit / dead / clinic)."""
    if character is None:
        return False
    if getattr(character, "spirit", False):
        return False
    if float(getattr(character, "hp", 0) or 0) <= 0:
        return False
    if getattr(character, "hospitalized", False):
        return False
    if getattr(character, "collapsed", False):
        return False
    return True


def living_mutual_brothers(character, game):
    """Living mutual siblings of `character` found in `game` (may be empty)."""
    if character is None or game is None:
        return []
    ensure_defaults(character)
    found = []
    for other_key in list_of_kind(character, "sibling"):
        other = game.find_character(other_key)
        if other is None or other is character:
            continue
        if get_kind(other, character) != "sibling":
            continue
        if not _is_living_embodied(other):
            continue
        found.append(other)
    return found


def has_living_mutual_brother(character, game):
    """True when at least one living mutual sibling exists in `game`."""
    return bool(living_mutual_brothers(character, game))


def living_mutual_ashkin(character, game):
    """Living mutual ashkin partners of `character` in `game` (may be empty)."""
    if character is None or game is None:
        return []
    ensure_defaults(character)
    found = []
    for other_key in list_of_kind(character, "ashkin"):
        other = game.find_character(other_key)
        if other is None or other is character:
            continue
        if get_kind(other, character) != "ashkin":
            continue
        if not _is_living_embodied(other):
            continue
        found.append(other)
    return found


def has_living_mutual_ashkin(character, game):
    """True when at least one living mutual ashkin partner exists in `game`."""
    return bool(living_mutual_ashkin(character, game))


def ensure_brother_bond(a, b):
    """Stamp mutual sibling; upgrades legacy friend tags to sibling."""
    if a is None or b is None or a is b:
        return
    ensure_defaults(a)
    ensure_defaults(b)
    for left, right in ((a, b), (b, a)):
        existing = get_kind(left, right)
        if existing in (None, "friend"):
            set_kind(left, right, "sibling")
