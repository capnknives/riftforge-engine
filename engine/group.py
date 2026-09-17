"""
group.py -- follow-bond parties (engine-generic).

A group is the follow tree rooted at whoever sits at the top of
``Character.following`` pointers: the leader plus everyone who trails
them (transitively). Beckon companion duty, pack glue, hunt partners,
and ordinary ``follow`` all use that bond -- so they share one Group.

No networking. No SUPERS imports -- companion_leader_key is optional
flavor; membership is the follow graph.

``group_row`` (front / back) is a *display* assignable stance for the
roster and prompt -- it does not change combat math until formation
combat is unparked.
"""

from __future__ import annotations

# Display-only party rows (config via ``group front`` / ``group back``).
ROW_FRONT = "front"
ROW_BACK = "back"
ROW_LABELS = {
    ROW_FRONT: "Front Row",
    ROW_BACK: "Back Row",
}

# Game ticks a party may stay follow-bonded in different rooms before
# auto-disband. Cadence followers may step to catch up during this window
# (instant disband thrashed Dean/Sam on one-tick lag + blocked reunite).
# 100 ticks = 5 real minutes at the 3s heartbeat (live bug #67: Dean/Sam
# stayed grouped-but-apart well past the old 3-minute grace window).
GROUP_APART_DISBAND_TICKS = 100

# After this many "left behind" auto-disbands between the same pair,
# Cadence stops seeking / re-gluing them for one game-day (both solo
# toward each other). Matches engine.game_calendar.TICKS_PER_GAME_DAY.
GROUP_SEPARATION_STRIKES_BEFORE_SOLO = 5
GROUP_SOLO_COOLDOWN_TICKS = 9600

# Every ordinary apart-disband (not just the 5th-strike escalation) gives
# the pair a short breather from Cadence's auto-regroup pull so they can
# chase their own SEEK/CRITICAL needs first -- 600 ticks = 30 real minutes
# at the 3s heartbeat (live bug #67, requested explicitly by the reporter).
GROUP_REGROUP_COOLDOWN_TICKS = 600

# Live leader with zero colocated followers: shorter grace before disband.
# Echo followers still get Cadence catch-up ticks to path in (bug #256).
# 20 ticks ~ 60s at the 3s heartbeat (full grace stays 100 / ~5 min).
GROUP_LIVE_ALONE_DISBAND_TICKS = 20


def resolve_leader(character):
    """Walk ``following`` to the root leader (or self when solo).

    Cycle-safe: if A follows B follows A, returns the first loop member
    already seen so we never hang.
    """
    if character is None:
        return None
    seen = set()
    cur = character
    while cur is not None:
        cid = id(cur)
        if cid in seen:
            return cur
        seen.add(cid)
        nxt = getattr(cur, "following", None)
        if nxt is None:
            return cur
        cur = nxt
    return character


def members_of(leader):
    """Leader plus every transitive follower (breadth-first).

    Returns a list with the leader first, then followers in discovery
    order. Empty when leader is None.
    """
    if leader is None:
        return []
    out = [leader]
    seen = {id(leader)}
    queue = [leader]
    while queue:
        node = queue.pop(0)
        for follower in list(getattr(node, "followers", None) or []):
            if follower is None:
                continue
            fid = id(follower)
            if fid in seen:
                continue
            seen.add(fid)
            out.append(follower)
            queue.append(follower)
    return out


def group_members(character):
    """Full party for ``character``, or a one-element list when solo."""
    leader = resolve_leader(character)
    if leader is None:
        return []
    return members_of(leader)


def in_group(character):
    """True when character shares a follow tree with at least one other."""
    return len(group_members(character)) >= 2


def same_group(a, b):
    """True when a and b are distinct members of the same follow party."""
    if a is None or b is None or a is b:
        return False
    if not in_group(a):
        return False
    return resolve_leader(a) is resolve_leader(b)


def get_row(character):
    """Return ``front`` or ``back`` for this character (default front)."""
    raw = (getattr(character, "group_row", None) or ROW_FRONT)
    key = str(raw).strip().lower()
    if key in ROW_LABELS:
        return key
    return ROW_FRONT


def set_row(character, row):
    """Assign display row. Returns True on success."""
    if character is None:
        return False
    key = str(row or "").strip().lower()
    if key not in ROW_LABELS:
        return False
    character.group_row = key
    return True


def row_label(character):
    """Plain ``Front Row`` / ``Back Row`` label for sheets."""
    return ROW_LABELS[get_row(character)]


def member_hp_pair(character):
    """``(current, max)`` lifeforce percents for roster lines (0-100).

    Uses ``hooks.gmcp_char_vitals`` when SUPERS is installed so the
    numbers match score / the prompt. Falls back to 100/100.
    """
    hp = 100
    max_hp = 100
    try:
        from engine import hooks
        vitals = hooks.gmcp_char_vitals(character) or {}
    except Exception:
        vitals = {}
    if not vitals:
        return hp, max_hp
    raw_hp = vitals.get("hp_raw", vitals.get("hp"))
    raw_max = vitals.get("maxhp_raw", vitals.get("maxhp"))
    try:
        cur = float(raw_hp)
        cap = float(raw_max) if raw_max not in (None, "", "0") else 0.0
        if cap > 0:
            if cap == 100.0 and "hp_raw" not in vitals:
                hp = max(0, min(100, int(round(cur))))
                max_hp = 100
            else:
                hp = max(0, min(100, int(round(100.0 * cur / cap))))
                max_hp = 100
    except (TypeError, ValueError):
        pass
    return hp, max_hp


