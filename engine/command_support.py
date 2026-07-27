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
    after_move_step,
    before_relocate,
    can_perceive_reaper,
    can_see_spirit,
    encounter_check,
    follow_pull_skip,
    move_gate_block,
)
from engine.world import Character


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


def _is_presence_hidden(viewer, other):
    """True when ``other`` should be omitted from viewer's look / targeting.

    Combines death-spirit invisibility (section 6), the living Reaper
    Mantle veil, true-invis staff Echoes left behind by ``gm on``, and
    rare ``gm fold`` shelved Echoes so call sites do not drift apart.
    """
    if other is None or viewer is None:
        return False
    if other is viewer:
        return False
    # Staff Cadence Echo while Session pilots GM form -- true invis.
    if getattr(other, "gm_away", False) and not _can_see_gm_away(viewer, other):
        return True
    # Soft-shelved player Echo (gm fold) -- same pierce as gm_away.
    if is_folded(other) and not _can_see_gm_away(viewer, other):
        return True
    if getattr(other, "spirit", False) and not _can_see_spirit(viewer, other):
        return True
    if not _can_perceive_reaper(viewer, other):
        return True
    return False


def _presence_hears(actor):
    """Predicate for ``Room.broadcast`` about ``actor``'s presence.

    Returns True when the watcher may hear leave/arrive / Cadence narrate
    lines naming ``actor``. Hidden presence (veiled Reaper, gm_away Echo,
    folded Echo, unseen spirit) is silent to ordinary watchers.
    """
    def _hears(watcher):
        return not _is_presence_hidden(watcher, actor)

    return _hears


def strip_ephemeral_storage_prefix(name):
    """Peel ``gmspirit:`` / ``husk:`` prefixes (including nested doubles).

    Storage keys like ``gmspirit:Wits`` or a corrupted
    ``gmspirit:gmspirit:Wits`` must never reach players as a display name.
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
        if low.startswith("gmspirit:") or low.startswith("husk:"):
            text = text.split(":", 1)[1].strip()
            if not text:
                return "?"
            continue
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
    return bool(
        getattr(obj, "gm_spirit", False)
        or getattr(obj, "gm_away", False)
        or is_folded(obj)
    )


def _presence_face(obj):
    """Public face for score / prompt / leave-arrive (no echo tags).

    Prefers ``assumed_face`` / ``husk_display_name`` over storage keys
    like ``gmspirit:Wits`` or ``husk:Castiel`` so players never see those
    internal prefixes in room traffic or sheets. When no face overlay is
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
        return strip_ephemeral_storage_prefix(face)
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


def _simple_english_plural(noun):
    """Cheap English plural for floor-item stacks (blade -> blades).

    Pluralizes the last word of a multi-word name (``angel blade`` ->
    ``angel blades``). Good enough for catalog keys; not a full
    inflection library (stdlib-only, learning project).
    """
    word = (noun or "").strip()
    if not word:
        return word
    # Multi-word: only inflect the last token.
    if " " in word:
        head, tail = word.rsplit(None, 1)
        return f"{head} {_simple_english_plural(tail)}"
    low = word.lower()
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 1 and low.endswith("y") and low[-2] not in "aeiou":
        return word[:-1] + "ies"
    if low.endswith("fe"):
        return word[:-2] + "ves"
    if low.endswith("f") and not low.endswith("ff"):
        return word[:-1] + "ves"
    return word + "s"


def _floor_item_stack_key(item):
    """Identity for stacking identical floor loot on look.

    Bodies stay unique (never ``2 bodies of Bob are here``). Catalog id
    wins when present so sixteen angel blades collapse; otherwise the
    display key (lowered) is the group.
    """
    if getattr(item, "is_body", False):
        # Each corpse is its own line even when keys match.
        return ("body", id(item))
    cat = getattr(item, "catalog_id", None)
    if cat:
        return ("cat", str(cat).strip().lower())
    key = (getattr(item, "key", None) or "").strip().lower()
    return ("key", key or id(item))


