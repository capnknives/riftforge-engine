"""
party_invite.py -- explicit party invite handshake (engine-generic).

Groups still *are* the follow tree (``engine.group``). This module adds
the invite/accept step live MUDs expect before someone is glued into
your party -- so strangers are not surprised by an instant follow bond.

Session-only pending state: ``Character._party_invite_from`` (inviter
character key). Not persisted across logout.

``_party_invite_intent`` distinguishes a roster invite (``party``) from
a group-merge request (``merge``) that re-parents the requester's
follow subtree onto the accepter's leader.
"""

from __future__ import annotations

INTENT_PARTY = "party"
INTENT_MERGE = "merge"


def _session(character):
    return getattr(character, "session", None)


def pending_inviter_key(character):
    """Character key of who invited this body, or None."""
    raw = getattr(character, "_party_invite_from", None)
    if not raw:
        return None
    return str(raw).strip() or None


def pending_intent(character):
    """``party`` (join inviter) or ``merge`` (requester's group joins yours)."""
    raw = getattr(character, "_party_invite_intent", None)
    key = str(raw or "").strip().lower()
    if key == INTENT_MERGE:
        return INTENT_MERGE
    return INTENT_PARTY


def pending_merge_source_key(character):
    """Source follow-root key for a pending merge, or None."""
    if pending_intent(character) != INTENT_MERGE:
        return None
    raw = getattr(character, "_party_merge_source_key", None)
    if not raw:
        return None
    return str(raw).strip() or None


def clear_pending(character):
    character._party_invite_from = None
    character._party_invite_intent = None
    character._party_merge_source_key = None


def _set_pending(target, inviter_key, intent, merge_source_key=None):
    target._party_invite_from = inviter_key
    target._party_invite_intent = intent
    if intent == INTENT_MERGE:
        target._party_merge_source_key = merge_source_key
    else:
        target._party_merge_source_key = None


def invite(inviter, target, game):
    """Offer a party slot to ``target`` in the same room.

    Returns (ok, message). On success the target may ``party accept``.
    """
    from engine import group as group_mod
    from world import Character as CharType

    if inviter is None or target is None or inviter is target:
        return False, "Invite who?"
    if not isinstance(target, CharType):
        return False, "They are not here."
    if _session(inviter) is None:
        return False, "You need to be online to form a party."
    if _session(target) is None:
        return False, f"{target.key} is not online."
    inv_room = getattr(inviter, "location", None)
    tgt_room = getattr(target, "location", None)
    if inv_room is None or tgt_room is None or inv_room is not tgt_room:
        return False, f"{target.key} is not in the room with you."
    leader = group_mod.resolve_leader(inviter)
    if leader is not inviter:
        return False, (
            "Only the party leader can invite -- see 'party' / 'help party'."
        )
    if group_mod.same_group(inviter, target):
        return False, f"{target.key} is already in your party."
    if group_mod.in_group(target):
        return False, (
            f"{target.key} is already grouped with someone else."
        )
    _set_pending(target, getattr(inviter, "key", None), INTENT_PARTY)
    return True, (
        f"You invite {target.key} into your party. "
        f"They can type 'party accept' or 'party decline'."
    )


def invite_merge(requester, dest_target, game):
    """Ask to merge ``requester's`` follow subtree into ``dest_target``'s party.

    The destination party leader must ``party accept`` (live players).
    Returns (ok, message, auto_moved) -- ``auto_moved`` is a list when the
  merge ran immediately (Echo / idlemode / close-tie auto-accept).
    """
    from engine import group as group_mod
    from world import Character as CharType

    if requester is None or dest_target is None or requester is dest_target:
        return False, "Merge with whom?", []
    if not isinstance(dest_target, CharType):
        return False, "They are not here.", []
    if _session(requester) is None:
        return False, "You need to be online to merge groups.", []
    req_room = getattr(requester, "location", None)
    tgt_room = getattr(dest_target, "location", None)
    if req_room is None or tgt_room is None or req_room is not tgt_room:
        return False, f"{dest_target.key} is not in the room with you.", []
    dest_leader = group_mod.resolve_leader(dest_target)
    if dest_leader is None:
        return False, "That group is not available.", []
    source_root = group_mod.merge_source_root(requester)
    if source_root is None:
        return False, "You are not in a group to merge.", []
    if group_mod.same_group(source_root, dest_leader):
        return False, f"You are already in {dest_target.key}'s group.", []
    to_move = group_mod.members_to_merge(source_root)
    if not to_move:
        return False, "Nothing to merge.", []
    dest_id = id(dest_leader)
    for member in to_move:
        if member is not None and id(member) == dest_id:
            return False, (
                f"You cannot merge into {dest_target.key} -- "
                "they are already in your group."
            )
    if _merge_auto_accept(requester, dest_target, dest_leader, game):
        moved = group_mod.merge_groups(source_root, dest_target, game)
        if not moved:
            return False, "Could not merge those groups.", []
        return True, _merge_done_message(requester, dest_leader, moved), moved
    if not group_mod.live_present(dest_leader):
        return False, (
            f"{dest_target.key} is not free to accept a merge right now."
        ), []
    if _session(dest_leader) is None:
        return False, f"{dest_leader.key} is not online.", []
    _set_pending(
        dest_leader,
        getattr(requester, "key", None),
        INTENT_MERGE,
        getattr(source_root, "key", None),
    )
    return True, (
        f"You ask to merge your group into {dest_leader.key}'s party. "
        "They can type 'party accept' or 'party decline'."
    ), []