def format_group_sheet(character, game=None):
    """Roster lines for the ``group`` verb (client-wrappable).

    Shape::

        Dean  100/100hp - Front Row (you)
        Sam   85/100hp - Back Row

        Group wants: food — Sam is hungry.
          Lead them to food (diner / buy / eat).

    Labels stay plain text -- never meaning-by-color alone. Rows are
    display-only (``group front`` / ``group back``). The optional
    ``Group wants`` block comes from ``hooks.group_sheet_extra`` (SUPERS
    convoy objective) so live / idlemode leaders know what errand to run.
    """
    if character is None:
        return "You are not in a group."
    members = group_members(character)
    if len(members) < 2:
        return (
            "You are not in a group.\r\n"
            "Follow someone, or accept a beckon, to team up "
            "(see 'help group' / 'help follow' / 'help beckon').\r\n"
            "When grouped: group front|back sets your display row."
        )
    leader = members[0]
    lines = ["", "Your group:"]
    for member in members:
        name = getattr(member, "key", "?")
        face = (
            getattr(member, "assumed_face", None)
            or getattr(member, "husk_display_name", None)
            or name
        )
        you = " (you)" if member is character else ""
        apart = (
            member is not leader
            and not mates_share_group_location(leader, member)
        )
        apart_tag = " (apart)" if apart else ""
        hp, max_hp = member_hp_pair(member)
        row = row_label(member)
        lines.append(f"  {face}  {hp}/{max_hp}hp - {row}{you}{apart_tag}")
    # Game flavor (pack SEEK convoy / burger rule) -- optional hook.
    try:
        from engine import hooks
        extra = hooks.group_sheet_extra(character, game)
    except Exception:
        extra = ""
    if extra:
        lines.append("")
        # Hook may return one block with embedded \r\n.
        for chunk in str(extra).replace("\r\n", "\n").split("\n"):
            if chunk != "":
                lines.append(chunk)
    focus_line = format_focus_line(leader, game)
    if focus_line:
        lines.append("")
        lines.append(focus_line)
    lines.append("")
    lines.append(
        "Type group for the party menu. "
        "group focus <name> sets who NPC and Echo mates attack. "
        "Only the leader moves the party -- others type "
        "'group leave confirm' to split off."
    )
    return "\r\n".join(lines)


def ensure_session_defaults(character):
    """Session-only flags for group leave confirm + split-strike stamps."""
    if character is None:
        return
    if not hasattr(character, "group_leave_confirm_pending"):
        character.group_leave_confirm_pending = False
    # Per-other-key left-behind strike counts (persisted).
    if not isinstance(getattr(character, "group_split_strikes", None), dict):
        character.group_split_strikes = {}
    # Per-other-key tick deadline: Cadence will not auto-regroup until then.
    if not isinstance(getattr(character, "group_solo_until", None), dict):
        character.group_solo_until = {}
    # Per-other-key short breather after an ordinary apart-disband -- see
    # regroup_paused(). Separate from group_solo_until (the 5-strike
    # escalation) so the short pause never eats into strike counting.
    if not isinstance(getattr(character, "group_regroup_pause_until", None), dict):
        character.group_regroup_pause_until = {}
    # Leader-set combat focus (a character key). NPC / Echo followers
    # attack this body, or the leader's live target when unset.
    if not hasattr(character, "group_focus_key"):
        character.group_focus_key = None
    if not hasattr(character, "_group_focus_picks"):
        character._group_focus_picks = []
    # Persisted follow leader (copyover / save). Live ``following`` pointer
    # is rebuilt from this after load via ``heal_follow_bonds``.
    if not hasattr(character, "following_leader_key"):
        character.following_leader_key = None
    # Player/Echo typed unfollow / group leave -- blocks Cadence re-glue
    # until they voluntarily ``follow`` again (bug report 1574).
    if not hasattr(character, "follow_declined_leader_key"):
        character.follow_declined_leader_key = None


def follow_declined(follower, leader):
    """True when ``follower`` explicitly peeled off ``leader`` recently."""
    if follower is None or leader is None:
        return False
    ensure_session_defaults(follower)
    declined = str(getattr(follower, "follow_declined_leader_key", "") or "").strip()
    if not declined:
        return False
    leader_key = str(getattr(leader, "key", "") or "").strip()
    return bool(leader_key) and declined == leader_key


def record_follow_declined(follower, leader, game=None):
    """Stamp explicit unfollow so pack / companion Cadence cannot re-glue."""
    if follower is None or leader is None:
        return
    ensure_session_defaults(follower)
    leader_key = str(getattr(leader, "key", "") or "").strip()
    if leader_key:
        follower.follow_declined_leader_key = leader_key
    from engine import hooks

    hooks.stamp_follow_declined_companion_cooldown(follower, leader, game)


def clear_follow_declined_for(follower, leader):
    """Clear declined stamp when the player voluntarily follows again."""
    if follower is None or leader is None:
        return
    ensure_session_defaults(follower)
    declined = str(getattr(follower, "follow_declined_leader_key", "") or "").strip()
    leader_key = str(getattr(leader, "key", "") or "").strip()
    if declined and leader_key and declined == leader_key:
        follower.follow_declined_leader_key = None


