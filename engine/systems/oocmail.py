"""oocmail.py -- persisted out-of-character letters between players.

Separate from IC post (``mail_inbox``) and phone SMS. Not location- or
plane-gated; tight cap; staff can wipe a character inbox.
"""

from __future__ import annotations

# Small cap -- OOC persistence is a harassment-retention risk.
OOCMAIL_CAP = 10
OOCMAIL_TEXT_MAX = 500


def inbox(character):
    """Return ``oocmail_inbox`` on the character (creates empty if missing)."""
    box = getattr(character, "oocmail_inbox", None)
    if box is None:
        character.oocmail_inbox = []
        return character.oocmail_inbox
    return box


def notify_inbox(character, game=None):
    """Tell an online character they have waiting OOC letters (login hook)."""
    _ = game
    session = getattr(character, "session", None)
    if session is None:
        return
    n = len(inbox(character))
    if n <= 0:
        return
    unit = "OOC letter" if n == 1 else "OOC letters"
    from engine import display_prefs as dp_mod

    session.send(
        dp_mod.format_tag_line(
            character,
            "OOCMAIL",
            f"You have {n} {unit}. Type 'oocmail'.",
            tag_role="gold",
            body_role="ooc",
        )
    )


def send_oocmail(sender, recipient_name, text, game):
    """Queue an OOC letter on the recipient. Returns (ok, message)."""
    name = (recipient_name or "").strip()
    body = (text or "").strip()
    if not name or not body:
        return False, "Usage: oocmail send <name> <text>"
    if len(body) > OOCMAIL_TEXT_MAX:
        return False, (
            f"OOC letters are limited to {OOCMAIL_TEXT_MAX} characters."
        )
    if name.lower() == sender.key.lower():
        return False, "You can't oocmail yourself."
    target = game.find_character(name) if game else None
    if target is None:
        return False, "No one by that name is available."
    box = inbox(target)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0
    box.append({"from": sender.key, "text": body, "tick": ticks})
    while len(box) > OOCMAIL_CAP:
        box.pop(0)
    if getattr(target, "session", None) is not None:
        from engine import display_prefs as dp_mod

        target.session.send(
            dp_mod.format_tag_line(
                target,
                "OOCMAIL",
                f"An OOC letter arrives from {sender.key}. Type 'oocmail'.",
                tag_role="gold",
                body_role="ooc",
            )
        )
    return True, f"You send an OOC letter to {target.key}."


def format_list(character):
    """Lines listing the OOC inbox (1-indexed)."""
    box = inbox(character)
    if not box:
        return ["Your OOC mailbox is empty."]
    lines = [f"OOC inbox ({len(box)}/{OOCMAIL_CAP}):"]
    for i, letter in enumerate(box, start=1):
        preview = letter.get("text", "")
        if len(preview) > 40:
            preview = preview[:37] + "..."
        lines.append(f"  {i}. from {letter.get('from', '?')}: {preview}")
    return lines


def read_letter(character, index):
    """Return (ok, message) for 1-based index."""
    box = inbox(character)
    try:
        n = int(index)
    except (TypeError, ValueError):
        return False, "Usage: oocmail read <number>"
    if n < 1 or n > len(box):
        return False, "No OOC letter with that number."
    letter = box[n - 1]
    return True, (
        f"OOC from {letter.get('from', '?')}:\r\n{letter.get('text', '')}"
    )


def discard_letter(character, which):
    """Discard one OOC letter by 1-based index, or all."""
    box = inbox(character)
    flag = (which or "").strip().lower()
    if flag in ("all", "*"):
        n = len(box)
        box.clear()
        return True, f"Discarded {n} OOC letter(s)."
    try:
        n = int(flag)
    except (TypeError, ValueError):
        return False, "Usage: oocmail discard <number|all>"
    if n < 1 or n > len(box):
        return False, "No OOC letter with that number."
    box.pop(n - 1)
    return True, "OOC letter discarded."


def staff_clear_inbox(target):
    """GM wipe: drop every queued OOC letter. Returns count cleared."""
    box = inbox(target)
    n = len(box)
    box.clear()
    return n