def _merge_auto_accept(requester, dest_target, dest_leader, game):
    """Echo/idlemode / beckon-style auto-accept for group merge."""
    from engine import group as group_mod

    if group_mod.live_present(dest_leader):
        return False
    from engine import hooks as hooks_mod

    if hooks_mod.can_auto_companion(
        requester, dest_target, game, require_close_tie=True,
    ):
        return True
    if dest_target is not dest_leader:
        return hooks_mod.can_auto_companion(
            requester, dest_leader, game, require_close_tie=True,
        )
    return False


def _merge_done_message(requester, dest_leader, moved):
    """Player-facing line after a successful merge."""
    dest_key = getattr(dest_leader, "key", "?")
    if len(moved) <= 1:
        return f"You merge into {dest_key}'s party."
    names = [
        getattr(m, "key", "?")
        for m in moved
        if m is not requester
    ]
    extra = ", ".join(names) if names else "your group"
    return (
        f"Your group merges into {dest_key}'s party "
        f"({extra} fall in behind)."
    )


def accept(accepter, game):
    """Join the party that invited ``accepter``, or accept a merge request."""
    from engine import group as group_mod
    from engine.command_support import start_following
    from world import Character as CharType

    key = pending_inviter_key(accepter)
    if not key:
        return False, "No party invitation pending."
    intent = pending_intent(accepter)
    inviter = game.find_character(key) if game is not None else None
    if inviter is None or not isinstance(inviter, CharType):
        clear_pending(accepter)
        return False, "That invitation expired."
    if intent == INTENT_MERGE:
        return _accept_merge(accepter, inviter, game)
    if _session(inviter) is None:
        clear_pending(accepter)
        return False, "Your inviter is no longer online."
    accepter_room = getattr(accepter, "location", None)
    inviter_room = getattr(inviter, "location", None)
    if accepter_room is None or inviter_room is None or accepter_room is not inviter_room:
        return False, "You need to be in the same room to accept."
    if group_mod.in_group(accepter):
        return False, (
            "Leave your current party first ('party leave confirm')."
        )
    clear_pending(accepter)
    if not start_following(accepter, inviter):
        return False, "Could not join that party."
    inviter.session.send(f"{accepter.key} joins your party.")
    return True, (
        f"You join {inviter.key}'s party. "
        "The leader moves -- you are pulled along (help party)."
    )


def _accept_merge(accepter, requester, game):
    """Leader accepts a pending group-merge from ``requester``."""
    from engine import group as group_mod

    leader = group_mod.resolve_leader(accepter)
    if leader is None or leader is not accepter:
        return False, (
            "Only the party leader can accept a merge -- see 'help group'."
        )
    merge_key = pending_merge_source_key(accepter)
    clear_pending(accepter)
    if _session(requester) is None:
        return False, "The requester is no longer online."
    source_root = None
    if merge_key:
        found = game.find_character(merge_key) if game is not None else None
        if found is not None:
            source_root = group_mod.merge_source_root(found)
    if source_root is None:
        source_root = group_mod.merge_source_root(requester)
    if source_root is None:
        return False, "That merge request expired."
    accepter_room = getattr(accepter, "location", None)
    req_room = getattr(requester, "location", None)
    if accepter_room is None or req_room is None or accepter_room is not req_room:
        return False, "You need to be in the same room to accept."
    moved = group_mod.merge_groups(source_root, accepter, game)
    if not moved:
        return False, "Could not merge those groups."
    msg = _merge_done_message(requester, accepter, moved)
    if requester.session is not None:
        requester.session.send(msg)
    return True, (
        f"{requester.key}'s group merges into yours. "
        f"({len(moved)} member(s) fall in behind.)"
    )


def decline(decliner, game):
    """Refuse a pending party invite or merge request."""
    key = pending_inviter_key(decliner)
    if not key:
        return False, "No party invitation pending."
    intent = pending_intent(decliner)
    inviter = game.find_character(key) if game is not None else None
    clear_pending(decliner)
    if inviter is not None and _session(inviter) is not None:
        if intent == INTENT_MERGE:
            inviter.session.send(
                f"{decliner.key} declines your group merge request."
            )
        else:
            inviter.session.send(f"{decliner.key} declines your party invite.")
    if intent == INTENT_MERGE:
        return True, "You decline the group merge request."
    return True, "You decline the party invite."


def format_pending_line(character):
    """One-line hint for roster output, or empty string."""
    key = pending_inviter_key(character)
    if not key:
        return ""
    if pending_intent(character) == INTENT_MERGE:
        return (
            f"Group merge pending from {key} -- "
            "'party accept' or 'party decline'."
        )
    return f"Party invite pending from {key} -- 'party accept' or 'party decline'."