def _resolve_character_key(game, key):
    """Find a live Character by key (game.find_character or roster scan)."""
    if not key or game is None:
        return None
    find = getattr(game, "find_character", None)
    if callable(find):
        found = find(key)
        if found is not None:
            return found
    try:
        from engine.char_index import iter_characters

        for obj in iter_characters(game):
            if getattr(obj, "key", None) == key:
                return obj
    except ImportError:
        pass
    return None


def restore_follow_bond(character, game):
    """Rebuild ``following`` from ``following_leader_key`` when safe.

    Called after world load and on session attach (copyover). Skips when
    the bond already exists, the leader is missing, the player declined
    unfollow, or Cadence pair-solo gates block regroup.
    """
    if character is None or game is None:
        return False
    ensure_session_defaults(character)
    key = str(getattr(character, "following_leader_key", "") or "").strip()
    if not key:
        return False
    if getattr(character, "following", None) is not None:
        return True
    leader = _resolve_character_key(game, key)
    if leader is None or leader is character:
        return False
    if follow_declined(character, leader):
        return False
    if cadence_regroup_blocked(character, leader, game):
        return False
    from engine.command_support import start_following

    return start_following(character, leader)


def heal_follow_bonds(game):
    """After all characters load, restore every persisted follow bond."""
    if game is None:
        return 0
    try:
        from engine.char_index import iter_characters

        roster = list(iter_characters(game))
    except ImportError:
        roster = list(getattr(game, "characters", None) or [])
        if isinstance(roster, dict):
            roster = list(roster.values())
    count = 0
    for obj in roster:
        if restore_follow_bond(obj, game):
            count += 1
    return count


def group_seek_blocked(a, b, game=None):
    """True when Cadence must not auto-follow / beckon / pack-glue a with b.

    Pair solo-day after repeated left-behind splits. Live players may still
    type ``follow`` / ``beckon`` on purpose; this only gates seeking.
    Symmetric: either body's stamp blocks the pair.
    """
    if a is None or b is None or a is b:
        return False
    ensure_session_defaults(a)
    ensure_session_defaults(b)
    now = _group_tick(game)
    a_key = getattr(a, "key", None)
    b_key = getattr(b, "key", None)
    if not a_key or not b_key:
        return False
    until_ab = int((a.group_solo_until or {}).get(b_key, 0) or 0)
    until_ba = int((b.group_solo_until or {}).get(a_key, 0) or 0)
    until = max(until_ab, until_ba)
    return bool(until and now < until)


def regroup_paused(a, b, game=None):
    """True during the short post-split breather (``GROUP_REGROUP_COOLDOWN_TICKS``).

    Separate from ``group_seek_blocked`` (the 5-strike day-long escalation)
    so a routine first-time split does not eat into strike counting -- it
    just gives the pair ~30 minutes to chase their own needs before Cadence
    tries to glue them back together (live bug #67).
    """
    if a is None or b is None or a is b:
        return False
    ensure_session_defaults(a)
    ensure_session_defaults(b)
    now = _group_tick(game)
    a_key = getattr(a, "key", None)
    b_key = getattr(b, "key", None)
    if not a_key or not b_key:
        return False
    until_ab = int((a.group_regroup_pause_until or {}).get(b_key, 0) or 0)
    until_ba = int((b.group_regroup_pause_until or {}).get(a_key, 0) or 0)
    until = max(until_ab, until_ba)
    return bool(until and now < until)


def cadence_regroup_blocked(a, b, game=None):
    """True when Cadence must not auto-regroup ``a`` with ``b`` right now.

    Combines the day-long 5-strike escalation with the short post-split
    breather -- call sites that gate Cadence auto-follow / beckon /
    pack-glue should use this instead of ``group_seek_blocked`` alone.
    """
    return group_seek_blocked(a, b, game) or regroup_paused(a, b, game)


def _stamp_pair_solo_day(a, b, game):
    """Both bodies treat each other as solo for GROUP_SOLO_COOLDOWN_TICKS.

    Resets their mutual strike counters. Returns True when a new stamp
    was written (False when already under an active mutual cooldown).
    """
    if a is None or b is None or a is b:
        return False
    ensure_session_defaults(a)
    ensure_session_defaults(b)
    a_key = getattr(a, "key", None)
    b_key = getattr(b, "key", None)
    if not a_key or not b_key:
        return False
    now = _group_tick(game)
    until = now + GROUP_SOLO_COOLDOWN_TICKS
    # Already cooling down -- do not re-notify / refresh from strike spam.
    if group_seek_blocked(a, b, game):
        return False
    a.group_solo_until[b_key] = until
    b.group_solo_until[a_key] = until
    a.group_split_strikes[b_key] = 0
    b.group_split_strikes[a_key] = 0
    return True


