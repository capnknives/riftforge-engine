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
    lines = ["", "Your group:"]
    for member in members:
        name = getattr(member, "key", "?")
        face = (
            getattr(member, "assumed_face", None)
            or getattr(member, "husk_display_name", None)
            or name
        )
        you = " (you)" if member is character else ""
        hp, max_hp = member_hp_pair(member)
        row = row_label(member)
        lines.append(f"  {face}  {hp}/{max_hp}hp - {row}{you}")
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
    lines.append("")
    lines.append(
        "Set your row: group front | group back. "
        "Room look tags groupmates with (Group). "
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


def mates_share_group_location(a, b):
    """True when two bodies count as together for group unit rules.

    Same Room object, or same boarded vehicle (cabin vs curb used to
    false-trigger instant disband).
    """
    if a is None or b is None:
        return False
    a_room = getattr(a, "location", None)
    b_room = getattr(b, "location", None)
    if a_room is not None and a_room is b_room:
        return True
    a_vid = getattr(a, "in_vehicle", None)
    b_vid = getattr(b, "in_vehicle", None)
    if a_vid and b_vid and a_vid == b_vid:
        return True
    return False


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


def maintain_group_colocation(leader, game):
    """Disband only after GROUP_APART_DISBAND_TICKS apart (not same tick).

    Returns True when the party was disbanded. While apart but inside the
    grace window, followers may Cadence-step to catch up (see
    ``cadence_may_step``).
    """
    if leader is None or game is None:
        return False
    if not in_group(leader):
        _clear_apart_stamp(leader)
        return False
    if all_members_colocated(leader):
        _clear_apart_stamp(leader)
        return False
    now = _group_tick(game)
    since = getattr(leader, "_group_apart_since_tick", None)
    if since is None:
        leader._group_apart_since_tick = now
        return False
    if (now - int(since)) < GROUP_APART_DISBAND_TICKS:
        return False
    disband_group(leader, game, reason="separated")
    _clear_apart_stamp(leader)
    return True


def live_move_blocked_message(character):
    """Warn when a live follower tries to initiate movement.

    Echo / idlemode bodies use Cadence pathing -- gated separately in
    ``cadence_may_step``. Returns None when movement is allowed.
    """
    if character is None:
        return None
    if getattr(character, "session", None) is None:
        return None
    if not in_group(character):
        return None
    if is_leader(character):
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


def _peel_from_group(member, game, *, reason="group_split"):
    """Remove one body from the follow tree + companion glue."""
    from engine.command_support import stop_following

    # Delegate companion cleanup to engine.hooks so engine stays SUPERS-free
    from engine import hooks

    if getattr(member, "companion_leader_key", None):
        hooks.clear_companion_duty(member, game, reason=reason, silent=True)
    stop_following(member, silent=True)

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
    else:
        msg = "[Group] The party disbands."
    for member in list(members):
        _peel_from_group(member, game, reason=reason)
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
