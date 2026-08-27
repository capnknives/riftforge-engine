"""
party_invite.py -- explicit party invite handshake (engine-generic).

Groups still *are* the follow tree (``engine.group``). This module adds
the invite/accept step live MUDs expect before someone is glued into
your party -- so strangers are not surprised by an instant follow bond.

Session-only pending state: ``Character._party_invite_from`` (inviter
character key). Not persisted across logout.
"""

from __future__ import annotations


def _session(character):
    return getattr(character, "session", None)


def pending_inviter_key(character):
    """Character key of who invited this body, or None."""
    raw = getattr(character, "_party_invite_from", None)
    if not raw:
        return None
    return str(raw).strip() or None


def clear_pending(character):
    character._party_invite_from = None


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
    target._party_invite_from = getattr(inviter, "key", None)
    return True, (
        f"You invite {target.key} into your party. "
        f"They can type 'party accept' or 'party decline'."
    )


def accept(accepter, game):
    """Join the party that invited ``accepter`` (follow bond)."""
    from engine import group as group_mod
    from engine.command_support import start_following
    from world import Character as CharType

    key = pending_inviter_key(accepter)
    if not key:
        return False, "No party invitation pending."
    inviter = game.find_character(key) if game is not None else None
    if inviter is None or not isinstance(inviter, CharType):
        clear_pending(accepter)
        return False, "That invitation expired."
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


def decline(decliner, game):
    """Refuse a pending party invite."""
    key = pending_inviter_key(decliner)
    if not key:
        return False, "No party invitation pending."
    inviter = game.find_character(key) if game is not None else None
    clear_pending(decliner)
    if inviter is not None and _session(inviter) is not None:
        inviter.session.send(f"{decliner.key} declines your party invite.")
    return True, "You decline the party invite."


def format_pending_line(character):
    """One-line hint for roster output, or empty string."""
    key = pending_inviter_key(character)
    if not key:
        return ""
    return f"Party invite pending from {key} -- 'party accept' or 'party decline'."
