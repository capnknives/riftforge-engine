"""
engine/command_support.py -- shared helpers used by more than one verb
package, kept SUPERS-agnostic (two-repo purity Phase 2b:
docs/plans/two_repo_purity.md).

commands.py used to be one 7000-line file with every `cmd_*` handler in it;
it has since been peeled into `engine/verbs/` (generic MUD verbs) and
`supers/verbs/` (SUPERS game verbs) -- see those packages' docstrings. A
handful of small helpers don't belong to either side alone because BOTH
sides call them (e.g. `_is_gm` gates both an engine verb like `reports` and
a dozen SUPERS GM verbs). This module holds them.

Phase 2b history: this used to live at the repo root as command_support.py
and reached into `supers` directly for a spirit-sight Attunement check and a
handful of move side effects (training cancel, job-site stop, carried-body
travel, lodging owner-enters). Those now go through `engine.hooks`
(`can_see_spirit`, `before_relocate`, `after_arrive`, `encounter_check`) --
the same pattern `engine/verbs/basic.py` already used for its own game-
flavor hooks. Root `command_support.py` is now a thin re-export facade over
this module, so every existing `from command_support import X` callsite
across the codebase keeps working unchanged.
"""

from engine.hooks import (
    after_arrive,
    after_move_crossing,
    after_move_step,
    before_move_crossing,
    before_relocate,
    can_perceive_reaper,
    can_see_spirit,
    encounter_check,
    follow_pull_handled,
    follow_pull_homestead_plot,
    follow_pull_household_pet,
    follow_pull_skip,
    follow_shares_origin,
    follow_survival_peel_message,
    in_veil,
    move_gate_block,
    veil_visible_to,
)
import re
import shlex

from engine.world import Character


