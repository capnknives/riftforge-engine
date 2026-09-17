"""vessel.py -- Celestial vessel chassis (folklore peel Wave 5b).

Pure field defaults and room-visible identity helpers for Mantles that
ride or possess mortal hosts. Show-specific verbs (discorporate, smite,
exorcise, Jimmy warehouse prose) stay in ``supers.vessel``; this module
only owns the shared chassis:

  * ``ensure_vessel_defaults`` — idempotent field stamp
  * ``is_riding`` / ``is_possessed_host`` — possession flags
  * ``public_actor`` / ``public_name`` — watcher-facing identity

Never expose raw storage keys or VNUMs in ``public_name`` — use face
overlays and ``engine.char_identity.legal_public_name`` instead.
"""

RATING_FAIR = "fair"


def ensure_vessel_defaults(character) -> None:
    """Fill vessel / possession / rating / ward fields. Idempotent.

    Early return when ``vessel_free`` already exists — same contract as
    live ``supers.vessel.ensure_vessel_defaults`` before SUPERS-only
    extras (smite fugitive ticks, vessel_passenger, …).
    """
    if hasattr(character, "vessel_free"):
        return
    character.vessel_free = False
    if not hasattr(character, "possess_opt_in"):
        character.possess_opt_in = False
    if not hasattr(character, "possessed_by"):
        character.possessed_by = None
    if not hasattr(character, "vessel_host_key"):
        character.vessel_host_key = None
    if not hasattr(character, "vessel_rider_rating"):
        character.vessel_rider_rating = None
    if not hasattr(character, "vessel_rider_forced"):
        character.vessel_rider_forced = False
    if not hasattr(character, "vessel_rider_consenting"):
        character.vessel_rider_consenting = False
    if not hasattr(character, "pending_realm_choice"):
        character.pending_realm_choice = False
    if not hasattr(character, "vessel_rating"):
        character.vessel_rating = RATING_FAIR
    if not hasattr(character, "homesickness"):
        character.homesickness = 0.0
    if not hasattr(character, "ward_demon_possess"):
        character.ward_demon_possess = False
    if not hasattr(character, "ward_angel_possess"):
        character.ward_angel_possess = False
    if not hasattr(character, "vessel_confused_ticks"):
        character.vessel_confused_ticks = 0
    if not hasattr(character, "vessel_spirit_prop"):
        character.vessel_spirit_prop = False


def is_riding(character) -> bool:
    """True when this Mantle is inside another living vessel."""
    ensure_vessel_defaults(character)
    return bool(getattr(character, "vessel_host_key", None))


def is_possessed_host(character) -> bool:
    """True when another Mantle is riding this living body."""
    ensure_vessel_defaults(character)
    return bool(getattr(character, "possessed_by", None))


def ridden_host(character, game=None):
    """Living host Character this Mantle is riding, or None."""
    ensure_vessel_defaults(character)
    host_key = getattr(character, "vessel_host_key", None)
    if not host_key:
        return None
    room = getattr(character, "location", None)
    if room is not None:
        chars = room.characters() if callable(getattr(room, "characters", None)) else []
        for ch in chars:
            if getattr(ch, "key", None) == host_key:
                return ch
    if game is not None:
        # Exact key lookup -- do not call Game.find_character (name/ordinal
        # resolver). Live used supers.vessel.find_character_by_key.
        from engine.char_index import find_character_by_key

        return find_character_by_key(game, host_key)
    return None


def public_actor(character, game=None):
    """Room-visible actor: the ridden host while riding, else self."""
    host = ridden_host(character, game)
    return host if host is not None else character


def public_name(character, game=None) -> str:
    """Display name for room broadcasts while possibly riding / assumed.

    Prefer face overlays on the room-visible actor, then on the rider.
    Fall back to legal given+surname — never the raw storage key.
    """
    actor = public_actor(character, game)
    face = (
        getattr(actor, "assumed_face", None)
        or getattr(actor, "husk_display_name", None)
    )
    if face:
        return face
    face = (
        getattr(character, "assumed_face", None)
        or getattr(character, "husk_display_name", None)
    )
    if face:
        return face
    try:
        from engine.char_identity import legal_public_name

        return legal_public_name(actor)
    except Exception:
        from engine.char_identity import humanize_storage_key
        from engine.command_support import strip_ephemeral_storage_prefix

        return humanize_storage_key(
            strip_ephemeral_storage_prefix(getattr(actor, "key", None) or "?")
        )