def floor_item_look_lines(items, character=None):
    """Build look lines for floor Items, stacking identical copies.

    One of a kind stays the usual display name (``an angel blade``).
    Two or more become ``N angel blades are here`` with a digit count
    (never ``sixteen``). Wayfinding signs keep muted paint / ``[SIGN]``
    the same way the old per-item loop did.
    """
    from collections import OrderedDict

    groups = OrderedDict()
    for item in items:
        groups.setdefault(_floor_item_stack_key(item), []).append(item)

    lines = []
    for group in groups.values():
        sample = group[0]
        name = _display_name(sample)
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
        count = len(group)
        if count == 1:
            lines.append(name)
            continue
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
        lines.append(line)
    return lines


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
    return name


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
    if getattr(obj, "criminal", False):
        bits.append("criminal")
    return bits


def _display_name_base(obj, viewer=None):
    """Core presence label without the follow-group ``(Group)`` suffix."""
    if isinstance(obj, Character) and viewer is not None:
        try:
            from engine.hooks import presence_face_for
            face = presence_face_for(viewer, obj)
            if face:
                if getattr(obj, "gm_mode", False):
                    return f"{face}(GM)"
                if obj.acts_as_echo():
                    return f"{face} ({', '.join(_echo_look_bits(obj))})"
                if obj.spirit:
                    return f"{face} (spirit)"
                if getattr(obj, "criminal", False):
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
                if getattr(obj, "gm_mode", False):
                    return f"{face}(GM)"
                if obj.acts_as_echo():
                    return f"{face} ({', '.join(_echo_look_bits(obj))})"
                if obj.spirit:
                    return f"{face} (spirit)"
                if getattr(obj, "criminal", False):
                    return f"{face} (criminal)"
                return face
        except Exception:
            _log_hook_error(
                "concealed_presence_name", getattr(obj, "key", None)
            )
    if isinstance(obj, Character) and getattr(obj, "gm_mode", False):
        # Staff form (gmmode) -- invincible wanderer; not a spirit/Echo.
        return f"{_presence_face(obj)}(GM)"
    if isinstance(obj, Character) and obj.acts_as_echo():
        return f"{_presence_face(obj)} ({', '.join(_echo_look_bits(obj))})"
    if isinstance(obj, Character) and obj.spirit:
        return f"{_presence_face(obj)} (spirit)"
    if isinstance(obj, Character) and getattr(obj, "criminal", False):
        return f"{_presence_face(obj)} (criminal)"
    if isinstance(obj, Character):
        return _presence_face(obj)
    return obj.key


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
        move_public_name, presence_face_for,
    )
    room = character.location
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
    _hears_move = _presence_hears(presence)
    # Skip the mover and (when different) the named host so a possessed
    # body does not hear third-person "PulseHost leaves north" about itself.
    exclude = character if presence is character else (character, presence)

    def _mover_face(watcher):
        """Per-watcher leave/arrive name (hood / unintroduced / vessel)."""
        face = presence_face_for(watcher, presence)
        return face or fallback_face

    if not stealth:
        room.broadcast(
            lambda w: move_leave_line(
                _mover_face(w), direction, presence,
            ),
            exclude=exclude,
            predicate=_hears_move,
        )
    # Leaving a job site ends an active gig-work shift (checked below, via
    # after_arrive, once we know whether the NEW room is also a work site).
    was_working = getattr(character, "working", False)
    # Moving always ends an online training montage (unlike work, which
    # only ends when you leave the job site -- training is not room-bound).
    train_msg = before_relocate(character)
    if train_msg and character.session:
        character.session.send(train_msg)
    character.move_to(dest)
    # Game-specific post-arrival effects: stop work if the job site was
    # left behind, drag a carried body along via cadence, and the lodging
    # owner-walks-in-on-a-stranger check.
    after_arrive(character, dest, game, was_working)
    # Opt-in combat-pit pose feed (direction known here; after_arrive does not).
    after_move_step(character, direction, dest, game)
    # A body heaved onto your shoulder (cmd_heave) travels with you, exactly
    # like the gravedigger NPC carrying a corpse to the plot -- after_arrive
    # above already moved it through cadence.move_body so any spirit's
    # body_room stays in sync; this just picks the right broadcast wording.
    carried = getattr(character, "_carrying_body", None)
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
    # Local import: cmd_look now lives in engine.verbs.basic, a different
    # package from this shared-helper module -- lazy avoids a module-level
    # cross-package import, same reasoning as every other import here.
    from engine.verbs.basic import cmd_look
    # Echo / idle companions pulled along have no need for auto-look
    # (and may have session None -- offline Echo).
    # Multi-hop walk passes auto_look=False so only the final room (or an
    # early stop) gets a full look -- encounters still fire below.
    if auto_look and character.session is not None:
        cmd_look(character, "", game, after_move=True)
    encounter_check(game, dest)   # roll AFTER the look, not before --
    # a dungeon-reveal/hostile-spawn message narrating something happening
    # in the room should land once the player has already seen the room
    # itself, not get buried above the room description they haven't read
    # yet (live player report). When auto_look is False the spawn line
    # still arrives; walk stops and looks if combat engages.


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
    stop_following(follower, silent=True)
    follower.following = leader
    followers = getattr(leader, "followers", None)
    if followers is None:
        leader.followers = [follower]
    elif follower not in followers:
        followers.append(follower)
    return True