def split_shell_args(text):
    """Split *text* on whitespace, honoring ``'…'`` and ``"…"`` phrases.

    Classic MUD disambiguation: ``bug 'Earl Jacobs'`` and ``goto 'Steve Toth'``
    must treat the quoted span as one name token, not two partial matches.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=True)
    except ValueError:
        # Unclosed quote — fall back so the verb still runs.
        return raw.split()


def peel_shell_arg(text):
    """Return ``(first_token, rest)`` after one shell-style arg."""
    tokens = split_shell_args(text)
    if not tokens:
        return "", ""
    first = tokens[0]
    rest = " ".join(tokens[1:])
    return first, rest


def unwrap_name_quotes(text):
    """Strip one matching ``'…'`` / ``"…"`` wrapper from a name token."""
    raw = (text or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1].strip()
    return raw


def split_gm_name_and_rest(game, args):
    """Split ``<name> <command>`` for gm force (quoted or two-word keys).

    ``gm force Officer Bates look`` used to take name ``Officer`` and
    command ``Bates look``. Prefer a quoted first token, then the longest
    prefix that ``resolve_gm_target_character`` actually finds, leaving at
    least one command token.
    """
    text = (args or "").strip()
    if not text:
        return "", ""
    if text[:1] in ("'", '"'):
        name, rest = peel_shell_arg(text)
        return unwrap_name_quotes(name), rest
    tokens = text.split()
    if len(tokens) < 2:
        return "", ""
    # Longest resolvable name prefix, then first token as fallback.
    for i in range(len(tokens) - 1, 0, -1):
        name = " ".join(tokens[:i])
        rest = " ".join(tokens[i:])
        if resolve_gm_target_character(game, name) is not None:
            return name, rest
    return tokens[0], " ".join(tokens[1:])


def collapse_shell_args(text):
    """Turn args into one lookup string; unwrap quotes, keep inner spaces."""
    tokens = split_shell_args(text)
    if not tokens:
        return ""
    return " ".join(tokens)


ASLEEP_WORLD_CLOSED_MSG = (
    "You're asleep -- the outside world is closed. Type 'wake'."
)


def asleep_blocks_world(character):
    """True when lodging sleep closes sensory verbs (look, auto-look, …).

    ``commands.dispatch`` enforces this for typed input; ``cmd_look`` and
    other direct call sites must share the same gate (bug #210: vehicle
    leave / walk arrival auto-look bypassed dispatch).

    Full-moon sleep-hunt is the Cadence exception: offline Echoes / NPCs
    with ``sleep_hunting`` may still move and hunt via ``npc_do`` (SilentSession).
    Live players must type ``wake`` first.

    Sleep-dream awareness is the other exception: the dream-self (or an
    in-place lucid sleeper already on the Dream plane) can look / speak /
    walk. The frozen Earth husk stays closed.
    """
    if not getattr(character, "asleep", False):
        return False
    if getattr(character, "is_sleep_dream_clone", False):
        return False
    if getattr(character, "sleep_dream_tour", False) and not getattr(
        character, "sleep_dream_clone_key", None,
    ):
        return False
    if getattr(character, "sleep_hunting", False):
        from engine.npc_act import is_live_session

        if not is_live_session(getattr(character, "session", None)):
            return False
    return True


def send_asleep_world_closed(character):
    """If asleep, tell the player the world is closed. Returns True if blocked."""
    if not asleep_blocks_world(character):
        return False
    send_if_online(character, ASLEEP_WORLD_CLOSED_MSG)
    return True


def send_if_online(character, message):
    """Send ``message`` when ``character`` has a live Session (Echo-safe)."""
    if not message:
        return
    session = getattr(character, "session", None)
    if session is not None and hasattr(session, "send"):
        session.send(message)


def require_here(character, *, tell=True, nowhere="You're nowhere."):
    """Return the acting room, or None after an honest nowhere line.

    Limbo (copyover stub, failed place, test double) used to crash any
    verb that walked ``character.location.contents`` / ``.characters()``
    or called ``.broadcast`` with no None check.
    """
    room = getattr(character, "location", None)
    if room is None:
        if tell:
            send_if_online(character, nowhere)
        return None
    return room


def broadcast_here(character, message, **kwargs):
    """Room broadcast that no-ops when the actor has no room."""
    room = getattr(character, "location", None)
    if room is None or not message:
        return False
    room.broadcast(message, **kwargs)
    return True


def _log_hook_error(where, detail=None):
    """Surface a failed display/presence hook without breaking the verb.

    Look labels and target matching are best-effort side effects of hooks
    registered by SUPERS. A broken hook must fall through to the bare-
    engine fallback (key / short-desc), but swallowing the exception hid
    real wiring regressions. Log once with a traceback so they stay
    visible in the server console -- same shape as engine.world /
    engine.npc_act activity-log helpers (#545).
    """
    import traceback
    if detail is None:
        print(f"[command_support] {where} failed:", flush=True)
    else:
        print(f"[command_support] {where} failed for {detail!r}:", flush=True)
    traceback.print_exc()


def _can_see_spirit(viewer, spirit_char):
    """Section 6's Attunement gate on spirit-sight, giving RES/FOC's
    "spirit tether"/"attunement" jobs (section 1's stat table) a concrete
    use. A spirit always perceives itself (engine.hooks.can_see_spirit's own
    default, with no game needed); anyone else's eligibility (Spirit Magic
    casters, high-Attunement characters) is SUPERS' call -- registered onto
    the hook by supers/bootstrap.py's register_all_hooks().
    """
    return can_see_spirit(viewer, spirit_char)


def _can_perceive_reaper(viewer, other):
    """Living Reaper Mantle veil (Vesseldetails3 / help reaper).

    Thin wrapper over engine.hooks.can_perceive_reaper so verb packages
    keep importing from command_support the same way as spirit-sight.
    """
    return can_perceive_reaper(viewer, other)


def is_folded(obj):
    """True when staff ``gm fold`` has shelved this offline Echo.

    Soft shelve: body stays in the world / save path, but Cadence and
    ordinary presence treat it as absent until login or ``gm unfold``.
    """
    return bool(getattr(obj, "folded", False))


def _can_see_gm_away(viewer, body):
    """True when ``viewer`` may perceive a staff Echo left by ``gm on``.

    ``gm_away`` bodies are true-invisible to players / NPCs (spirit-sight
    does not pierce). Staff (gm / head_gm), anyone already in GM form, and
    the piloting spirit looking at its own body still see them -- so
    ``who`` / look / where keep working for tooling.

    Same pierce rules apply to ``folded`` Echoes (rare staff shelve).
    """
    if body is None or viewer is None:
        return False
    if viewer is body:
        return True
    # Piloting spirit → its left-behind body.
    if getattr(viewer, "gm_body_key", None) and (
        getattr(viewer, "gm_body_key", None) == getattr(body, "key", None)
    ):
        return True
    if getattr(viewer, "gm_spirit", False) or getattr(viewer, "gm_mode", False):
        return True
    rank = getattr(viewer, "gm_rank", None)
    if rank in ("gm", "head_gm"):
        return True
    return False


def staff_perfect_vision(character):
    """True when staff GM form pierces weather whiteout and dark rooms.

    Mirrors ``in_gm_mode`` in ``supers/verbs/gm/presence.py`` without
    importing SUPERS from engine layers (two-repo purity). Permanent
    ``gmspirit:`` bodies heal ``gm_mode`` on tool checks; the key prefix
    and ``gm_spirit`` flag cover autosave gaps.
    """
    if character is None:
        return False
    if getattr(character, "gm_mode", False):
        return True
    if getattr(character, "gm_spirit", False):
        return True
    if getattr(character, "gm_spirit_permanent", False):
        return True
    key_low = (getattr(character, "key", None) or "").lower()
    if key_low.startswith("gmspirit:"):
        return True
    # Staff riding visible immersion cast (playcast / assigned login).
    session = getattr(character, "session", None)
    if session is None:
        return False
    if bool(getattr(session, "playcast_gm_tools", False)):
        return bool(getattr(session, "gm_eyes_staff_view", False))
    if getattr(character, "immersion", False) and getattr(
        session, "staff_account", None,
    ):
        return bool(getattr(session, "gm_eyes_staff_view", False))
    return False


def in_active_gm_form(character):
    """True while this character is actually piloting GM form right now.

    Mirrors ``in_gm_mode`` in ``supers/verbs/gm/presence.py`` without
    importing SUPERS from engine layers (two-repo purity). Unlike
    ``_is_gm`` (account-is-staff, ever), this reflects the live GM
    on/off toggle -- a staff account playing an immersion cast body
    with GM powers off returns False here (bug report 1574: ``follow``
    was routing a staff-off cast body into the GM-only diagnostic tail
    because it checked ``_is_gm`` instead of this).
    """
    if character is None:
        return False
    if bool(getattr(character, "gm_mode", False)):
        return True
    if getattr(character, "session", None) is None:
        return False
    if not getattr(character, "gm_spirit", False):
        return False
    key_low = (getattr(character, "key", None) or "").lower()
    if key_low.startswith("gmspirit:") or bool(
        getattr(character, "gm_spirit_permanent", False)
    ):
        character.gm_mode = True
        return True
    return False


def _can_see_hellhound(viewer, hound):
    """Deal-pursuit hellhound invis pierce (Celestial / glasses / engage).

    Game registers ``hooks.set_can_see_hellhound``; bare engine defaults
    to False (no Deal hellhounds).
    """
    from engine import hooks
    return bool(hooks.can_see_hellhound(viewer, hound))


def _is_presence_hidden(viewer, other):
    """True when ``other`` should be omitted from viewer's look / targeting.

    Combines death-spirit invisibility (section 6), the living Reaper
    Mantle veil, Deal hellhound true-invis, true-invis staff Echoes left
    behind by ``gm on``, and rare ``gm fold`` shelved Echoes so call
    sites do not drift apart.
    """
    if other is None or viewer is None:
        return False
    if other is viewer:
        return False
    # Earth Veil layer -- death spirits, faded Ghosts, veiled Reapers, etc.
    # Deal hellhounds are Prime-physical invisibles. If they were mis-layered
    # onto the Veil, innate / glasses pierce still wins so Angels see them.
    if in_veil(other) and not veil_visible_to(viewer, other):
        hound_invis = bool(
            getattr(other, "invisible", False)
            or getattr(other, "hellhound_invisible", False)
        )
        if not (hound_invis and _can_see_hellhound(viewer, other)):
            return True
    # Staff Cadence Echo while Session pilots GM form -- true invis.
    if getattr(other, "gm_away", False) and not _can_see_gm_away(viewer, other):
        return True
    # Soft-shelved player Echo (gm fold) -- same pierce as gm_away.
    if is_folded(other) and not _can_see_gm_away(viewer, other):
        return True
    # GM staff shell -- wizinvis / parked gate (uses gm_spirit, not spirit).
    if getattr(other, "gm_spirit", False) and not _can_see_spirit(viewer, other):
        return True
    # RPC / staff observe-cloak -- GMs and other council members pierce.
    if getattr(other, "staff_cloak", False):
        from engine import hooks
        if not _is_gm(viewer) and not hooks.is_rpc_staff(viewer):
            return True
    # Deal-pack hellhounds are physical dogs. A stray ``spirit`` stamp
    # must not use death-spirit hide -- hellhound pierce below owns that.
    pack_hound = bool(
        getattr(other, "hellhound_invisible", False)
        or getattr(other, "_hellhound_pack", False)
    )
    if (
        getattr(other, "spirit", False)
        and not pack_hound
        and not _can_see_spirit(viewer, other)
    ):
        return True
    if not _can_perceive_reaper(viewer, other):
        return True
    # Deal hellhounds: invisible unless Celestial / glasses.
    # Check both the generic ``invisible`` flag and the Deal-specific stamp.
    if (
        getattr(other, "invisible", False)
        or getattr(other, "hellhound_invisible", False)
    ):
        if not _can_see_hellhound(viewer, other):
            return True
    # Mundane hide/sneak (ghost fade / umbral lurk stamp stealth_active).
    if getattr(other, "stealth_active", False):
        from engine import hooks
        if not hooks.can_notice_stealth(viewer, other):
            return True
    # Game extras (Signal dwell in hardware, …) -- not mundane stealth.
    from engine import hooks as _hooks_presence
    if _hooks_presence.presence_hidden_extra(viewer, other):
        return True
    # Trickster withdraw -- real Loki (etc.) folds into the shrine while an
    # archangel wears their face; casual onlookers should not see two of
    # them standing in the same room (bug report 632 / docs/LORE.md
    # "Gabriel/Loki bargain"). Staff, research intel, hunter field codex,
    # and tier-3+ angel grace-sight still pierce it (trickster research).
    if getattr(other, "trickster_laying_low", False):
        from engine import hooks
        if not hooks.can_perceive_trickster_laylow(viewer, other):
            return True
    # Magi soul-carry hitchhike -- the passenger is inside the host's
    # blood (Dean/Benny). Host and staff still see them; everyone else
    # treats the body as gone from the room.
    carrier = getattr(other, "soul_carrier_key", "") or ""
    if carrier:
        if getattr(viewer, "key", None) != carrier and not _is_gm(viewer):
            return True
    return False


def _staff_tags_hidden_in_look(viewer, other):
    """True when a hidden presence should appear in look tagged ``(hidden)``.

    Players still omit stealth-hidden bodies entirely. Staff in ``gm on``
    see them in the Souls section (and may ``look <name>`` them) with the
    same tag who uses for soft-omitted names -- ops clarity without
    treating hide/sneak as fully revealed.
    """
    if viewer is None or other is None or viewer is other:
        return False
    if not _is_presence_hidden(viewer, other):
        return False
    from engine import hooks
    return bool(hooks.staff_tags_hidden_presence(viewer, other))


def _is_attacking_viewer(viewer, other):
    """True when ``other`` shares a room with ``viewer`` and focuses them."""
    if viewer is None or other is None or other is viewer:
        return False
    room = getattr(viewer, "location", None)
    if room is None or getattr(other, "location", None) is not room:
        return False
    return getattr(other, "target", None) is viewer


def _can_target_for_combat(viewer, other):
    """True when ``viewer`` may pick ``other`` for fight verbs (attack, hex, …).

    Hidden presences stay off ``look`` / ``who``, but a body already swinging
    at you is targetable -- you can return fire at the foe you feel. Deal
    hellhounds keep their blind-swing / sight / kill-tool gates.
    """
    if other is None or viewer is None:
        return False
    if other is viewer:
        return True
    from engine import hooks
    if hooks.is_untargetable_helper(other):
        return False
    if not _is_presence_hidden(viewer, other):
        return True
    if not _is_attacking_viewer(viewer, other):
        return False
    from engine import hooks
    return hooks.can_target_hidden_attacker(viewer, other)


def _combat_target_candidates(actor, base_candidates):
    """Extend visible fight picks with hidden bodies actively attacking ``actor``."""
    merged = list(base_candidates or [])
    seen = {id(c) for c in merged}
    room = getattr(actor, "location", None)
    if room is None:
        return merged
    for other in room.characters():
        if other is actor or id(other) in seen:
            continue
        if not _can_target_for_combat(actor, other):
            continue
        if not _is_presence_hidden(actor, other):
            continue
        merged.append(other)
        seen.add(id(other))
    return merged


def _presence_hears(actor):
    """Predicate for ``Room.broadcast`` about ``actor``'s presence.

    Returns True when the watcher may hear leave/arrive / Cadence narrate
    lines naming ``actor``. Hidden presence (veiled Reaper, gm_away Echo,
    folded Echo, unseen spirit, invisible hellhound) is silent to
    ordinary watchers.
    """
    def _hears(watcher):
        return not _is_presence_hidden(watcher, actor)

    return _hears


def strip_ephemeral_storage_prefix(name):
    """Peel internal storage prefixes (nested too).

    Storage keys like ``gmspirit:Wits``, ``husk:Castiel``, ``twin:Gary``,
    or ``gym_dummy:TM00001`` must never reach players as a display name.
    Also cleans a mistaken ``assumed_face`` that still carries the prefix.
    Returns ``?`` only when the input is empty after stripping.
    """
    if name is None:
        return "?"
    text = str(name).strip()
    if not text:
        return "?"
    # Nested bug (gm on while already on a spirit) left double prefixes.
    while True:
        low = text.lower()
        if (
            low.startswith("gmspirit:")
            or low.startswith("husk:")
            or low.startswith("twin:")
            or low.startswith("dreamself:")
        ):
            text = text.split(":", 1)[1].strip()
            if not text:
                return "?"
            continue
        if low.startswith("gym_dummy:"):
            # Room suffix is an internal VNUM -- never expose it.
            return "a training dummy"
        if low.startswith("cultivator_rival:"):
            # Bonded rival storage keys are never player-facing names.
            return "a rival"
        return text


def is_staff_stealth_presence(obj):
    """True for GM staff spirit / gm_away / folded Echo -- no room tells.

    Matches ``_move_one`` leave/arrive stealth: true-invisible staff form,
    the Cadence body left by ``gm on``, and rare ``gm fold`` shelved
    Echoes must not broadcast idle stir, disconnect "goes still", or
    zone-transition arrives to watchers.
    """
    if not isinstance(obj, Character):
        return False
    if getattr(obj, "gm_spirit", False):
        # A staffer who toggled `wizinvis off` is a deliberate reveal: the
        # active form should broadcast leave/arrive/idle like any visible
        # character. Parked spirits and the default (wizinvis on) stay silent.
        active = (
            getattr(obj, "session", None) is not None
            or getattr(obj, "gm_mode", False)
        )
        if active and not getattr(obj, "wizinvis", True):
            return False
        return True
    if getattr(obj, "staff_cloak", False):
        return True
    return bool(
        getattr(obj, "gm_away", False)
        or is_folded(obj)
    )


def _presence_face(obj):
    """Public face for score / prompt / leave-arrive (no echo tags).

    Prefers ``assumed_face`` / ``husk_display_name`` over storage keys
    like ``gmspirit:Wits``, ``husk:Castiel``, or ``twin:Gary`` so players
    never see those internal prefixes in room traffic or sheets. When no
    face overlay is
    set, uses legal given (+ visible surname). Always strips ephemeral
    prefixes from whichever string wins (including a polluted face).

    Room prose (idle stir/still, zone enter, overland leave/arrive, get /
    drop) must call this -- never interpolate ``character.key`` into a
    watcher-facing string. Storage keys stay in persistence / find only.
    """
    if not isinstance(obj, Character):
        return strip_ephemeral_storage_prefix(getattr(obj, "key", "?"))
    face = (
        getattr(obj, "assumed_face", None)
        or getattr(obj, "husk_display_name", None)
    )
    if face:
        peeled = strip_ephemeral_storage_prefix(face)
        # assumed_face stuck on the bare storage mononym while legal identity
        # carries a visible surname (login roster vs room face -- bug 748).
        if not getattr(obj, "husk_display_name", None):
            key_face = strip_ephemeral_storage_prefix(
                getattr(obj, "key", None) or ""
            )
            if key_face and peeled.lower() == key_face.lower():
                try:
                    from engine.char_identity import (
                        character_surname,
                        legal_public_name,
                        storage_key_has_camel_mash,
                    )
                    # Pierre + Dupont on a bare mononym key (bug 748) -- not
                    # CapnKnives-style account brands with interior humps.
                    if (
                        character_surname(obj)
                        and bool(getattr(obj, "surname_visible", True))
                        and not storage_key_has_camel_mash(key_face)
                    ):
                        legal = legal_public_name(obj)
                        if legal and legal.lower() != peeled.lower():
                            return strip_ephemeral_storage_prefix(legal)
                except Exception:
                    _log_hook_error(
                        "legal_public_name", getattr(obj, "key", None)
                    )
        return peeled
    # Legal name (given + optional visible surname) when no face overlay.
    try:
        from engine.char_identity import legal_public_name
        return legal_public_name(obj)
    except Exception:
        _log_hook_error("legal_public_name", getattr(obj, "key", None))
        return strip_ephemeral_storage_prefix(getattr(obj, "key", None) or "?")


def _public_label(obj):
    """Player/staff-facing label -- never ``gmspirit:`` / ``husk:`` keys.

    Characters use ``_display_name`` (``Wits(GM)``, ``Jimmy Novak (echo)``,
    …). Items keep their floor key. Bare strings (relationship keys, etc.)
    get ephemeral prefixes stripped. Internal storage keys stay in code /
    persistence only -- always call this (or ``_display_name`` for Characters)
    before ``session.send``, sheets, ``where``, or snoop output.
    """
    if obj is None:
        return "?"
    if isinstance(obj, Character):
        return _display_name(obj)
    from engine.world import Item
    if isinstance(obj, Item):
        return getattr(obj, "key", "?")
    text = str(obj).strip()
    if not text:
        return "?"
    return strip_ephemeral_storage_prefix(text)


def _strip_leading_article(name):
    """Drop a leading a/an/the so stacks can pluralize the bare noun."""
    text = (name or "").strip()
    low = text.lower()
    for art in ("an ", "a ", "the "):
        if low.startswith(art):
            return text[len(art):].strip()
    return text


# Singular stems that already end in ``s`` but still take ``-es``.
_SINGULAR_ES_EXCEPTIONS = {
    "glass": "glasses",
}


def _simple_english_plural(noun):
    """Cheap English plural for floor-item stacks (blade -> blades).

    Container phrases (``plate of sugar cookies``) pluralize the first
    lowercase word only so stacks do not become ``cookieses``. Invariant
    plurals (``sweatpants``, ``sneakers``, ``jeans``, ``shoes``) stay
    unchanged when already ending in ``s``. Proper-noun heads such as
    ``Men of Letters …`` skip the container rule. Good enough for
    catalog keys; not a full inflection library (stdlib-only).
    """
    word = (noun or "").strip()
    if not word:
        return word
    # ``plate of sugar cookies`` -> ``plates of sugar cookies``.
    of_at = word.lower().find(" of ")
    if of_at > 0:
        head = word[:of_at]
        rest = word[of_at:]
        if " " not in head and head[:1].islower():
            return _simple_english_plural(head) + rest
    # Multi-word: only inflect the last token.
    if " " in word:
        head, tail = word.rsplit(None, 1)
        return f"{head} {_simple_english_plural(tail)}"
    low = word.lower()
    if low in _SINGULAR_ES_EXCEPTIONS:
        suffix = _SINGULAR_ES_EXCEPTIONS[low]
        if word[0].isupper():
            return suffix[:1].upper() + suffix[1:]
        return suffix
    # Singular stems that take -es (kiss, bus, church, box) — not bare -s
    # endings like pants/sneakers/shoes that are already plural-only.
    if low.endswith(("ss", "us", "is", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 1 and low.endswith("y") and low[-2] not in "aeiou":
        return word[:-1] + "ies"
    if low.endswith("fe"):
        return word[:-2] + "ves"
    if low.endswith("f") and not low.endswith("ff"):
        return word[:-1] + "ves"
    if low.endswith("s"):
        return word
    return word + "s"


_SEVERED_HEAD_CATALOGS = frozenset(
    {"severed_head", "severed_demon_head", "severed_leviathan_head"}
)


def _floor_item_stack_key(item):
    """Identity for stacking identical floor loot on look.

    Bodies stay unique (never ``2 bodies of Bob are here``). Catalog id
    wins when present so sixteen angel blades collapse; otherwise the
    display key (lowered) is the group.

    Severed heads share a catalog id but are named per victim -- each stays
    its own look line (like bodies) so mixed trophy piles read clearly.
    """
    if getattr(item, "is_body", False):
        # Each corpse is its own line even when keys match.
        return ("body", id(item))
    cat = getattr(item, "catalog_id", None)
    if cat and str(cat).strip().lower() in _SEVERED_HEAD_CATALOGS:
        return ("severed_head", id(item))
    if cat:
        return ("cat", str(cat).strip().lower())
    key = (getattr(item, "key", None) or "").strip().lower()
    return ("key", key or id(item))


def visible_floor_items(viewer, items):
    """Return floor Items ``viewer`` may perceive.

    Veil-layer ghost copies are omitted unless the viewer pierces the Veil
    (high Attunement, Spirit Magic, pierceveil, Ghosts, staff).
    """
    from engine import hooks
    if not items:
        return []
    return [item for item in items if hooks.item_visible_to(viewer, item)]


def floor_item_look_lines(items, character=None):
    """Build look lines for floor Items, stacking identical copies.

    One of a kind stays the usual display name (``an angel blade``).
    Two or more become ``N angel blades are here`` with a digit count
    (never ``sixteen``). Wayfinding signs keep muted paint / ``[SIGN]``
    the same way the old per-item loop did. Veil ghost gear is omitted
    for ordinary living sight and tagged ``[Veil]`` when pierced.
    """
    from collections import OrderedDict
    from engine import hooks

    items = visible_floor_items(character, items)
    groups = OrderedDict()
    for item in items:
        groups.setdefault(_floor_item_stack_key(item), []).append(item)

    lines = []
    for group in groups.values():
        sample = group[0]
        name = _display_name(sample)
        count = len(group)
        # Staff look: singles show short desc[INUM] (same gate as room VNUMs).
        # Stacks stay the player plural -- use ``where item all`` for copies.
        if count == 1 and staff_room_vnums_in_look(character):
            from engine.item_inum import staff_item_label

            name = staff_item_label(sample)
        # Wayfinding signs: faint muted paint for sighted; plain + [SIGN]
        # meaning for everyone (never color alone).
        cat = getattr(sample, "catalog_id", None) or ""
        is_sign = cat == "wayfinding_sign" or "wayfinding" in (
            getattr(sample, "key", "") or ""
        ).lower()
        if is_sign:
            if character is not None and not getattr(
                character, "screenreader", False
            ):
                from engine import style as style_mod
                name = style_mod.paint_for(character, "muted", name)
            if "[SIGN]" not in name.upper() and "sign" not in name.lower():
                name = f"[SIGN] {name}"
        if count == 1:
            line = name
        else:
            # Strip ANSI before pluralizing so paint on singles does not leak
            # into the stack noun; re-apply muted paint for wayfinding stacks.
            from engine.style import strip_ansi
            bare = _strip_leading_article(strip_ansi(name))
            # Drop a leading [SIGN] tag from the noun, then re-prefix.
            sign_prefix = ""
            if bare.upper().startswith("[SIGN]"):
                bare = bare[6:].strip()
                sign_prefix = "[SIGN] "
            plural = _simple_english_plural(bare)
            line = f"{sign_prefix}{count} {plural} are here."
            if is_sign and character is not None and not getattr(
                character, "screenreader", False
            ):
                from engine import style as style_mod
                line = style_mod.paint_for(character, "muted", line)
        if character is not None and hooks.item_in_veil(sample):
            tag = hooks.veil_look_tag()
            if tag and tag not in line:
                line = f"{tag} {line}"
        lines.append(line)
    return lines


def format_floor_items_for_look(lines, character=None):
    """Optionally collapse stacked floor-item lines into one paragraph.

    ``config items compact on`` keeps sighted look shorter; screenreader
    keeps the vertical Items list for TTS clarity.
    """
    if not lines:
        return []
    if character is None:
        return list(lines)
    from engine import display_prefs as display_prefs_mod
    from engine.style import strip_ansi

    if not display_prefs_mod.wants_compact_floor_items(character):
        return list(lines)
    if getattr(character, "screenreader", False):
        return list(lines)
    plain = []
    for line in lines:
        text = strip_ansi(str(line)).strip()
        if text:
            plain.append(text)
    if not plain:
        return []
    if len(plain) == 1:
        return [f"On the ground: {plain[0]}."]
    return [f"On the ground: {', '.join(plain)}."]


def _display_name(obj, viewer=None):
    """How an object shows up in a room listing.

    A Character with no session is an Echo -- a logged-out player left standing
    in the world (systems doc section 4-E). Tag it so people know the figure
    won't respond. If they set a regimen, show what they're grinding
    (`Name (echo, pushups)`). A permanent NPC (Character.is_npc, e.g. the
    training dummy) also has session=None but is NOT an Echo, so it's
    excluded here.

    A discorporate spirit (section 6, Character.spirit) is tagged
    `(spirit)` -- by the time this runs on one, the caller has already
    decided the viewer can perceive it (_can_see_spirit above); this
    function itself stays viewer-agnostic unless *viewer* is passed.

    When *viewer* is set, SUPERS may swap in a viewer-relative face
    (hood/mask / unintroduced short-desc) via engine.hooks.presence_face_for.

    Without a viewer, mundane hood/mask still replaces the login key via
    concealed_presence_name (legacy path for non-viewer call sites).

    Staff GM form shows as ``Wits(GM)`` (never the ``gmspirit:`` key).
    Living husks show their mortal name (never ``husk:Mantle``).

    Follow/beckon party mates (engine.group) append ``(Group)`` when the
    viewer shares their follow tree -- e.g. Dean sees ``Sam(Group)``.

    When a staff viewer has Account pref ``gm_see_accounts`` on and is in
    GM form, append ``(AccountName)`` -- never shown to ordinary players
    (feature F / E).
    """
    name = _display_name_base(obj, viewer)
    if (
        isinstance(obj, Character)
        and viewer is not None
        and isinstance(viewer, Character)
        and obj is not viewer
    ):
        try:
            from engine import group as group_mod
            if group_mod.same_group(viewer, obj) and not str(name).endswith(
                "(Group)"
            ):
                name = f"{name}(Group)"
        except Exception:
            _log_hook_error("group suffix", getattr(obj, "key", None))
    # Feature F: GM see-accounts toggle (staff form only).
    if (
        isinstance(obj, Character)
        and viewer is not None
        and isinstance(viewer, Character)
    ):
        name = _maybe_append_account_tag(name, obj, viewer)
    return name


def staff_room_vnums_in_look(viewer):
    """True when ``look`` room titles append staff VNUM chrome."""
    if viewer is None:
        return False
    if getattr(viewer, "gm_mode", False):
        return True
    session = getattr(viewer, "session", None)
    if session is None:
        return False
    return bool(getattr(session, "gm_eyes_staff_view", False))


def _staff_viewer_uses_gm_who(viewer):
    """True when *viewer* should get staff who / seeaccounts, not player who.

    Invis GM spirit, or staff riding immersion cast with gm eyes on.
    Engine cannot import SUPERS ``staff_sees_as_gm``; Session flags
    carry the same occupy stamp.
    """
    if viewer is None:
        return False
    if getattr(viewer, "gm_mode", False) or getattr(viewer, "gm_spirit", False):
        return True
    session = getattr(viewer, "session", None)
    if session is None:
        return False
    return bool(getattr(session, "gm_eyes_staff_view", False))


def _maybe_append_account_tag(name, subject, viewer):
    """Append ``(AccountName)`` when the viewer is staff with the pref on.

    Account names must never appear for ordinary players (feature E).

    Staff already in GM form read as ``Accountname(GM)`` -- never append
    a second ``(Accountname)`` (``CapnKnives(GM)(CapnKnives)`` is noise).
    """
    if not _staff_viewer_uses_gm_who(viewer):
        return name
    # Face is already the account + (GM); tagging again is redundant.
    if _staff_form_label(subject):
        return name
    game = None
    session = getattr(viewer, "session", None)
    if session is not None:
        game = getattr(session, "game", None)
    if game is None:
        return name
    try:
        from engine.accounts import account_for_character, find_account
        viewer_account = account_for_character(game, viewer)
        if viewer_account is None:
            # Spirit may not have character.account -- check body.
            body = getattr(viewer, "gm_mode_body", None)
            if body is not None:
                viewer_account = account_for_character(game, body)
        if viewer_account is None or not viewer_account.gm_see_accounts:
            return name
        subject_account = account_for_character(game, subject)
        if subject_account is None:
            return name
        tag = subject_account.display_name or subject_account.name
        if not tag:
            return name
        suffix = f"({tag})"
        if str(name).endswith(suffix):
            return name
        return f"{name}{suffix}"
    except Exception:
        _log_hook_error("account tag", getattr(subject, "key", None))
        return name


def _shows_wanted_presence_tag(obj):
    """True when a Character should show ``(criminal)`` in look/who.

    Collared / escorted / jailed crooks are spoken for -- hide the badge
    without importing SUPERS (bug report 730).
    """
    if not isinstance(obj, Character):
        return False
    if not getattr(obj, "criminal", False):
        return False
    if getattr(obj, "_arrested_by", None):
        return False
    if getattr(obj, "_cuffed", False):
        return False
    if getattr(obj, "in_jail", False):
        return False
    if getattr(obj, "jail_until_tick", None):
        return False
    return True


def _echo_look_bits(obj):
    """Room-look Echo tags via game hook (quiet vs full -- no SUPERS import)."""
    try:
        from engine.hooks import echo_look_bits
        bits = echo_look_bits(obj)
        if bits:
            return list(bits)
    except Exception:
        _log_hook_error("echo_look_bits", getattr(obj, "key", None))
    # Bare-engine fallback: always label Echo; include idle/regimen/criminal.
    bits = ["echo"]
    if getattr(obj, "idle_mode", False) and obj.session is not None:
        bits.append("idle")
    if getattr(obj, "regimen", None):
        bits.append(obj.regimen)
    if _shows_wanted_presence_tag(obj):
        bits.append("criminal")
    return bits


def _display_name_base(obj, viewer=None):
    """Core presence label without the follow-group ``(Group)`` suffix."""
    # Staff form (gm on / GM spirit) always reads as the account face plus
    # ``(GM)``, viewer-independent. The introduce/name-learning system (which
    # masks a stranger as "a person") must never apply to a deliberately
    # revealed GM -- players who can see a `wizinvis off` form, and other
    # staff, all see ``Accountname(GM)``, not a masked description.
    if isinstance(obj, Character) and _staff_form_label(obj):
        return f"{_presence_face(obj)}(GM)"
    if isinstance(obj, Character) and viewer is not None:
        try:
            from engine.hooks import presence_face_for
            face = presence_face_for(viewer, obj)
            if face:
                if _staff_form_label(obj):
                    return f"{face}(GM)"
                if obj.acts_as_echo():
                    return f"{face} ({', '.join(_echo_look_bits(obj))})"
                if obj.spirit:
                    return f"{face} (spirit)"
                if _shows_wanted_presence_tag(obj):
                    return f"{face} (criminal)"
                return face
        except Exception:
            _log_hook_error("presence_face_for", getattr(obj, "key", None))
    if isinstance(obj, Character):
        # Mundane concealment overrides assumed_face / key for presence.
        try:
            from engine.hooks import concealed_presence_name
            face = concealed_presence_name(obj)
            if face:
                if _staff_form_label(obj):
                    return f"{face}(GM)"
                if obj.acts_as_echo():
                    return f"{face} ({', '.join(_echo_look_bits(obj))})"
                if obj.spirit:
                    return f"{face} (spirit)"
                if _shows_wanted_presence_tag(obj):
                    return f"{face} (criminal)"
                return face
        except Exception:
            _log_hook_error(
                "concealed_presence_name", getattr(obj, "key", None)
            )
    if isinstance(obj, Character) and _staff_form_label(obj):
        # Staff form (gmmode) -- invincible wanderer; not a spirit/Echo.
        return f"{_presence_face(obj)}(GM)"
    if isinstance(obj, Character) and obj.acts_as_echo():
        return f"{_presence_face(obj)} ({', '.join(_echo_look_bits(obj))})"
    if isinstance(obj, Character) and obj.spirit:
        return f"{_presence_face(obj)} (spirit)"
    if isinstance(obj, Character) and _shows_wanted_presence_tag(obj):
        return f"{_presence_face(obj)} (criminal)"
    if isinstance(obj, Character):
        return _presence_face(obj)
    from engine.world import Item
    if isinstance(obj, Item):
        from engine.systems.civic_fixture import is_fixture_item
        if is_fixture_item(obj):
            display = (getattr(obj, "display_name", None) or "").strip()
            if display:
                return display
    return obj.key


def _staff_form_label(obj):
    """True when presence should show ``Name(GM)`` for staff form.

    Any GM staff spirit is labeled staff form -- active *or* parked. A
    parked (sessionless) permanent spirit is normally hidden from look
    entirely (see ``_can_see_spirit``), but on the rare occasion it does
    reach a label path it must read ``Accountname(GM)``, never the mortal
    ``(echo)`` / ``(spirit)`` tag (that made a staffer's own spirit look
    like a real logged-out character).
    """
    if obj is None:
        return False
    if getattr(obj, "character_kind", None) == "gm":
        return True
    if getattr(obj, "gm_mode", False):
        return True
    if getattr(obj, "gm_spirit", False):
        return True
    key_low = (getattr(obj, "key", None) or "").lower()
    return key_low.startswith("gmspirit:")

def _move_one(character, direction, dest, game, auto_look=True):
    """The actual single-character move: leave/arrive broadcast, encounter
    roll, auto-look. Split out of cmd_move so `follow` (suggestions.log #44)
    can reuse it for each follower pulled along, without re-running the exit
    lookup (a follower's origin room -- same as the leader's -- already
    confirmed this exit exists).

    Game-specific side effects (cancel training, stop work, drag a carried
    body, lodging owner-enters, wilderness/dungeon spawn rolls) all run
    through engine.hooks so this function itself needs no `supers` import --
    see this module's docstring.

    ``auto_look`` (default True): when False, skip the full room look so a
    multi-hop ``walk`` path does not spam TTS / sighted clients with every
    intermediate cell. Encounter rolls still run each step. Callers that
    pass False should look once at the final destination (or when stopped).
    """
    from engine.hooks import (
        move_arrive_line, move_leave_line, move_presence_actor,
        move_public_name, movement_hears_predicate, presence_face_for,
    )
    room = character.location
    if room is None:
        # HB-50: limbo actors cannot leave/arrive-broadcast. Refuse
        # quietly so a follower pull or walk hop does not crash.
        sess = getattr(character, "session", None)
        if sess is not None:
            sess.send("You're nowhere.")
        return
    # True-invis: GM staff spirit, or the Cadence Echo left by gm on.
    stealth = bool(
        getattr(character, "gm_spirit", False)
        or getattr(character, "gm_away", False)
    )
    # Riding Mantle: leave/arrive names + hears use the host vessel
    # (immersion parity). Mantle stays spirit=True on look, but ordinary
    # eyes see the host walk -- check the host's presence, not the Mantle's.
    presence = move_presence_actor(character, game)
    # Fallback face when the viewer-relative hook is unset (bare engine).
    fallback_face = move_public_name(presence, game)
    _hears_move = movement_hears_predicate(
        presence, room, dest, game, _presence_hears(presence),
    )
    # Skip the mover and (when different) the named host so a possessed
    # body does not hear third-person "PulseHost leaves north" about itself.
    exclude = character if presence is character else (character, presence)

    def _mover_face(watcher):
        """Per-watcher leave/arrive name (hood / unintroduced / vessel)."""
        face = presence_face_for(watcher, presence)
        return face or fallback_face

    import time as _time
    from engine import lag_watch

    t_leave = _time.perf_counter()
    before_move_crossing(character, room, direction, dest, game)
    if not stealth:
        room.broadcast(
            lambda w: move_leave_line(
                _mover_face(w), direction, presence,
            ),
            exclude=exclude,
            predicate=_hears_move,
        )
    lag_watch.stamp_move_phase(game, "broadcast", t_leave)
    # Leaving a job site ends an active gig-work shift (checked below, via
    # after_arrive, once we know whether the NEW room is also a work site).
    was_working = getattr(character, "working", False)
    # Moving always ends an online training montage (unlike work, which
    # only ends when you leave the job site -- training is not room-bound).
    train_msg = before_relocate(character)
    if train_msg and character.session:
        character.session.send(train_msg)
    character.move_to(dest)
    from engine.systems import combat_pursuit as combat_pursuit_mod
    combat_pursuit_mod.notify_character_relocated(
        character, room, dest, game,
    )
    # Game-specific post-arrival effects: stop work if the job site was
    # left behind, drag a carried body along via cadence, and the lodging
    # owner-walks-in-on-a-stranger check.
    t_after = _time.perf_counter()
    after_arrive(character, dest, game, was_working)
    lag_watch.stamp_move_phase(game, "after_arrive", t_after)
    # Occupancy GMCP is additive HUD. Never let a DTO fault skip arrive
    # broadcast, auto-look, or follower pull (body already in dest).
    try:
        from engine import gmcp as gmcp_mod
        gmcp_mod.push_occupants_for_room(room)
        gmcp_mod.push_occupants_for_room(dest)
    except Exception as exc:
        print(f"[move] occupants gmcp skipped: {exc!r}", flush=True)
    # A body heaved onto your shoulder (cmd_heave) travels with you, exactly
    # like the gravedigger NPC carrying a corpse to the plot -- after_arrive
    # above already moved it through cadence.move_body so any spirit's
    # body_room stays in sync; this just picks the right broadcast wording.
    carried = getattr(character, "_carrying_body", None)
    t_arrive = _time.perf_counter()
    if not stealth:
        if carried is not None:
            dest.broadcast(
                lambda w: move_arrive_line(
                    _mover_face(w), direction, presence, carried=carried,
                ),
                exclude=exclude,
                predicate=_hears_move,
            )
        else:
            dest.broadcast(
                lambda w: move_arrive_line(
                    _mover_face(w), direction, presence,
                ),
                exclude=exclude,
                predicate=_hears_move,
            )
    lag_watch.stamp_move_phase(game, "broadcast", t_arrive)
    # Auto-close (and re-lock) after arrive so destination watchers see
    # open → walk in → close, not close before the threshold line (bug 1749).
    after_move_crossing(character, room, direction, dest, game)
    # Opt-in combat-pit pose feed + hygiene exposure + stealth stamps run
    # after leave/arrive broadcasts so state-swap room tells (naked, filthy,
    # …) never precede the walk-in line (bug report 1372).
    after_move_step(character, direction, dest, game)
    # Local import: cmd_look now lives in engine.verbs.basic, a different
    # package from this shared-helper module -- lazy avoids a module-level
    # cross-package import, same reasoning as every other import here.
    from engine.verbs.basic import cmd_look
    # Echo / idle companions pulled along have no need for auto-look
    # (and may have session None -- offline Echo).
    # Multi-hop walk passes auto_look=False so only the final room (or an
    # early stop) gets a full look -- encounters still fire below.
    # SilentSession (Cadence ``npc_do``) also skips auto-look: town
    # pathfind.step uses npc_do for immersion broadcasts, but building
    # look UI into a sink froze the asyncio loop on every hop.
    session = character.session
    t_look = _time.perf_counter()
    if (
        auto_look
        and session is not None
        and not getattr(session, "silent", False)
    ):
        cmd_look(character, "", game, after_move=True)
    lag_watch.stamp_move_phase(game, "look", t_look)
    t_enc = _time.perf_counter()
    encounter_check(game, dest)   # roll AFTER the look, not before --
    # a dungeon-reveal/hostile-spawn message narrating something happening
    # in the room should land once the player has already seen the room
    # itself, not get buried above the room description they haven't read
    # yet (live player report). When auto_look is False the spawn line
    # still arrives; walk stops and looks if combat engages.
    lag_watch.stamp_move_phase(game, "encounter", t_enc)


def _would_create_follow_cycle(follower, leader):
    """True when bonding follower -> leader would close a follow ring."""
    seen = set()
    cur = leader
    while cur is not None:
        key = getattr(cur, "key", None)
        if key is not None:
            if key in seen:
                return True
            seen.add(key)
        if cur is follower:
            return True
        cur = getattr(cur, "following", None)
    return False


def start_following(follower, leader):
    """Bond `follower` to trail `leader` (Cadence-safe; no Session needed).

    Same list/pointer rules as the player `follow` verb. Idempotent when
    the bond is already set. Returns True when a (new or existing) bond
    to `leader` is in place, False if the args are invalid.
    """
    if follower is None or leader is None or follower is leader:
        return False
    if getattr(follower, "following", None) is leader:
        return True
    if _would_create_follow_cycle(follower, leader):
        return False
    stop_following(follower, silent=True)
    follower.following = leader
    followers = getattr(leader, "followers", None)
    if followers is None:
        leader.followers = [follower]
    elif follower not in followers:
        followers.append(follower)
    leader_key = getattr(leader, "key", None)
    if leader_key:
        follower.following_leader_key = str(leader_key).strip()
    from engine import group as group_mod

    group_mod.clear_follow_declined_for(follower, leader)
    return True


def stop_following(follower, silent=False, *, declined=False, game=None):
    """Clear `follower`'s follow bond. Safe with no Session (Cadence / Echo).

    When `silent` is False and the follower has a live Session, send the
    usual "you stop following" line (player unfollow / bare follow).

    ``declined=True`` stamps ``follow_declined_leader_key`` so Cadence pack /
    companion glue does not immediately re-bond (bug report 1574).
    """
    if follower is None:
        return
    target = getattr(follower, "following", None)
    if target is None:
        if not silent and getattr(follower, "session", None) is not None:
            follower.session.send("You aren't following anyone.")
        return
    followers = getattr(target, "followers", None) or []
    if follower in followers:
        followers.remove(follower)
    follower.following = None
    follower.following_leader_key = None
    # Opaque SUPERS beckon-companion marker (supers/companion.py) -- clear
    # when the follow bond drops so duty does not outlive the trail.
    if getattr(follower, "companion_leader_key", None) is not None:
        follower.companion_leader_key = None
    if declined and target is not None:
        from engine import group as group_mod

        group_mod.record_follow_declined(follower, target, game=game)
    if not silent and getattr(follower, "session", None) is not None:
        follower.session.send(f"You stop following {target.key}.")


def start_staff_tail(tailer, target):
    """Bond a staff GM to trail `target` for diagnosis only.

    Uses ``staff_tailing`` / ``staff_tailers`` -- never ``following`` /
    ``followers`` -- so Group, pack convoy, beckon duty, and ``(Group)``
  look tags stay untouched. Session-only; not persisted.
    """
    if tailer is None or target is None or tailer is target:
        return False
    if not _is_gm(tailer):
        return False
    if getattr(tailer, "staff_tailing", None) is target:
        return True
    stop_staff_tail(tailer, silent=True)
    stop_following(tailer, silent=True)
    tailer.staff_tailing = target
    tailers = getattr(target, "staff_tailers", None)
    if tailers is None:
        target.staff_tailers = [tailer]
    elif tailer not in tailers:
        tailers.append(tailer)
    return True


def stop_staff_tail(tailer, silent=False):
    """Clear a staff diagnostic tail. Safe with no Session."""
    if tailer is None:
        return
    target = getattr(tailer, "staff_tailing", None)
    if target is None:
        if not silent and getattr(tailer, "session", None) is not None:
            tailer.session.send("You aren't tailing anyone.")
        return
    tailers = getattr(target, "staff_tailers", None) or []
    if tailer in tailers:
        tailers.remove(tailer)
    tailer.staff_tailing = None
    label = _public_label(target)
    if not silent and getattr(tailer, "session", None) is not None:
        tailer.session.send(f"You stop tailing {label}.")


def _pull_staff_tailers(leader, origin, direction, game, dest=None):
    """Move staff GMs tailing `leader` with the same step (no group/pack)."""
    if dest is None and origin is not None and direction:
        dest = origin.exits.get(direction)
    for tailer in list(getattr(leader, "staff_tailers", None) or []):
        if getattr(tailer, "staff_tailing", None) is not leader:
            continue
        if tailer.location is not origin or getattr(tailer, "spirit", False):
            continue
        if dest is None:
            continue
        if move_gate_block(tailer, origin, dest, game):
            if tailer.session is not None:
                tailer.session.send(
                    "[Staff] Tail stopped -- that exit is blocked for you."
                )
            stop_staff_tail(tailer, silent=True)
            continue
        _move_one(tailer, direction or "in", dest, game)


def _pull_followers(leader, origin, direction, game):
    """Move everyone trailing `leader` the same compass exit."""
    dest = None
    if origin is not None and direction:
        dest = origin.exits.get(direction)
    _pull_followers_to(leader, origin, dest, game, direction=direction)


def _pull_followers_to(leader, origin, dest, game, *, direction="in"):
    """Move everyone trailing `leader` (and, transitively, everyone trailing
    THEM) to *dest* -- suggestions.log 44 plus zone enter/exit.

    Compass ``cmd_move`` looks dest up from ``origin.exits``. ``enter`` /
    ``exit`` / ``in`` / ``out`` pass the dest room directly so castle
    mouths and portal pockets pull the party instead of leaving mates
    at the threshold.

    Walked breadth-first from a plain list used as a queue, with a `moved`
    id-set guard: `following` is a single pointer but nothing stops two
    characters from following each other, so without a visited guard a
    two-cycle would pull each other back and forth forever.

    Pulls online players **and** Echo / idlemode companions (`acts_as_echo`)
    still standing in the leader's ORIGIN room -- Cadence hunt partners
    (e.g. Echo Sam trailing Dean) must walk and board together. Ordinary
    spirits still never pull (genuine discorporate linger). A Mantle whose
    ``vessel_host_key`` is the leader (possession rider, not a generic
    spirit follower) does pull so ``enter homestead`` does not leave them
    on the yard. Anyone who already left some other way is left alone.
    """
    if leader is None or origin is None or dest is None:
        return
    moved = {id(leader)}
    queue = list(getattr(leader, "followers", None) or [])
    while queue:
        follower = queue.pop(0)
        if id(follower) in moved:
            continue
        moved.add(id(follower))
        if follower.spirit:
            # Possession-only: host walked into a pocket while the riding
            # Mantle spirit still stands on the yard cell. Do not pull every
            # discorporate linger -- only this host↔rider link (1506).
            rider_host = getattr(follower, "vessel_host_key", None)
            if not (
                rider_host
                and rider_host == getattr(leader, "key", None)
            ):
                continue
        # Cars sit in a cabin Room, not the street. Count them as "here"
        # when their ride is still parked (or overlanding) at origin.
        if getattr(follower, "location", None) is not origin and (
            not follow_shares_origin(follower, origin, game)
            and not follow_pull_homestead_plot(
                follower, leader, origin, dest, game,
            )
        ):
            continue
        # Own living vessel husks must never trail the Mantle via follow
        # pulls -- a desynced vacant shell reads as a naked NPC (826).
        # SUPERS stamps is_vessel_husk on designed husks; bare engine
        # never sets it, so this is a no-op without importing supers.
        try:
            if getattr(follower, "is_vessel_husk", False):
                owner = getattr(follower, "vessel_owner_key", None)
                if owner and owner == getattr(leader, "key", None):
                    stop_following(follower, silent=True)
                    continue
        except Exception:
            _log_hook_error("husk follow peel", getattr(follower, "key", None))
        # Live followers peel off when survival meters spike -- do not drag
        # Sam past bunk/gym while Dean walks (bug report 464).
        if getattr(follower, "session", None) is not None:
            peel_msg = follow_survival_peel_message(follower, leader)
            if peel_msg:
                stop_following(follower, silent=True)
                follower.session.send(peel_msg)
                continue
        # SUPERS pack: follower on their own haunt/mission must not be
        # yanked when the companion leader walks for food (hooked).
        if follow_pull_skip(follower, leader, game):
            continue
        # Boarded followers: park their own ride at dest (horse enter
        # sunrise) instead of yanking the rider off the saddle.
        if follow_pull_handled(follower, origin, dest, game):
            queue.extend(list(getattr(follower, "followers", None) or []))
            continue
        # Household pets trail owners through homestead porch exits and
        # yard gates (can_enter_home), not Lebanon town gateways.
        if follow_pull_household_pet(follower, leader, origin, dest, game):
            queue.extend(list(getattr(follower, "followers", None) or []))
            continue
        # Same gates a manual move would hit (jail cells, hunter-safe
        # sanctuaries, ...) -- immersion parity (AGENTS.md rule 9): a
        # follower being dragged along must not slip through a gate that
        # would have stopped them walking there on their own. Uses the same
        # move_gate_block hook cmd_move itself calls, so this stays
        # supers-agnostic (Phase 2b) instead of importing supers.slayer
        # directly the way this helper used to.
        if move_gate_block(follower, origin, dest, game):
            if follower.session is not None:
                follower.session.send(
                    "Something in you recoils -- that place is claimed "
                    "by the night. You stop following rather than "
                    "trespass."
                )
            continue
        hub_key = getattr(leader, "zone_entry_hub_key", None)
        if hub_key:
            follower.zone_entry_hub_key = hub_key
        _move_one(follower, direction or "in", dest, game)
        queue.extend(list(getattr(follower, "followers", None) or []))
    _pull_staff_tailers(leader, origin, direction or "in", game, dest=dest)
    # Party must share a room -- disband stragglers the pull could not move.
    try:
        from engine import group as group_mod
        group_mod.validate_group_colocation(leader, game)
    except Exception:
        # HB-56: a failed colocation check used to leave stragglers
        # grouped across rooms with no log.
        import traceback
        print("[group] colocation check failed after follow-pull:", flush=True)
        traceback.print_exc()


_ITEM_NAME_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _item_text_tokens(text):
    """Lowercase word tokens from an item key or alias string."""
    return [
        tok
        for tok in _ITEM_NAME_TOKEN_SPLIT.split(str(text or "").lower())
        if tok
    ]


def _item_name_matches(needle, item):
    """True when ``needle`` matches an item key or alias at word boundaries.

  Multi-word queries must appear as a contiguous phrase bounded by
  non-alphanumerics (``kit bag`` in ``a worn kit bag``). Single-token
  queries prefix-match whole words only (``kit`` -> kit bag; ``w`` does
  not match ``worn``). One-letter queries must equal a whole token.
  """
    if not needle:
        return False
    needle = needle.strip().lower()
    if not needle:
        return False
    haystacks = [item.key]
    haystacks.extend(getattr(item, "aliases", ()) or ())
    for raw in haystacks:
        text = str(raw).lower()
        if " " in needle:
            pat = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
            if re.search(pat, text):
                return True
            continue
        tokens = _item_text_tokens(text)
        if not tokens:
            continue
        if len(needle) == 1:
            if needle in tokens:
                return True
            continue
        if any(tok.startswith(needle) for tok in tokens):
            return True
    return False


def drop_item_refusal(character, item):
    """Return a refuse string when ``item`` may not be dropped, else None."""
    from engine.hooks import containers_item_worn_on_body, item_drop_refusal

    refuse = item_drop_refusal(character, item)
    if refuse:
        return refuse
    if containers_item_worn_on_body(character, item):
        return (
            "You have that on your body -- remove or unequip it "
            "before dropping it."
        )
    return None


def _collect_item_matches(query, items):
    """All items whose key or alias contains ``query`` (inventory order).

    Ordinals are peeled by ``_find_item`` / ``_find_item_prefer_locked``;
    pass the remaining name fragment here.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []
    return [item for item in items if _item_name_matches(needle, item)]


# Classic MUD bulk: ``all``, ``all.sword``, ``all sword``, ``*.coin``.
# Digit ordinals (``2.backpack``) stay with parse_target_ordinal.
_BULK_ITEM_QUERY_RE = re.compile(
    r"^(all|\*|everything)(?:[.\s]+(.+))?$",
    re.IGNORECASE,
)

# Counted move: ``10 salt`` / bare ``10``. A dotted first token is an
# ordinal (``2.pistol``), never a count.
_QTY_ITEM_QUERY_RE = re.compile(r"^(\d+)\s+(.+)$")
_QTY_ITEM_CAP = 200


def parse_qty_item_query(query):
    """Return ``(count, name_fragment)`` for counted put/get, else ``(None, query)``.

    ``10 salt`` is ten matching items. Bare ``10`` is ten of whatever is
    there (first rows in the bag or loose inventory). ``2.pistol`` stays
    an ordinal -- the dotted first token is never treated as a count.
    Caps at 200 so a typo cannot walk the whole catalog.
    """
    text = (query or "").strip()
    if not text:
        return None, text
    first = text.split(None, 1)[0]
    if "." in first:
        return None, text
    if text.isdigit():
        n = int(text)
        if n < 1:
            return None, text
        return min(n, _QTY_ITEM_CAP), ""
    match = _QTY_ITEM_QUERY_RE.match(text)
    if not match:
        return None, text
    n = int(match.group(1))
    if n < 1:
        return None, text
    return min(n, _QTY_ITEM_CAP), match.group(2).strip()


def format_item_tally(names):
    """Condense repeated item keys: scrap, scrap, scrap -> scrap (x3).

    Used on put/get/craft bulk tells so twenty identical pieces do not
    print twenty times (suggestion report 324).
    """
    from collections import OrderedDict

    counts = OrderedDict()
    for raw in names:
        key = (raw or "?").strip() or "?"
        counts[key] = counts.get(key, 0) + 1
    parts = []
    for name, n in counts.items():
        if n > 1:
            parts.append(f"{name} (x{n})")
        else:
            parts.append(name)
    return ", ".join(parts)


def parse_bulk_item_query(query):
    """Return ``('all', name_fragment)`` for Classic bulk forms, else ``(None, query)``.

    ``name_fragment`` is empty for plain ``all`` / ``*`` / ``everything``.
    Does not consume ``2.item`` ordinals.
    """
    text = (query or "").strip()
    if not text:
        return None, text
    if text[:1].isdigit() and "." in text:
        return None, text
    match = _BULK_ITEM_QUERY_RE.match(text)
    if not match:
        return None, text
    fragment = (match.group(2) or "").strip()
    return "all", fragment


def _find_item(query, items, character=None):
    """Return an Item whose key (or aliases) contains ``query``.

    Case-insensitive substring match. Supports ``2.pistol`` / ``other
    pistol`` ordinals (bug report 126: duplicate names with one equipped).
    When ``character`` is passed and several items match without an ordinal,
    the game hook ``inventory_item_match_rank`` sorts candidates so carried
    (not worn) copies win over equipped duplicates.

    Staff INUM tags (``AD00001``) are **not** matched here -- players must
    never target instance numbers. Use :func:`find_staff_item_by_inum`.
    """
    from engine.char_identity import parse_target_ordinal, pick_ordinal

    ordinal, rest = parse_target_ordinal(query)
    needle = rest if rest else (query or "")
    matches = _collect_item_matches(needle, items)
    if not matches:
        return None
    if ordinal is not None:
        return pick_ordinal(matches, ordinal)
    if len(matches) > 1 and character is not None:
        from engine import hooks
        matches = sorted(
            matches,
            key=lambda item: hooks.inventory_item_match_rank(character, item),
        )
    return matches[0]


def find_staff_item_by_inum(character, query, *, room=None, can_see=True):
    """Exact INUM lookup for staff look / inspect. Players get None.

    Searches carried inventory (including nested bags) and, when the
    viewer can see, floor items in ``room``. Never a player targeting path.
    """
    from engine.item_inum import parse_inum, persist_inum_text, walk_item_tree
    from engine.world import Item

    if not staff_room_vnums_in_look(character):
        return None
    if not parse_inum(query):
        return None
    want = str(query).strip().upper()
    pool = []
    inv = getattr(character, "inventory", None) or [] if character else []
    pool.extend(inv)
    gear = getattr(character, "gear_bag", None) or [] if character else []
    pool.extend(gear)
    if can_see and room is not None:
        pool.extend(
            obj for obj in getattr(room, "contents", ()) or ()
            if isinstance(obj, Item)
        )
    for obj in pool:
        if not isinstance(obj, Item):
            continue
        for node in walk_item_tree(obj):
            if persist_inum_text(node) == want:
                return node
    return None


def _find_item_prefer_locked(query, items, character=None):
    """Like _find_item, but when several keys match, pick a locked container
    first (bug_reports.log #21: a leftover flavor strongbox sitting next to a
    real lockbox made `open strongbox` hit the wrong one and say "isn't
    locked")."""
    from engine.char_identity import parse_target_ordinal, pick_ordinal

    ordinal, rest = parse_target_ordinal(query)
    needle = rest if rest else (query or "")
    matches = _collect_item_matches(needle, items)
    if not matches:
        return None
    if ordinal is not None:
        return pick_ordinal(matches, ordinal)
    if len(matches) > 1 and character is not None:
        from engine import hooks
        matches = sorted(
            matches,
            key=lambda item: hooks.inventory_item_match_rank(character, item),
        )
    for item in matches:
        if item.locked:
            return item
    return matches[0]


def _find_carried_item_prefer_locked(character, query):
    """Return ``(item, holder)`` for ``open`` / force-unlock targeting.

    Searches open inventory, worn loot-bag contents, and the gear kit bag --
    same places ``get … from backpack`` can reach. ``holder`` is the mutable
    list ``cmd_open`` must ``remove`` from (inventory row or ``bag_contents``).
    """
    from world import Item
    from engine.systems import containers as containers_mod

    if character is None:
        return None, None

    def _pick(items, holder):
        found = _find_item_prefer_locked(query, items, character=character)
        if found is not None:
            return found, holder
        return None, None

    inv = getattr(character, "inventory", None) or []
    inv_items = [obj for obj in inv if isinstance(obj, Item)]
    item, holder = _pick(inv_items, inv)
    if item is not None:
        return item, holder

    for bag in containers_mod.worn_bags(character):
        contents = containers_mod.bag_contents(bag)
        nested = [obj for obj in contents if isinstance(obj, Item)]
        item, holder = _pick(nested, contents)
        if item is not None:
            return item, holder

    from engine import hooks
    gear = hooks.containers_ensure_gear_bag(character)
    if gear:
        nested = [obj for obj in gear if isinstance(obj, Item)]
        item, holder = _pick(nested, gear)
        if item is not None:
            return item, holder

    return None, None


def _open_container_rank(item):
    """Sort key for ``open``: picked lockboxes before sealed ones (bug report 1057).

    When several strongboxes match the same keyword, ``lockpick`` may unlock
    one row while ``open`` used to prefer another still-locked copy and print
    "force open" even though the player just picked a lock. Prefer an unlocked
    box that still carries loot (picked, ready to open), then locked boxes
    (force-open framing), then legacy flavor rows with no loot yet (bug
    report 21).
    """
    from world import Item

    if not isinstance(item, Item):
        return 3
    loot = getattr(item, "loot", None)
    has_loot = bool(loot)
    locked = bool(getattr(item, "locked", False))
    if not locked and has_loot:
        return 0
    if locked and has_loot:
        return 1
    if not locked and not has_loot:
        return 2
    return 3


def _find_item_for_open(query, items, character=None):
    """Like ``_find_item``, but when several keys match prefer a picked
    lockbox (unlocked + loot) over a still-sealed duplicate."""
    from engine.char_identity import parse_target_ordinal, pick_ordinal

    ordinal, rest = parse_target_ordinal(query)
    needle = rest if rest else (query or "")
    matches = _collect_item_matches(needle, items)
    if not matches:
        return None
    if ordinal is not None:
        return pick_ordinal(matches, ordinal)
    if len(matches) > 1:
        if character is not None:
            from engine import hooks

            matches = sorted(
                matches,
                key=lambda item: (
                    _open_container_rank(item),
                    hooks.inventory_item_match_rank(character, item),
                ),
            )
        else:
            matches = sorted(matches, key=_open_container_rank)
    return matches[0]


def _find_carried_item_for_open(character, query):
    """Return ``(item, holder)`` for ``cmd_open`` container targeting.

    Same search order as ``_find_carried_item_prefer_locked`` (inventory,
    worn loot bags, gear bag) but prefers an unlocked strongbox the player
    just picked over a still-locked duplicate in another pocket.
    """
    from world import Item
    from engine.systems import containers as containers_mod

    if character is None:
        return None, None

    def _pick(items, holder):
        found = _find_item_for_open(query, items, character=character)
        if found is not None:
            return found, holder
        return None, None

    inv = getattr(character, "inventory", None) or []
    inv_items = [obj for obj in inv if isinstance(obj, Item)]
    item, holder = _pick(inv_items, inv)
    if item is not None:
        return item, holder

    for bag in containers_mod.worn_bags(character):
        contents = containers_mod.bag_contents(bag)
        nested = [obj for obj in contents if isinstance(obj, Item)]
        item, holder = _pick(nested, contents)
        if item is not None:
            return item, holder

    from engine import hooks

    gear = hooks.containers_ensure_gear_bag(character)
    if gear:
        nested = [obj for obj in gear if isinstance(obj, Item)]
        item, holder = _pick(nested, gear)
        if item is not None:
            return item, holder

    return None, None


# Classic MUD self-target tokens -- never search the world for these names.
SELF_NAME_ALIASES = frozenset({"me", "self", "myself"})


def is_self_name(query):
    """True when `query` is me / self / myself (case-insensitive)."""
    return (query or "").strip().lower() in SELF_NAME_ALIASES


def is_linked_self(actor, other):
    """True when `other` is `actor`, or their GM-form linked body/spirit.

    ``gm on`` leaves the mortal body as a separate Character (Echo) while
    the Session pilots ``gmspirit:…``. Staff verbs that refuse self-target
    (snoop / hakai / gmslay) must treat that husk as the same person.
    """
    if actor is None or other is None:
        return False
    if actor is other:
        return True
    # Spirit -> left-behind body (live pointer, then key fallback).
    body = getattr(actor, "gm_mode_body", None)
    if body is not None and body is other:
        return True
    body_key = getattr(actor, "gm_body_key", None)
    if body_key and getattr(other, "key", None) == body_key:
        return True
    # Body -> its active GM spirit (reverse pointer / key).
    if getattr(other, "gm_mode_body", None) is actor:
        return True
    spirit_key = getattr(actor, "gm_spirit_key", None)
    if spirit_key and getattr(other, "key", None) == spirit_key:
        return True
    other_spirit_key = getattr(other, "gm_spirit_key", None)
    if other_spirit_key and getattr(actor, "key", None) == other_spirit_key:
        return True
    return False


def _viewport_staff_body_pref(game, query):
    """Prefer the Ash viewport play body over world ``Ash`` name collisions.

    Crashgate viewport opens as staff account Ash. ``gm force Ash`` /
    ``summon Ash`` / ``gm stats Ash`` must hit the staff-login Roadhouse
    fixture, not a player Angel named Ash Riley (bug report 1213).
    """
    raw = (query or "").strip()
    if not raw or game is None:
        return None
    hub = getattr(game, "viewport_hub", None)
    session = getattr(hub, "session", None) if hub is not None else None
    if session is None or not getattr(session, "alive", True):
        return None
    staff = (getattr(session, "staff_account", None) or "").strip()
    if not staff:
        return None
    low = raw.lower()
    if low not in (staff.lower(), "ash"):
        return None
    from engine.accounts import resolve_staff_login_cast

    fixture = resolve_staff_login_cast(game, "Ash")
    if fixture is not None:
        return fixture
    char = getattr(session, "character", None)
    if char is None:
        return None
    body = getattr(char, "gm_mode_body", None)
    if body is not None:
        return body
    return char


def resolve_gm_target_character(game, query):
    """Resolve a staff target token to a Character world-wide.

    GM verbs (``snoop``, ``goto``, ``stats``, ``force``, …) accept account
    login names as well as character keys and presence faces. When staff
    type an account name, prefer the **online** session character on that
    account; otherwise fall back to the first playable roster body (Echo /
    offline).

    Resolution order:
      0. Active Ash viewport: staff-login fixture, then occupy body
      1. ``game.find_character`` -- keys, faces, NPCs, Echoes, ordinals
      2. ``find_login_character`` -- bare roster keys / unique given names
      3. Account name → online session character on that account
      4. Account name → first playable roster body (Echo / offline)
    """
    raw = unwrap_name_quotes(query)
    if not raw or game is None:
        return None

    pref = _viewport_staff_body_pref(game, raw)
    if pref is not None:
        return pref

    char = game.find_character(raw)
    if char is not None:
        return char

    login_finder = getattr(game, "find_login_character", None)
    if callable(login_finder):
        char = login_finder(raw)
        if char is not None:
            return char

    from engine.accounts import account_lookup_key, find_account, normalize_account_name

    acct = find_account(game, raw)
    if acct is None:
        cleaned, err = normalize_account_name(raw)
        if not err:
            acct = find_account(game, cleaned)
    if acct is None:
        low = raw.lower()
        for candidate in (getattr(game, "accounts", None) or {}).values():
            display = (getattr(candidate, "display_name", None) or "").strip()
            name = (getattr(candidate, "name", None) or "").strip()
            if display.lower() == low or name.lower() == low:
                acct = candidate
                break
    if acct is None:
        return None

    want_key = account_lookup_key(acct.name)
    for session in list(getattr(game, "sessions", None) or []):
        character = getattr(session, "character", None)
        if character is None or getattr(character, "is_npc", False):
            continue
        char_acct = (getattr(character, "account", None) or "").strip()
        if account_lookup_key(char_acct) == want_key:
            return character

    for key in list(acct.character_keys):
        body = login_finder(key) if callable(login_finder) else None
        if body is None:
            body = game.find_character(key)
        if body is None or getattr(body, "is_npc", False):
            continue
        if getattr(body, "immersion", False):
            continue
        return body

    return None


def resolve_named_character(actor, query, game=None, candidates=None, *, staff_world=False):
    """Resolve a typed name to a Character; me/self/myself always means `actor`.

    Prefer this over bare ``game.find_character`` whenever the typer can
    target themselves (GM ``set me …``, ``stats me``, room look-alikes).

    Supports ordinals: ``2.carl``, ``other carl``, ``second carl``.
    Supports shell quotes: ``'Earl Jacobs'`` resolves as one name.

    When ``staff_world`` is True (GM inspect / moderation verbs), account
    login names also resolve via ``resolve_gm_target_character``.

    Resolution order:
      1. Empty query -> None
      2. Self alias -> ``actor`` (never a world name match)
      3. ``candidates`` list via ``_find_character`` when provided
      4. Else ``game.find_character(query)`` when ``game`` is provided
      5. When ``staff_world``, account name → online player / roster body
    """
    raw = collapse_shell_args(query)
    if not raw:
        return None
    if is_self_name(raw):
        return actor
    if candidates is not None:
        return _find_character(raw, candidates, self_character=actor)
    if staff_world and game is not None:
        return resolve_gm_target_character(game, raw)
    finder = getattr(game, "find_character", None) if game is not None else None
    if finder is not None:
        return finder(raw)
    return None


def resolve_fight_target(
    actor,
    query,
    candidates,
    *,
    usage_msg=None,
    missing_msg="You don't see them here.",
    clear_stale_target=True,
):
    """Resolve a combat-verb target from typed args or the current fight.

    When ``query`` is empty, returns ``actor.target`` if still co-located.
    Clears a stale ``actor.target`` when the partner left the room.
    Returns ``(target, error_message)``; on success the error is ``None``.
    """
    raw = (query or "").strip()
    pool = _combat_target_candidates(actor, candidates)
    if raw:
        target = _find_character(raw, pool, self_character=actor)
        if target is None:
            return None, missing_msg
        if not _can_target_for_combat(actor, target):
            return None, missing_msg
        return target, None

    target = getattr(actor, "target", None)
    if target is None:
        return None, usage_msg or "You need a target."
    room = getattr(actor, "location", None)
    if getattr(target, "location", None) is not room:
        if clear_stale_target:
            actor.target = None
        gone = _public_label(target)
        return None, f"{gone} is no longer here."
    if not _can_target_for_combat(actor, target):
        if clear_stale_target:
            actor.target = None
        return None, missing_msg
    return target, None


def verb_target_token(actor, target, game=None):
    """Build an npc_do / snoop-safe target string that resolves to ``target``.

    Cadence planners already picked the Character object; this turns it into
    a command token without ``husk:`` / ``gmspirit:`` storage keys. Labels
    use given name, ``First Last``, or presence face -- never a mashed login
    key like ``ZackMarkson``. When several co-located bodies share the same
    label, prefixes an ordinal (``2.Zack``) so dispatch still hits the prey.
    """
    from engine.char_identity import (
        character_given_name,
        character_surname,
        humanize_storage_key,
        legal_public_name,
        surname_is_targetable,
    )

    def _humanize_if_mash(label, storage_key):
        """Turn ``ZackMarkson`` into ``Zack Markson`` when label is the mash."""
        peeled = strip_ephemeral_storage_prefix(storage_key or "")
        text = (label or "").strip()
        if not text:
            return humanize_storage_key(peeled)
        if peeled and text.lower() == peeled.lower():
            return humanize_storage_key(peeled)
        return text

    def _verb_target_labels(subject):
        """Ordered player-facing labels (most specific first)."""
        labels = []
        storage_key = getattr(subject, "key", None)
        peeled = strip_ephemeral_storage_prefix(storage_key or "")

        face = _presence_face(subject)
        if face and face != "?":
            labels.append(_humanize_if_mash(face, storage_key))

        given = character_given_name(subject).strip()
        sur = character_surname(subject).strip()
        if given and sur and surname_is_targetable(subject):
            labels.append(f"{given} {sur}")
        elif given:
            labels.append(_humanize_if_mash(given, storage_key))
        pub = legal_public_name(subject)
        if pub and pub != "?":
            labels.append(_humanize_if_mash(pub, storage_key))
        if peeled:
            labels.append(humanize_storage_key(peeled))

        seen = set()
        ordered = []
        for label in labels:
            clean = (label or "").strip()
            if not clean:
                continue
            key = clean.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(clean)
        return ordered

    if not isinstance(target, Character):
        return humanize_storage_key(strip_ephemeral_storage_prefix(target))

    labels = _verb_target_labels(target)
    fallback = labels[0] if labels else humanize_storage_key(
        getattr(target, "key", None),
    )
    room = getattr(actor, "location", None)
    if room is None:
        return fallback
    candidates = [
        c for c in room.characters()
        if c is not actor and isinstance(c, Character)
    ]
    for token in labels:
        matches = _collect_character_matches(
            token, candidates, self_character=actor,
        )
        if target not in matches:
            continue
        if len(matches) == 1:
            return token
        matches = sorted(
            matches,
            key=lambda ch: (getattr(ch, "key", "") or "").lower(),
        )
        idx = matches.index(target) + 1
        return f"{idx}.{token}"
    return fallback


def _collect_character_matches(query, characters, self_character=None):
    """All Characters whose key / given / face / kind aliases contain ``query``.

    Kind/race aliases (``arachne``, ``vampire``, …) come from the
    ``extra_target_match_needles`` hook when the game registers one --
    bare engine stays name-only.
    """
    from engine.char_identity import identity_match_needles

    needle = (query or "").strip().lower()
    if not needle:
        return []
    hits = []
    for char in characters:
        matched = False
        # Unpierced archangel deep cover: only the mask (Loki) matches,
        # not the true key or archangel kit tokens (bug report 1278).
        if (
            getattr(char, "deep_cover_active", False)
            and self_character is not None
        ):
            try:
                from engine.hooks import presence_face_for
                mask = presence_face_for(self_character, char)
            except Exception:
                mask = None
                _log_hook_error(
                    "presence_face_for (cover)",
                    getattr(char, "key", None),
                )
            true_key = (getattr(char, "key", None) or "").lower()
            if mask and true_key and true_key not in str(mask).lower():
                if needle in str(mask).lower():
                    matched = True
                if matched and char not in hits:
                    hits.append(char)
                continue
        for label in identity_match_needles(char):
            if needle in label:
                matched = True
                break
        if not matched and self_character is not None:
            try:
                from engine.hooks import presence_face_for
                rel = presence_face_for(self_character, char)
                if rel and needle in rel.lower():
                    matched = True
            except Exception:
                _log_hook_error(
                    "presence_face_for (find)",
                    getattr(char, "key", None),
                )
        # Origin/Path/kind tokens (game-owned). Viewer may be None when a
        # call site omitted self_character -- obvious hostiles still match.
        if not matched:
            try:
                from engine.hooks import extra_target_match_needles
                for label in extra_target_match_needles(self_character, char):
                    if needle in label:
                        matched = True
                        break
            except Exception:
                _log_hook_error(
                    "extra_target_match_needles (find)",
                    getattr(char, "key", None),
                )
        if matched and char not in hits:
            hits.append(char)
    return hits


def sort_character_target_matches(matches):
    """Stable target order for ordinals and staff disambiguation.

    God Mantle bodies sort before their bilocated ``twin:`` husks so
    ``gary`` / ``2.gary`` / ``other gary`` always mean mantle / twin /
    twin (bug report 214). Remaining ties break on storage key.
    """
    if not matches:
        return []

    def _sort_key(ch):
        is_twin = bool(getattr(ch, "god_twin", False))
        is_dream = bool(getattr(ch, "is_sleep_dream_clone", False))
        key = (getattr(ch, "key", "") or "").lower()
        return (1 if is_twin or is_dream else 0, key)

    return sorted(matches, key=_sort_key)


def _resolve_god_twin_owner(target, game=None):
    """Return the owning Mantle for a bilocated twin, when resolvable."""
    if target is None or not getattr(target, "god_twin", False):
        return None
    owner_key = getattr(target, "god_twin_owner_key", None)
    if not owner_key:
        return None
    if game is not None:
        finder = getattr(game, "find_character", None)
        if callable(finder):
            owner = finder(owner_key)
            if owner is not None:
                return owner
    rooms = getattr(game, "rooms", None) if game is not None else None
    if rooms:
        for room in rooms.values():
            for ch in room.characters():
                if getattr(ch, "key", None) == owner_key:
                    return ch
    return None


def staff_snoop_face(target, game=None):
    """Staff snoop tag: public face plus mantle/twin when omnipresent.

    Bilocated twins borrow the owner's legal name and append ``(twin)`` so
    GMs can tell ``snoop Gary`` from ``snoop 2.gary``. Active Mantles with
    a standing twin append ``(mantle)`` for the same reason.
    """
    if target is None:
        return "?"
    if getattr(target, "god_twin", False):
        owner = _resolve_god_twin_owner(target, game)
        base = _public_label(owner if owner is not None else target)
        return f"{base} (twin)"
    if getattr(target, "god_twin_key", None):
        return f"{_public_label(target)} (mantle)"
    return _public_label(target)


def staff_snoop_kind(target):
    """Short kind tag for ``snoop`` confirmation lines."""
    if getattr(target, "god_twin", False):
        return "twin"
    if getattr(target, "gm_spirit", False):
        return "GM"
    if getattr(target, "is_npc", False):
        return "NPC"
    if getattr(target, "session", None) is None:
        return "Echo"
    return "player"


def _find_character(query, characters, self_character=None):
    """Search a list of Characters by name -- ``attack er`` matches ``Erin``.

    Also matches an active assumed_face (Leviathan identity theft) so
    watchers can target the worn name.

    When ``self_character`` is passed, me/self/myself resolve to that
    actor (classic MUD self-target) instead of substring-matching names.
    Also matches viewer-relative short-desc faces (hood / unintroduced)
    via the presence_face_for hook when available.

    Ordinals: ``2.carl``, ``other carl``, ``second carl`` pick among
    multiple matches. With no ordinal and multiple hits, returns the
    first match (legacy) -- callers that need ambiguity messages should
    use ``_collect_character_matches`` + ``parse_target_ordinal``.

    Shell quotes: ``'Earl Jacobs'`` is one needle, not ``'Earl`` + ``Jacobs'``.
    """
    query = collapse_shell_args(query)
    if self_character is not None and is_self_name(query):
        return self_character
    from engine.char_identity import parse_target_ordinal, pick_ordinal

    ordinal, rest = parse_target_ordinal(query)
    if self_character is not None and is_self_name(rest):
        return self_character
    matches = _collect_character_matches(
        rest, characters, self_character=self_character
    )
    if not matches:
        return None
    if ordinal is not None:
        return pick_ordinal(matches, ordinal)
    return matches[0]


def _game_for_staff_check(character):
    """Best-effort Game handle for staff rank checks.

    Prefer the live Session's game; fall back to a room stamp when hooks
    call ``_is_gm`` without a session game pointer (bug report 740).
    """
    session = getattr(character, "session", None)
    if session is not None:
        game = getattr(session, "game", None)
        if game is not None:
            return game
    room = getattr(character, "location", None)
    if room is not None:
        return getattr(room, "game", None)
    return None


def _staff_identity_character(character, game=None):
    """Resolve God omnipresence twins to the owning Mantle for staff checks.

    Bilocated twins borrow the player's Session for room verbs but are not
    linked Accounts -- staff rank must follow the God body (bug report 135).
    Engine-only: reads ``god_twin`` / ``god_twin_owner_key`` without importing
    supers (two-repo purity).
    """
    if character is None or not getattr(character, "god_twin", False):
        return character
    if game is None:
        session = getattr(character, "session", None)
        if session is not None:
            game = getattr(session, "game", None)
    owner_key = getattr(character, "god_twin_owner_key", None)
    if not owner_key or game is None:
        return character
    finder = getattr(game, "find_character", None)
    if callable(finder):
        owner = finder(owner_key)
        if owner is not None:
            return owner
    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        for ch in room.characters():
            if getattr(ch, "key", None) == owner_key:
                return ch
    return character


def _is_gm(character):
    """Is this character any rank of GM (ordinary or head)?

    Authoritative rank lives on the linked Account when present; falls
    back to legacy per-character ``gm_rank`` during migration. Immersion
    cast rides keep staff identity via ``session.staff_account``.
    """
    session = getattr(character, "session", None)
    game = _game_for_staff_check(character)
    character = _staff_identity_character(character, game)
    if session is not None:
        # Cast / alt ride: Session remembers the staff account from gm on.
        staff_name = getattr(session, "staff_account", None)
        if staff_name and game is not None:
            try:
                from engine.accounts import find_account, account_is_staff
                acct = find_account(game, staff_name)
                if account_is_staff(acct):
                    return True
            except Exception:
                _log_hook_error("gm check staff_account", staff_name)
    # Visible cast occupy survives copyover on the body blob before Session
    # restamp (bug report 1417).
    occupy = (getattr(character, "staff_occupy_account", None) or "").strip()
    if occupy and game is not None:
        try:
            from engine.accounts import find_account, account_is_staff
            if account_is_staff(find_account(game, occupy)):
                return True
        except Exception:
            _log_hook_error("gm check staff_occupy_account", occupy)
    # When session is on a GM spirit, gm_mode_body may hold the login body
    # whose account we should check; spirit also copies gm_rank.
    if game is not None:
        try:
            from engine.accounts import effective_gm_rank
            rank = effective_gm_rank(game, character)
            if rank in ("gm", "head_gm"):
                return True
            # Also check the login body when viewing from a GM spirit.
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                return effective_gm_rank(game, body) in ("gm", "head_gm")
        except Exception:
            _log_hook_error(
                "gm check effective_gm_rank",
                getattr(character, "key", None),
            )
    return character.gm_rank in ("gm", "head_gm")


def _is_head_gm(character):
    """Is this character specifically the head GM (can promote/demote)?"""
    session = getattr(character, "session", None)
    game = _game_for_staff_check(character)
    character = _staff_identity_character(character, game)
    if session is not None:
        staff_name = getattr(session, "staff_account", None)
        if staff_name and game is not None:
            try:
                from engine.accounts import find_account
                acct = find_account(game, staff_name)
                if acct is not None and (acct.gm_rank or "") == "head_gm":
                    return True
            except Exception:
                _log_hook_error("head_gm check staff_account", staff_name)
    if game is not None:
        try:
            from engine.accounts import effective_gm_rank
            if effective_gm_rank(game, character) == "head_gm":
                return True
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                return effective_gm_rank(game, body) == "head_gm"
        except Exception:
            _log_hook_error(
                "head_gm check effective_gm_rank",
                getattr(character, "key", None),
            )
    return character.gm_rank == "head_gm"


def _is_staff_gm(character):
    """True for live staff GMs, not immersion cast catalog bodies.

    Same filter as `who`'s GM strip -- used for evil-spawn tier scaling so
    a high-tier head GM online does not crank city threat to peak+1.
    Occupying staff still pass ``_is_gm`` (and gateway bind
    ``session_is_staff_gm``); do not reuse this filter for copyover stitch.
    """
    return _is_gm(character) and not getattr(character, "immersion", False)


# ---------------------------------------------------------------------------
# Movement dispatch table -- shared by commands.dispatch() (which routes a
# bare direction word straight to cmd_move, bypassing COMMANDS) and by
# cmd_commands' listing.
# ---------------------------------------------------------------------------

# Movement: each alias maps to a canonical direction string. Both "n" and
# "north" point to "north", so we only need one set of exit names. Diagonals
# (northwest/nw, ...) match room exit keys used by the town cross layout.
DIRECTIONS = {
    "north": "north", "n": "north",
    "south": "south", "s": "south",
    "east": "east",   "e": "east",
    "west": "west",   "w": "west",
    "northeast": "northeast", "ne": "northeast",
    "northwest": "northwest", "nw": "northwest",
    "southeast": "southeast", "se": "southeast",
    "southwest": "southwest", "sw": "southwest",
    "up": "up",       "u": "up",
    "down": "down",   "d": "down",
}
# Atlas / overland / demesne grids: n/s/e/w plus ne/nw/se/sw. Not up/down.
COMPASS_STEPS = frozenset({
    "north", "south", "east", "west",
    "northeast", "northwest", "southeast", "southwest",
})
# Opposite exit for gait arrive lines ("glides in from the west").
# Street-address / apartment doors are absent -> arrive says "… in.".
# Keep aligned with supers/map_store.OPPOSITE (cardinals + diagonals +
# vertical + in/out); engine must not import supers.
OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
    "up": "down",
    "down": "up",
    "in": "out",
    "out": "in",
    "leave": "in",
}
# Ash Court apartment doors (Floor hubs use a1-a10 / b1-b10 / c1-c10 exit
# names). Listed here so players can type the door label the same way NPCs
# path through room.exits -- look shows the exits; these make them walkable.
for _apt_floor, _apt_letter in (("a", "A"), ("b", "B"), ("c", "C")):
    for _apt_n in range(1, 11):
        _apt_exit = f"{_apt_floor}{_apt_n}"
        DIRECTIONS[_apt_exit] = _apt_exit
del _apt_floor, _apt_letter, _apt_n, _apt_exit


def resolve_compass_step(raw):
    """Canonical n/s/e/w/ne/nw/se/sw, or None.

    Accepts short tokens (``ne``), full words (``northeast``), and spaced
    pairs (``north east`` / ``n e``). Used by ``walk ne`` / ``drive sw``
    on atlas and other coord grids so those verbs do not treat the
    compass as a place name.
    """
    text = " ".join(str(raw or "").strip().lower().split())
    if not text:
        return None
    if text in DIRECTIONS:
        canon = DIRECTIONS[text]
        return canon if canon in COMPASS_STEPS else None
    compact = text.replace(" ", "").replace("-", "")
    if compact in DIRECTIONS:
        canon = DIRECTIONS[compact]
        return canon if canon in COMPASS_STEPS else None
    return None


def resolve_walk_direction(verb, room=None):
    """Map a typed verb to a ``room.exits`` key, or None if not a walk.

    Cardinals / diagonals / up / down and Ash Court ``a1``…``c10`` come from
    ``DIRECTIONS``. Other exit labels — especially street-address numbers
    from ``populate homes`` (``12223``) — resolve when ``room`` has that
    exact exit key and the verb is not a registered COMMANDS entry (so
    ``look`` / ``say`` never become walks).

    ``room`` may be None (then only DIRECTIONS matches).
    """
    if not verb:
        return None
    verb = str(verb).strip().lower()
    if not verb:
        return None
    if verb in DIRECTIONS:
        return DIRECTIONS[verb]
    if room is None:
        return None
    exits = getattr(room, "exits", None) or {}
    if verb not in exits:
        return None
    # Lazy import: commands imports this module at load time.
    try:
        from commands import COMMANDS
    except ImportError:
        COMMANDS = {}
    if verb in COMMANDS:
        return None
    return verb
