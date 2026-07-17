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
    before_relocate,
    can_see_spirit,
    encounter_check,
    move_gate_block,
)
from engine.world import Character


def _can_see_spirit(viewer, spirit_char):
    """Section 6's Attunement gate on spirit-sight, giving RES/FOC's
    "spirit tether"/"attunement" jobs (section 1's stat table) a concrete
    use. A spirit always perceives itself (engine.hooks.can_see_spirit's own
    default, with no game needed); anyone else's eligibility (Spirit Magic
    casters, high-Attunement characters) is SUPERS' call -- registered onto
    the hook by supers/bootstrap.py's register_all_hooks().
    """
    return can_see_spirit(viewer, spirit_char)


def _display_name(obj):
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
    function itself stays viewer-agnostic, like it always has been.
    """
    if isinstance(obj, Character) and getattr(obj, "gm_mode", False):
        # Staff form (gmmode) -- invincible wanderer; not a spirit/Echo.
        face = getattr(obj, "assumed_face", None) or obj.key
        return f"{face} (gm)"
    if isinstance(obj, Character) and obj.acts_as_echo():
        bits = ["echo"]
        # Online idlemode: tag so watchers know someone is AFK-simming
        # (session still attached -- they can receive tells / spectator verbs).
        if getattr(obj, "idle_mode", False) and obj.session is not None:
            bits.append("idle")
        if obj.regimen:
            bits.append(obj.regimen)
        if getattr(obj, "criminal", False):
            bits.append("criminal")
        face = getattr(obj, "assumed_face", None) or obj.key
        return f"{face} ({', '.join(bits)})"
    if isinstance(obj, Character) and obj.spirit:
        face = getattr(obj, "assumed_face", None) or obj.key
        return f"{face} (spirit)"
    if isinstance(obj, Character) and getattr(obj, "criminal", False):
        face = getattr(obj, "assumed_face", None) or obj.key
        return f"{face} (criminal)"
    if isinstance(obj, Character):
        face = getattr(obj, "assumed_face", None)
        if face:
            return face
    return obj.key


def _move_one(character, direction, dest, game):
    """The actual single-character move: leave/arrive broadcast, encounter
    roll, auto-look. Split out of cmd_move so `follow` (suggestions.log #44)
    can reuse it for each follower pulled along, without re-running the exit
    lookup (a follower's origin room -- same as the leader's -- already
    confirmed this exit exists).

    Game-specific side effects (cancel training, stop work, drag a carried
    body, lodging owner-enters, wilderness/dungeon spawn rolls) all run
    through engine.hooks so this function itself needs no `supers` import --
    see this module's docstring.
    """
    from engine.hooks import move_public_name
    room = character.location
    # Riding Mantle: leave/arrive as the host vessel (immersion parity).
    face = move_public_name(character, game)
    room.broadcast(f"{face} leaves {direction}.", exclude=character)
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
    # A body heaved onto your shoulder (cmd_heave) travels with you, exactly
    # like the gravedigger NPC carrying a corpse to the plot -- after_arrive
    # above already moved it through cadence.move_body so any spirit's
    # body_room stays in sync; this just picks the right broadcast wording.
    carried = getattr(character, "_carrying_body", None)
    if carried is not None:
        dest.broadcast(
            f"{face} arrives, {carried.key} slung over one shoulder.",
            exclude=character,
        )
    else:
        dest.broadcast(f"{face} arrives.", exclude=character)
    # Local import: cmd_look now lives in engine.verbs.basic, a different
    # package from this shared-helper module -- lazy avoids a module-level
    # cross-package import, same reasoning as every other import here.
    from engine.verbs.basic import cmd_look
    # Echo / idle companions pulled along have no need for auto-look
    # (and may have session None -- offline Echo).
    if character.session is not None:
        cmd_look(character, "", game)
    encounter_check(game, dest)   # roll AFTER the look, not before --
    # a dungeon-reveal/hostile-spawn message narrating something happening
    # in the room should land once the player has already seen the room
    # itself, not get buried above the room description they haven't read
    # yet (live player report).


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
        queue.extend(follower.followers)


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


def _find_character(query, characters):
    """Same idea as _find_item, but searching a list of Characters by name
    instead of Items by key -- lets 'attack er' match 'Erin'.

    Also matches an active assumed_face (Leviathan identity theft) so
    watchers can target the worn name.
    """
    query = query.lower()
    for char in characters:
        if query in char.key.lower():
            return char
        face = getattr(char, "assumed_face", None) or ""
        if face and query in face.lower():
            return char
    return None


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
# Ash Court apartment doors (Floor hubs use a1-a10 / b1-b10 / c1-c10 exit
# names). Listed here so players can type the door label the same way NPCs
# path through room.exits -- look shows the exits; these make them walkable.
for _apt_floor, _apt_letter in (("a", "A"), ("b", "B"), ("c", "C")):
    for _apt_n in range(1, 11):
        _apt_exit = f"{_apt_floor}{_apt_n}"
        DIRECTIONS[_apt_exit] = _apt_exit
del _apt_floor, _apt_letter, _apt_n, _apt_exit