def stop_following(follower, silent=False):
    """Clear `follower`'s follow bond. Safe with no Session (Cadence / Echo).

    When `silent` is False and the follower has a live Session, send the
    usual "you stop following" line (player unfollow / bare follow).
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
    # Opaque SUPERS beckon-companion marker (supers/companion.py) -- clear
    # when the follow bond drops so duty does not outlive the trail.
    if getattr(follower, "companion_leader_key", None) is not None:
        follower.companion_leader_key = None
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


def _pull_staff_tailers(leader, origin, direction, game):
    """Move staff GMs tailing `leader` with the same step (no group/pack)."""
    for tailer in list(getattr(leader, "staff_tailers", None) or []):
        if getattr(tailer, "staff_tailing", None) is not leader:
            continue
        if tailer.location is not origin or getattr(tailer, "spirit", False):
            continue
        dest = origin.exits.get(direction)
        if dest is not None and move_gate_block(tailer, origin, dest, game):
            if tailer.session is not None:
                tailer.session.send(
                    "[Staff] Tail stopped -- that exit is blocked for you."
                )
            stop_staff_tail(tailer, silent=True)
            continue
        _move_one(tailer, direction, dest, game)


def _pull_followers(leader, origin, direction, game):
    """Move everyone trailing `leader` (and, transitively, everyone trailing
    THEM) the same direction leader just went -- suggestions.log #44.

    Walked breadth-first from a plain list used as a queue, with a `moved`
    id-set guard: `following` is a single pointer but nothing stops two
    characters from following each other, so without a visited guard a
    two-cycle would pull each other back and forth forever.

    Pulls online players **and** Echo / idlemode companions (`acts_as_echo`)
    still standing in the leader's ORIGIN room -- Cadence hunt partners
    (e.g. Echo Sam trailing Dean) must walk and board together. Spirits
    still never pull; anyone who already left some other way is left alone.
    """
    moved = {id(leader)}
    queue = list(leader.followers)
    while queue:
        follower = queue.pop(0)
        if id(follower) in moved:
            continue
        moved.add(id(follower))
        if follower.location is not origin or follower.spirit:
            continue
        # SUPERS pack: follower on their own haunt/mission must not be
        # yanked when the companion leader walks for food (hooked).
        if follow_pull_skip(follower, leader, game):
            continue
        dest = origin.exits.get(direction)
        # Same gates a manual move would hit (jail cells, hunter-safe
        # sanctuaries, ...) -- immersion parity (AGENTS.md rule 9): a
        # follower being dragged along must not slip through a gate that
        # would have stopped them walking there on their own. Uses the same
        # move_gate_block hook cmd_move itself calls, so this stays
        # supers-agnostic (Phase 2b) instead of importing supers.slayer
        # directly the way this helper used to.
        if dest is not None and move_gate_block(follower, origin, dest, game):
            if follower.session is not None:
                follower.session.send(
                    "Something in you recoils -- that place is claimed "
                    "by the night. You stop following rather than "
                    "trespass."
                )
            continue
        _move_one(follower, direction, dest, game)
        queue.extend(list(getattr(follower, "followers", None) or []))
    _pull_staff_tailers(leader, origin, direction, game)
    # Party must share a room -- disband stragglers the pull could not move.
    try:
        from engine import group as group_mod
        group_mod.validate_group_colocation(leader, game)
    except Exception:
        pass


def _find_item(query, items):
    """Return the first Item whose key (or aliases) contains `query`.

    Case-insensitive substring match. The leading underscore in the name is a
    Python convention meaning "internal helper" -- not a command the player
    types. Lets 'get sword' match 'a rusted sword', and 'look in fridge'
    match a refrigerator that lists 'fridge' in Item.aliases.
    """
    query = query.lower()              # lowercase once, up front
    for item in items:
        # 'in' on strings is a substring test: is "sword" inside "a rusted sword"?
        if query in item.key.lower():
            return item                # found one -- hand it back immediately
        for alias in getattr(item, "aliases", ()) or ():
            if query in str(alias).lower():
                return item
    return None                        # looped through everything, no match


def _find_item_prefer_locked(query, items):
    """Like _find_item, but when several keys match, pick a locked container
    first (bug_reports.log #21: a leftover flavor strongbox sitting next to a
    real lockbox made `open strongbox` hit the wrong one and say "isn't
    locked")."""
    query = query.lower()
    matches = []
    for item in items:
        hit = query in item.key.lower()
        if not hit:
            for alias in getattr(item, "aliases", ()) or ():
                if query in str(alias).lower():
                    hit = True
                    break
        if hit:
            matches.append(item)
    if not matches:
        return None
    for item in matches:
        if item.locked:
            return item
    return matches[0]


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


def resolve_named_character(actor, query, game=None, candidates=None):
    """Resolve a typed name to a Character; me/self/myself always means `actor`.

    Prefer this over bare ``game.find_character`` whenever the typer can
    target themselves (GM ``set me …``, ``stats me``, room look-alikes).

    Supports ordinals: ``2.carl``, ``other carl``, ``second carl``.

    Resolution order:
      1. Empty query -> None
      2. Self alias -> ``actor`` (never a world name match)
      3. ``candidates`` list via ``_find_character`` when provided
      4. Else ``game.find_character(query)`` when ``game`` is provided
    """
    raw = (query or "").strip()
    if not raw:
        return None
    if is_self_name(raw):
        return actor
    if candidates is not None:
        return _find_character(raw, candidates, self_character=actor)
    finder = getattr(game, "find_character", None) if game is not None else None
    if finder is not None:
        return finder(raw)
    return None


def _collect_character_matches(query, characters, self_character=None):
    """All Characters whose key / given / face contain ``query`` (lower)."""
    from engine.char_identity import identity_match_needles

    needle = (query or "").strip().lower()
    if not needle:
        return []
    hits = []
    for char in characters:
        matched = False
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
        if matched and char not in hits:
            hits.append(char)
    return hits


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
    """
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


def _is_gm(character):
    """Is this character any rank of GM (ordinary or head)?"""
    return character.gm_rank in ("gm", "head_gm")


def _is_head_gm(character):
    """Is this character specifically the head GM (can promote/demote)?"""
    return character.gm_rank == "head_gm"


def _is_staff_gm(character):
    """True for live staff GMs, not immersion cast catalog bodies.

    Same filter as `who`'s GM strip -- used for evil-spawn tier scaling so
    a high-tier head GM online does not crank city threat to peak+1.
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