def _stamp_pair_regroup_cooldown(a, b, game):
    """Both bodies skip Cadence auto-regroup toward each other for a short
    breather (``GROUP_REGROUP_COOLDOWN_TICKS``) after an ordinary apart
    disband -- unlike ``_stamp_pair_solo_day`` this does NOT reset the
    left-behind strike counters, so repeat splits still escalate to the
    full day-long cooldown after ``GROUP_SEPARATION_STRIKES_BEFORE_SOLO``.

    No-op (returns False) when a longer cooldown (the strike escalation,
    or an earlier call to this same helper) is already active, so it never
    shortens an existing wait.
    """
    if a is None or b is None or a is b:
        return False
    ensure_session_defaults(a)
    ensure_session_defaults(b)
    a_key = getattr(a, "key", None)
    b_key = getattr(b, "key", None)
    if not a_key or not b_key:
        return False
    if cadence_regroup_blocked(a, b, game):
        return False
    now = _group_tick(game)
    until = now + GROUP_REGROUP_COOLDOWN_TICKS
    a.group_regroup_pause_until[b_key] = until
    b.group_regroup_pause_until[a_key] = until
    return True


def _notify_pair_solo_day(a, b):
    """Tell live / idle watchers the pair is going solo for a day."""
    a_face = (
        getattr(a, "assumed_face", None)
        or getattr(a, "husk_display_name", None)
        or getattr(a, "key", "?")
    )
    b_face = (
        getattr(b, "assumed_face", None)
        or getattr(b, "husk_display_name", None)
        or getattr(b, "key", "?")
    )
    for body, other_face in ((a, b_face), (b, a_face)):
        sess = getattr(body, "session", None)
        if sess is None:
            continue
        sess.send(
            f"[Group] You and {other_face} keep getting split. "
            "Both of you go solo for a day -- Cadence will not "
            "regroup you until then."
        )


def note_separation_strikes(members, game):
    """Bump left-behind strikes for every pair; stamp solo-day at threshold.

    Called when a party auto-disbands for ``reason='separated'``. After
    ``GROUP_SEPARATION_STRIKES_BEFORE_SOLO`` hits between the same pair,
    both choose solo toward each other for one game-day.

    Returns ``(a, b)`` pairs that just entered the solo-day so the caller
    can notify after the split tell (readable message order).
    """
    stamped = []
    bodies = [m for m in (members or ()) if m is not None]
    if len(bodies) < 2:
        return stamped
    # Unordered pairs -- each body stores the other's key.
    for i, a in enumerate(bodies):
        ensure_session_defaults(a)
        a_key = getattr(a, "key", None)
        if not a_key:
            continue
        for b in bodies[i + 1:]:
            ensure_session_defaults(b)
            b_key = getattr(b, "key", None)
            if not b_key:
                continue
            # Already on a solo-day -- do not thrash the counter.
            if group_seek_blocked(a, b, game):
                continue
            strikes_a = dict(a.group_split_strikes or {})
            strikes_b = dict(b.group_split_strikes or {})
            count = max(
                int(strikes_a.get(b_key, 0) or 0),
                int(strikes_b.get(a_key, 0) or 0),
            ) + 1
            a.group_split_strikes[b_key] = count
            b.group_split_strikes[a_key] = count
            if count < GROUP_SEPARATION_STRIKES_BEFORE_SOLO:
                continue
            if _stamp_pair_solo_day(a, b, game):
                stamped.append((a, b))
    return stamped


def is_leader(character):
    """True when this body sits at the top of the follow chain."""
    if character is None:
        return False
    return resolve_leader(character) is character


def _group_tick(game):
    """Current game tick for apart-grace stamps (0 when unknown)."""
    if game is None:
        return 0
    return int(getattr(game, "game_time_ticks", 0) or 0)


def _group_event_rooms(members):
    """Map each party member key to their current room key (bug-report context).

    ``note_group_event`` stores this as ``rooms`` so triage can see who stood
    where when the bond broke (apart-disband vs colocated leave).
    """
    rooms = {}
    for member in members or ():
        if member is None:
            continue
        member_key = getattr(member, "key", None)
        if not member_key:
            continue
        room = getattr(member, "location", None)
        room_key = getattr(room, "key", None) if room is not None else None
        if room_key is not None:
            rooms[str(member_key)] = str(room_key)
    return rooms


def _stamp_group_events(members, game, *, reason):
    """Stamp Wave 0 group lifecycle events on every body in ``members``.

    Called once per split/disband *before* follow bonds are cleared so the
    roster and room map still match the party that just broke.
    """
    bodies = [m for m in (members or ()) if m is not None]
    if len(bodies) < 2:
        return
    # Lazy import keeps report_debug optional at import time and SUPERS-free.
    from engine.report_debug import note_group_event

    leader = bodies[0]
    leader_key = getattr(leader, "key", None)
    member_keys = [
        str(getattr(m, "key"))
        for m in bodies
        if getattr(m, "key", None)
    ]
    rooms = _group_event_rooms(bodies)
    tick = _group_tick(game)
    for member in bodies:
        note_group_event(
            member,
            tick=tick,
            reason=reason,
            leader=leader_key,
            members=member_keys,
            rooms=rooms,
        )


def _character_plane(character):
    """Plane id for colocation rules (default earth when unknown)."""
    room = getattr(character, "location", None)
    if room is None:
        return "earth"
    return getattr(room, "plane", None) or "earth"


def mates_share_group_plane(a, b):
    """True when two bodies stand on the same plane (earth, purgatory, …)."""
    if a is None or b is None:
        return False
    return _character_plane(a) == _character_plane(b)


def _colocation_game(a, b):
    """Best-effort Game for travel-spot colocation (Echoes / NPCs)."""
    for who in (a, b):
        if who is None:
            continue
        g = getattr(who, "game", None)
        if g is not None:
            return g
        sess = getattr(who, "session", None)
        g = getattr(sess, "game", None) if sess is not None else None
        if g is not None:
            return g
        loc = getattr(who, "location", None)
        g = getattr(loc, "game", None) if loc is not None else None
        if g is not None:
            return g
    return None


