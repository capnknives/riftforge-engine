"""mail.py -- the engine's generic text-inbox mail kit.

Every MUD that wants asynchronous offline messaging eventually needs the
same shape: a capped list of letters on a character, send from a room
tagged for mail, list/read/discard, and a login notify. That inbox --
not any particular game's rare-dealer consignments or courier gig -- is
what lives here.

SUPERS' town post (``supers/mail.py``) keeps ``ship_item`` (Curio Lux /
rare_dealer) and re-exports these primitives so existing
``mail.send_mail`` / ``mail.inbox`` call sites keep working
(docs/plans/two_repo_purity.md Phase 7 Stage 9). ``mail_inbox`` remains
an optional dynamic attribute (game attach / persist owns defaults).

Pure attribute + room-tag + ``game.find_character`` logic: no networking
beyond Session.send, zero ``supers`` imports.
"""

from __future__ import annotations

# Soft cap so an inbox cannot grow forever; oldest letters drop first.
MAIL_CAP = 30
# Hard cap on letter body length (characters).
MAIL_TEXT_MAX = 500


def is_mail_room(room):
    """True if this room accepts outbound mail (``"mail"`` in resources)."""
    if room is None:
        return False
    return "mail" in (getattr(room, "resources", None) or [])


def inbox(character):
    """Return the character's ``mail_inbox`` list (creates empty if missing)."""
    box = getattr(character, "mail_inbox", None)
    if box is None:
        character.mail_inbox = []
        return character.mail_inbox
    return box


def notify_inbox(character, game=None):
    """Tell an online character they have waiting letters (login hook).

    ``game`` is accepted for the ``after_session_attach`` hook signature
    and unused here (inbox lives on the character).
    """
    session = getattr(character, "session", None)
    if session is None:
        return
    n = len(inbox(character))
    if n <= 0:
        return
    unit = "letter" if n == 1 else "letters"
    session.send(f"You have {n} {unit}. Type 'mail'.")


def send_mail(sender, recipient_name, text, game):
    """Queue a letter on the recipient. Returns (ok, message).

    Privacy: missing name and offline-with-no-character look the same.
    Recipient may be offline (session None) -- letter still queues.
    """
    if not is_mail_room(getattr(sender, "location", None)):
        return False, "There is no post counter here. Try the Post Office."
    name = (recipient_name or "").strip()
    body = (text or "").strip()
    if not name or not body:
        return False, "Usage: mail send <name> <text>"
    if len(body) > MAIL_TEXT_MAX:
        return False, f"Letters are limited to {MAIL_TEXT_MAX} characters."
    if name.lower() == sender.key.lower():
        return False, "You can't mail yourself."
    target = game.find_character(name) if game else None
    if target is None:
        return False, "No one by that name is available."
    box = inbox(target)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0) if game else 0
    box.append({"from": sender.key, "text": body, "tick": ticks})
    while len(box) > MAIL_CAP:
        box.pop(0)
    if getattr(target, "session", None) is not None:
        target.session.send(
            f"A letter arrives from {sender.key}. Type 'mail'."
        )
    return True, f"You send a letter to {target.key}."


def format_list(character):
    """Lines listing the inbox (1-indexed)."""
    box = inbox(character)
    if not box:
        return ["Your cubby is empty."]
    lines = [f"Inbox ({len(box)}/{MAIL_CAP}):"]
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
        return False, "Usage: mail read <number>"
    if n < 1 or n > len(box):
        return False, "No letter with that number."
    letter = box[n - 1]
    return True, (
        f"From {letter.get('from', '?')}:\r\n{letter.get('text', '')}"
    )


def discard_letter(character, which):
    """Discard one letter by 1-based index, or all. Returns (ok, message)."""
    box = inbox(character)
    flag = (which or "").strip().lower()
    if flag in ("all", "*"):
        n = len(box)
        box.clear()
        return True, f"Discarded {n} letter(s)."
    try:
        n = int(flag)
    except (TypeError, ValueError):
        return False, "Usage: mail discard <number|all>"
    if n < 1 or n > len(box):
        return False, "No letter with that number."
    box.pop(n - 1)
    return True, "Letter discarded."
