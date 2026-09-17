"""Resolve gateway / copyover reattach keys for live sessions.

Staff occupying immersion cast (Gabriel, Dean, …) must reattach to that
body after a Veil rewrite, not the ranked PC anchor on the parked spirit.
"""

from __future__ import annotations


def session_copyover_bind_name(session):
    """Return the corporeal Character storage key for this session.

    Never ``gmspirit:`` / ``husk:``. Used by gateway ``bound`` metadata and
    classic copyover state rows so resume skips login.
    """
    from engine.command_support import strip_ephemeral_storage_prefix

    actor = getattr(session, "character", None)
    if actor is None:
        return None

    # Sleep-astral: bind the Earth husk, not the ephemeral dreamself: key.
    if getattr(actor, "is_sleep_dream_clone", False):
        owner_key = getattr(actor, "sleep_dream_owner_key", None)
        if isinstance(owner_key, str) and owner_key.strip():
            return strip_ephemeral_storage_prefix(owner_key)

    # Visible staff cast occupy / playcast -- stay on the immersion body.
    if getattr(actor, "immersion", False):
        staff_occupy = (getattr(actor, "staff_occupy_account", None) or "").strip()
        playcast = bool(getattr(session, "playcast_gm_tools", False))
        if playcast or staff_occupy:
            key = getattr(actor, "key", None)
            if key:
                return strip_ephemeral_storage_prefix(key)

    key_low = (getattr(actor, "key", "") or "").lower()
    in_spirit = (
        getattr(actor, "gm_spirit", False)
        or getattr(actor, "gm_mode", False)
        or key_low.startswith("gmspirit:")
    )
    if in_spirit:
        # Occupy/login body wins over ranked ``gm_body_key`` (bug report 1380).
        return_key = getattr(actor, "gm_return_body_key", None)
        if isinstance(return_key, str) and return_key.strip():
            return strip_ephemeral_storage_prefix(return_key)
        body_key = getattr(actor, "gm_body_key", None)
        if body_key:
            return strip_ephemeral_storage_prefix(body_key)
        return strip_ephemeral_storage_prefix(getattr(actor, "key", None) or "")

    bind = getattr(actor, "gm_body_key", None) or getattr(actor, "key", None)
    if not bind:
        return None
    return strip_ephemeral_storage_prefix(bind)