def mates_share_group_location(a, b):
    """True when two bodies count as together for group unit rules.

    Same Room object, or same boarded vehicle (cabin vs curb used to
    false-trigger instant disband).
    """
    if a is None or b is None:
        return False
    if not mates_share_group_plane(a, b):
        return False
    a_room = getattr(a, "location", None)
    b_room = getattr(b, "location", None)
    if a_room is not None and a_room is b_room:
        return True
    a_vid = getattr(a, "in_vehicle", None)
    b_vid = getattr(b, "in_vehicle", None)
    if a_vid and b_vid and a_vid == b_vid:
        return True
    from engine.hooks import group_share_travel_spot

    return group_share_travel_spot(a, b, _colocation_game(a, b))


def all_members_colocated(character):
    """True when every groupmate shares the leader's room (or vehicle)."""
    members = group_members(character)
    if len(members) < 2:
        return True
    leader = members[0]
    if getattr(leader, "location", None) is None and not getattr(
        leader, "in_vehicle", None
    ):
        return False
    for mate in members[1:]:
        if not mates_share_group_location(leader, mate):
            return False
    return True


def separated_members(character):
    """Groupmates not colocated with the leader (empty when solo)."""
    members = group_members(character)
    if len(members) < 2:
        return []
    leader = members[0]
    out = []
    for mate in members[1:]:
        if not mates_share_group_location(leader, mate):
            out.append(mate)
    return out


def _clear_apart_stamp(leader):
    """Drop grace timer when the party is together again."""
    if leader is None:
        return
    if hasattr(leader, "_group_apart_since_tick"):
        leader._group_apart_since_tick = None


def live_present(character):
    """True when a body has an active player Session (not Echo / idlemode).

    Cadence ``npc_do`` and ``gm force`` on Echoes attach SilentSession --
    that is not a live player and must not lock group movement.
    """
    if character is None:
        return False
    from engine.npc_act import is_live_session

    if not is_live_session(getattr(character, "session", None)):
        return False
    if hasattr(character, "acts_as_echo") and character.acts_as_echo():
        return False
    return True


def _colocated_follower_count(leader):
    """How many followers share the leader's room or vehicle."""
    if leader is None:
        return 0
    count = 0
    for mate in group_members(leader)[1:]:
        if mates_share_group_location(leader, mate):
            count += 1
    return count


def _apart_disband_ticks(leader):
    """Grace window before an apart party auto-disbands."""
    if live_present(leader) and _colocated_follower_count(leader) == 0:
        return GROUP_LIVE_ALONE_DISBAND_TICKS
    return GROUP_APART_DISBAND_TICKS


def maintain_group_colocation(leader, game):
    """Disband only after GROUP_APART_DISBAND_TICKS apart (not same tick).

    Returns True when the party was disbanded. While apart but inside the
    grace window, followers may Cadence-step to catch up (see
    ``cadence_may_step``). Cross-plane splits disband immediately -- there
    is no reunite path while one mate is in Purgatory and another on Earth
    (live bug report 188).
    """
    if leader is None or game is None:
        return False
    if not in_group(leader):
        _clear_apart_stamp(leader)
        return False
    members = group_members(leader)
    for mate in members[1:]:
        if not mates_share_group_plane(leader, mate):
            disband_group(leader, game, reason="separated")
            _clear_apart_stamp(leader)
            return True
    if all_members_colocated(leader):
        _clear_apart_stamp(leader)
        return False
    now = _group_tick(game)
    since = getattr(leader, "_group_apart_since_tick", None)
    if since is None:
        leader._group_apart_since_tick = now
        return False
    if (now - int(since)) < _apart_disband_ticks(leader):
        return False
    disband_group(leader, game, reason="separated")
    _clear_apart_stamp(leader)
    return True


def live_move_blocked_message(character):
    """Warn when a live follower tries to initiate movement.

    Echo / idlemode bodies use Cadence pathing -- gated separately in
    ``cadence_may_step``. Returns None when movement is allowed.

    A follow bond to a GM, idle Echo, NPC, or other non-live leader
    does not trap a live climber (Ash viewport snoop + force Castiel
    ``up`` while following Ash). Two live players still glue: only the
    live leader walks.
    """
    if character is None:
        return None
    if not live_present(character):
        return None
    if getattr(character, "gm_mode", False) or getattr(
        character, "gm_spirit", False
    ):
        return None
    if not in_group(character):
        return None
    if is_leader(character):
        return None
    leader = resolve_leader(character)
    if leader is None or not live_present(leader):
        return None
    if getattr(leader, "gm_mode", False) or getattr(leader, "gm_spirit", False):
        return None
    if getattr(leader, "is_npc", False):
        return None
    ensure_session_defaults(character)
    return (
        "You're in a group -- only the leader moves the party. "
        "Walking away breaks the group. Type 'group leave confirm' "
        "if you must split off."
    )


def block_live_group_move(character):
    """True when a live follower move was blocked (message sent)."""
    msg = live_move_blocked_message(character)
    if not msg:
        return False
    sess = getattr(character, "session", None)
    if sess is not None:
        sess.send(msg)
    return True


def warn_group_leave(character, *, as_leader=False):
    """First leave/disband attempt -- set confirm pending and tell."""
    ensure_session_defaults(character)
    character.group_leave_confirm_pending = True
    sess = getattr(character, "session", None)
    if sess is None:
        return
    if as_leader:
        sess.send(
            "You're the group leader -- disbanding splits everyone. "
            "Type 'group disband confirm' if you're sure."
        )
    else:
        sess.send(
            "Leaving the group breaks the party bond. "
            "Type 'group leave confirm' if you're sure."
        )


def try_leave_group(character, game, *, confirm=False):
    """Follower peels off the party. Returns blocked|left|ok."""
    ensure_session_defaults(character)
    if not in_group(character):
        character.group_leave_confirm_pending = False
        return "ok"
    if is_leader(character):
        return try_disband_group(character, game, confirm=confirm)
    if not confirm:
        leader = resolve_leader(character)
        # Live-on-live parties still need confirm. Following a GM, Echo,
        # or idle body should not trap you behind a second confirm hop.
        need_confirm = live_present(character) and live_present(leader)
        if leader is not None and (
            getattr(leader, "gm_mode", False)
            or getattr(leader, "gm_spirit", False)
        ):
            need_confirm = False
        if need_confirm:
            warn_group_leave(character, as_leader=False)
            return "blocked"
    character.group_leave_confirm_pending = False
    _peel_from_group(character, game, reason="group_leave")
    sess = getattr(character, "session", None)
    if sess is not None:
        sess.send("You step away from the group.")
    return "left"


def try_disband_group(character, game, *, confirm=False):
    """Leader disbands the whole party. Returns blocked|left|ok."""
    ensure_session_defaults(character)
    if not in_group(character):
        character.group_leave_confirm_pending = False
        return "ok"
    leader = resolve_leader(character)
    if leader is None:
        return "ok"
    if not confirm:
        warn_group_leave(character, as_leader=True)
        return "blocked"
    character.group_leave_confirm_pending = False
    disband_group(leader, game, reason="disband")
    return "left"


def _peel_from_group(member, game, *, reason="group_split", stamp_event=True):
    """Remove one body from the follow tree + companion glue.

    When ``stamp_event`` is True (default), a real party split (two or more
    bodies still bonded) writes Wave 0 debug stamps *before* the peel.
    ``disband_group`` passes ``stamp_event=False`` because it already stamped
    the whole roster once for the disband.
    """
    if member is None:
        return
    # Capture the follow tree while bonds still exist.
    if stamp_event and in_group(member):
        _stamp_group_events(group_members(member), game, reason=reason)
    from engine.command_support import stop_following

    # Delegate companion cleanup to engine.hooks so engine stays SUPERS-free
    from engine import hooks

    if getattr(member, "companion_leader_key", None):
        hooks.clear_companion_duty(member, game, reason=reason, silent=True)
    stop_following(member, silent=True, declined=True, game=game)


def disband_group(leader, game, *, reason="separated"):
    """Break follow bonds for the whole party.

    ``reason`` is a short label for companion clear (separated / disband).
    Left-behind splits also bump per-pair strike counts; after five,
    both bodies go Cadence-solo toward each other for one game-day.
    """
    if leader is None:
        return
    members = members_of(leader)
    if len(members) < 2:
        return
    # One ops line per disband (not per member) plus per-body report stamps.
    from engine import log_util

    leader_key = getattr(leader, "key", "?")
    dropped = [
        str(getattr(m, "key", "?"))
        for m in members[1:]
    ]
    log_util.ops(
        "group",
        f"split reason={reason} leader={leader_key} "
        f"dropped={','.join(dropped)}",
    )
    _stamp_group_events(members, game, reason=reason)
    solo_pairs = []
    if reason == "separated":
        msg = (
            "[Group] The party split -- someone was left behind. "
            "The group disbands; follow or beckon again to regroup."
        )
        # Count before peel so we still have the full party list.
        solo_pairs = note_separation_strikes(members, game)
        # Every ordinary split (even a first-time one) gets a short
        # Cadence regroup cooldown so the pair chases their own needs
        # instead of being pulled straight back together -- pairs that
        # just escalated to the full day-long cooldown above are skipped
        # (group_seek_blocked already holds them, and re-stamping would
        # be a no-op).
        escalated = {frozenset((id(a), id(b))) for a, b in solo_pairs}
        bodies = [m for m in members if m is not None]
        for i, a in enumerate(bodies):
            for b in bodies[i + 1:]:
                if frozenset((id(a), id(b))) in escalated:
                    continue
                _stamp_pair_regroup_cooldown(a, b, game)
    elif reason == "reconnect_apart":
        msg = (
            "[Group] You reconnect apart from your party -- "
            "the group bond cleared. Follow or beckon again to regroup."
        )
    else:
        msg = "[Group] The party disbands."
    for member in list(members):
        _peel_from_group(member, game, reason=reason, stamp_event=False)
        ensure_session_defaults(member)
        member.group_leave_confirm_pending = False
        sess = getattr(member, "session", None)
        if sess is not None:
            sess.send(msg)
    # Solo-day tell after the split line (same session, readable order).
    for a, b in solo_pairs:
        _notify_pair_solo_day(a, b)


def validate_group_colocation(anchor, game):
    """Grace-gated colocation check (see ``maintain_group_colocation``)."""
    if anchor is None or game is None:
        return False
    if not in_group(anchor):
        return False
    leader = resolve_leader(anchor)
    if leader is None:
        return False
    return maintain_group_colocation(leader, game)


def heal_group_on_session_attach(character, game):
    """Live login / reconnect: restore follow bonds + clear stale apart glue.

    Persisted ``following_leader_key`` is re-linked first (copyover bug
    report 1573). Echo and idlemode bodies keep apart grace -- Cadence
    may still catch up. A present player reconnecting apart from the
    party still peels (crash #256).
    """
    if character is None or game is None:
        return
    restore_follow_bond(character, game)
    if not live_present(character):
        return
    if not in_group(character):
        return
    if all_members_colocated(character):
        return
    leader = resolve_leader(character)
    if leader is None:
        return
    if is_leader(character):
        disband_group(leader, game, reason="reconnect_apart")
        return
    _peel_from_group(character, game, reason="reconnect_apart")
    sess = getattr(character, "session", None)
    if sess is not None:
        sess.send(
            "[Group] You reconnect apart from the party -- "
            "the group bond cleared. Follow or beckon again to regroup."
        )


def heal_separated_groups(game):
    """Tick heal: disband parties left apart past the grace window."""
    if game is None:
        return
    try:
        from engine.char_index import iter_characters
        chars = list(iter_characters(game))
    except ImportError:
        chars = list(getattr(game, "characters", None) or [])
        if isinstance(chars, dict):
            chars = list(chars.values())
    seen = set()
    for obj in chars:
        if not in_group(obj):
            continue
        leader = resolve_leader(obj)
        if leader is None:
            continue
        lid = id(leader)
        if lid in seen:
            continue
        seen.add(lid)
        maintain_group_colocation(leader, game)


def merge_source_root(caller):
    """Who moves when ``caller`` initiates a group merge.

    Leaders (including solo) drag their whole follow subtree; followers
    in someone else's party only move themselves.
    """
    if caller is None:
        return None
    if is_leader(caller):
        return resolve_leader(caller)
    return caller


def members_to_merge(source_root):
    """Characters that ``merge_groups`` will re-parent (source_root first)."""
    if source_root is None:
        return []
    if is_leader(source_root):
        return members_of(source_root)
    return [source_root]


def merge_groups(source_root, dest_target, game):
    """Merge ``source_root``'s subtree into ``dest_target``'s party.

    Resolves ``dest_target`` to its follow-tree leader, then re-parents
    every member from ``members_to_merge(source_root)`` onto that leader.
    Each member's ``companion_leader_key`` is retargeted only when it
    already pointed at the old subtree leader; unrelated companion bonds
    are left alone.

    Returns the list of characters that actually changed follow bonds
    (empty when nothing moved or args invalid).
    """
    from engine.command_support import start_following

    if source_root is None or dest_target is None:
        return []
    dest_leader = resolve_leader(dest_target)
    if dest_leader is None:
        return []
    actual_source = merge_source_root(source_root)
    if actual_source is None:
        return []
    if same_group(actual_source, dest_leader):
        return []
    to_move = members_to_merge(actual_source)
    if not to_move:
        return []
    # Destination leader must sit outside the moving subtree.
    dest_id = id(dest_leader)
    for member in to_move:
        if member is None:
            continue
        if id(member) == dest_id:
            return []
    old_leader = resolve_leader(actual_source)
    old_leader_key = getattr(old_leader, "key", None) if old_leader else None
    new_leader_key = getattr(dest_leader, "key", None)
    moved = []
    # Re-parent deepest followers first so transitive members are not
    # skipped once the leader moves onto dest_leader.
    ordered = list(to_move)
    if len(ordered) > 1:
        ordered.sort(
            key=lambda m: len(members_of(m)) if is_leader(m) else 1,
            reverse=True,
        )
    for member in ordered:
        if member is None:
            continue
        if member is dest_leader:
            continue
        old_companion = getattr(member, "companion_leader_key", None)
        retarget_companion = (
            old_companion
            and old_leader_key
            and str(old_companion).strip() == str(old_leader_key).strip()
        )
        if not start_following(member, dest_leader):
            continue
        if retarget_companion and new_leader_key:
            member.companion_leader_key = new_leader_key
        moved.append(member)
    return moved


def cadence_may_step(character, *, group_pulled=False):
    """True when Cadence may move this body on its own initiative.

    Leaders and solos always pass. Colocated followers stick (leader pull
    only). Followers who are **apart** may step to catch up during the
  apart-grace window instead of instant disband + critical Obligation.
    """
    if character is None:
        return False
    if group_pulled:
        return True
    if not in_group(character):
        return True
    if is_leader(character):
        return True
    if not all_members_colocated(character):
        return True
    return False


def is_cadence_grouped_follower(character):
    """True when an NPC / Echo / idlemode body is grouped and not leading.

    Live present players keep their own agency (they just cannot walk
    without ``group leave confirm``). Cadence followers lose lifestyle
    until they peel -- they glue-follow the leader and attack the group
    focus (or whoever the leader is fighting).
    """
    if character is None:
        return False
    if not in_group(character) or is_leader(character):
        return False
    return not live_present(character)


def get_focus_key(character):
    """Leader's stored group-focus key, or empty string when unset."""
    leader = resolve_leader(character)
    if leader is None:
        return ""
    ensure_session_defaults(leader)
    raw = getattr(leader, "group_focus_key", None)
    key = str(raw or "").strip()
    return key


def set_focus_key(character, name):
    """Stamp a focus key on the party leader. Returns True on success."""
    leader = resolve_leader(character)
    if leader is None:
        return False
    ensure_session_defaults(leader)
    key = str(name or "").strip()
    if not key:
        leader.group_focus_key = None
        return True
    leader.group_focus_key = key
    return True


def clear_focus(character):
    """Drop the party focus so followers fall back to the leader's fight."""
    return set_focus_key(character, None)


def format_focus_line(character, game=None):
    """One roster line naming the group focus, or empty when solo."""
    if character is None or not in_group(character):
        return ""
    leader = resolve_leader(character)
    if leader is None:
        return ""
    key = get_focus_key(leader)
    if key:
        return f"Focus: {key} -- NPC and Echo mates attack this target."
    fighting = getattr(leader, "target", None)
    if fighting is not None:
        face = (
            getattr(fighting, "assumed_face", None)
            or getattr(fighting, "husk_display_name", None)
            or getattr(fighting, "key", "?")
        )
        return (
            f"Focus: (none) -- mates attack {face} "
            f"(whoever {getattr(leader, 'key', 'the leader')} is fighting)."
        )
    return (
        "Focus: (none) -- mates attack whoever the leader fights. "
        "Type group focus <name> to set one."
    )


def hub_options(character):
    """Nested menu rows: ``(id, label, hint)`` for the group hub.

    Numbered so a player can type ``group 2`` or ``group focus``. Solo
    bodies still get a short form/invite list so the verb is never a
    dead end.
    """
    if character is None or not in_group(character):
        return [
            ("form", "Form a group", "follow <name>, beckon, or party invite"),
            ("invite", "Invite", "party invite <name>"),
        ]
    opts = [
        ("roster", "Roster", "who is in the party"),
        ("focus", "Focus", "who NPC and Echo mates attack"),
        ("invite", "Invite", "ask someone in the room to join"),
        ("merge", "Merge", "fold your group into theirs"),
        ("row", "Row", "front or back (display)"),
    ]
    if is_leader(character):
        opts.append(("disband", "Disband", "break the whole party"))
    else:
        opts.append(("leave", "Leave", "split off -- needs confirm"))
    return opts


def lookup_hub_option(character, token):
    """Resolve a typed number or id to a hub option id, or None."""
    raw = str(token or "").strip().lower()
    if not raw:
        return None
    opts = hub_options(character)
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(opts):
            return opts[index][0]
        return None
    aliases = {
        "form": "form",
        "join": "form",
        "follow": "form",
        "roster": "roster",
        "list": "roster",
        "who": "roster",
        "focus": "focus",
        "target": "focus",
        "invite": "invite",
        "ask": "invite",
        "merge": "merge",
        "row": "row",
        "stance": "row",
        "leave": "leave",
        "split": "leave",
        "peel": "leave",
        "disband": "disband",
        "break": "disband",
    }
    return aliases.get(raw)


def format_group_hub(character, game=None):
    """Framed party menu: roster + numbered nested options.

    Returns a single ``str`` (joined with ``\\r\\n``) so live
    ``Session.send`` never sees a list. Screenreader path uses the same
    ``format_sheet(..., screenreader=)`` family as score / gait.
    """
    from engine import style as style_mod
    from engine import display_prefs as dprefs

    dprefs.ensure_display_defaults(character)
    sr = bool(getattr(character, "screenreader", False))
    width = dprefs.sheet_width(character)
    members = group_members(character) if character is not None else []
    body = []
    if len(members) < 2:
        body.append("You are not in a group.")
        body.append(
            "Follow someone, accept a beckon, or party invite a name "
            "in the room (see help group)."
        )
    else:
        leader = members[0]
        body.append("Your group:")
        for member in members:
            name = getattr(member, "key", "?")
            face = (
                getattr(member, "assumed_face", None)
                or getattr(member, "husk_display_name", None)
                or name
            )
            you = " (you)" if member is character else ""
            role = " leader" if member is leader else ""
            apart = (
                member is not leader
                and not mates_share_group_location(leader, member)
            )
            apart_tag = " (apart)" if apart else ""
            hp, max_hp = member_hp_pair(member)
            row = row_label(member)
            body.append(
                f"{face}  {hp}/{max_hp}hp  {row}{role}{you}{apart_tag}"
            )
        focus_line = format_focus_line(leader, game)
        if focus_line:
            body.append("")
            body.append(focus_line)
        try:
            from engine import hooks
            extra = hooks.group_sheet_extra(character, game)
        except Exception:
            extra = ""
        if extra:
            body.append("")
            for chunk in str(extra).replace("\r\n", "\n").split("\n"):
                if chunk != "":
                    body.append(chunk)
    body.append("")
    body.append("Options")
    for index, (_oid, label, hint) in enumerate(hub_options(character), start=1):
        body.append(f"{index}. {label} -- {hint}")
    body.append("")
    body.append(
        "Type group <number> or group <name>. "
        "Example: group 2 or group focus."
    )
    title = "Your group" if len(members) >= 2 else "Group"
    lines = style_mod.format_sheet(
        title, body, width=width, screenreader=sr,
    )
    return "\r\n".join(str(line) for line in lines)
