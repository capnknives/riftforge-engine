"""phone.py -- generic ring/dial/contacts/voicemail-shell phone framework.

Plane-local signal, physical handset numbers, phonebook aliases, payphone
fee hook, and connected-call private speech. Game flavor (WKNZ, styled tags,
Echo auto-answer, Cadence asks) registers via ``engine.hooks`` and/or wraps
``dial`` in the game package (docs/plans/two_repo_purity.md H7a).

No networking. Zero ``import supers``.
"""

from __future__ import annotations

import re

from engine import hooks
from engine.char_index import iter_characters
import engine.systems.economy as economy_wallet

MAX_CONTACTS = 40
MAX_ALIAS_LEN = 24

# Active-call / ring state lives on Character for the process lifetime
# (not persisted -- hang up on logout / copyover is fine for v1).


def normalize_number(raw):
    """Canonical phone number string, or None if empty/invalid."""
    text = (raw or "").strip().upper()
    if not text:
        return None
    # Allow 555-0142, 5550142, 555.0142
    compact = re.sub(r"[^0-9A-Z]+", "", text)
    if not compact:
        return None
    # Pretty-print 555XXXX -> 555-XXXX when all digits and length 7.
    if compact.isdigit() and len(compact) == 7:
        return f"{compact[:3]}-{compact[3:]}"
    if compact.isdigit() and len(compact) == 10:
        return f"{compact[:3]}-{compact[3:6]}-{compact[6:]}"
    # Keep WKNZ-shaped aliases readable.
    if compact.lower() in ("wknz", "555wknz"):
        return "555-WKNZ"
    return compact if len(compact) <= 16 else compact[:16]


def _is_service_number(number):
    """True when ``number`` is a non-handset service line (e.g. WKNZ)."""
    norm = normalize_number(number)
    if not norm:
        return False
    key = re.sub(r"[^0-9A-Z]+", "", norm).lower()
    return key in ("wknz", "555wknz") or norm.upper() == "555-WKNZ"


def is_phone_item(item):
    """True when a world Item is a phone (portable or payphone)."""
    if item is None:
        return False
    return bool(getattr(item, "is_phone", False))


def is_payphone_item(item):
    """True when the Item is payphone furniture."""
    if item is None:
        return False
    if bool(getattr(item, "is_payphone", False)):
        return True
    return False


def is_portable_phone(item):
    """True when the Item is a carried handset."""
    if not is_phone_item(item):
        return False
    return not is_payphone_item(item)


def room_desc_mentions_payphone(room):
    """True when room description text mentions a payphone."""
    if room is None:
        return False
    desc = (getattr(room, "description", None) or "").lower()
    return "payphone" in desc or "pay phone" in desc


def room_has_payphone_item(room):
    """True when the room contains a payphone catalog Item."""
    if room is None:
        return False
    for obj in list(getattr(room, "contents", None) or []):
        if is_payphone_item(obj):
            return True
    return False


def room_has_payphone(room):
    """Payphone usable here (furniture item or description mention)."""
    return room_has_payphone_item(room) or room_desc_mentions_payphone(room)


def character_plane(character):
    """Plane id for the character's current room (default earth)."""
    room = getattr(character, "location", None)
    if room is None:
        return "earth"
    return getattr(room, "plane", None) or "earth"


def same_plane(a, b):
    """True when both characters share a plane for signal."""
    if a is None or b is None:
        return False
    return character_plane(a) == character_plane(b)


def _next_number(game):
    """Allocate the next 555-XXXX style number from game meta."""
    n = int(getattr(game, "next_phone_seq", 0) or 0) + 1
    if n < 1000:
        n = 1000
    if n > 9999:
        n = 1000 + (n % 9000)
    game.next_phone_seq = n
    return f"555-{n:04d}"


def ensure_phone_number(item, game=None):
    """Stamp a unique ``phone_number`` on a phone Item if missing.

    Returns the number string, or None when ``item`` is not a phone.
    """
    if not is_phone_item(item):
        return None
    existing = getattr(item, "phone_number", None)
    if isinstance(existing, str) and existing.strip():
        return normalize_number(existing) or existing.strip().upper()
    if game is None:
        # No game yet (catalog preview) -- leave unset until spawn.
        return None
    number = _next_number(game)
    item.phone_number = number
    return number


def stamp_phone_on_spawn(item, game):
    """Hook for make_world_item / seed: ensure number on stamped phones."""
    if not is_phone_item(item):
        return
    if is_payphone_item(item):
        item.furniture = True
    ensure_phone_number(item, game)


def portable_phones_held(character):
    """List portable phone Items in inventory (not furniture)."""
    out = []
    for piece in list(getattr(character, "inventory", None) or []):
        if is_portable_phone(piece):
            out.append(piece)
    return out


def has_portable_phone(character):
    """True when the character carries at least one flip phone."""
    return bool(portable_phones_held(character))


def primary_handset(character):
    """First portable phone in inventory, or None."""
    phones = portable_phones_held(character)
    return phones[0] if phones else None


def can_place_call(character, game):
    """Return (ok, mode, detail) for outbound dial.

    mode is ``portable`` or ``payphone``. detail is refusal text or fee.
    """
    _ = game
    room = getattr(character, "location", None)
    if room is None:
        return False, None, "You are nowhere."
    fee = hooks.phone_payphone_fee()
    if has_portable_phone(character):
        return True, "portable", None
    if room_has_payphone(room):
        coins = economy_wallet.wallet_dollars(character)
        if coins < fee:
            return (
                False,
                "payphone",
                f"The payphone wants {fee} dollars "
                f"(you have {coins}).",
            )
        return True, "payphone", fee
    return (
        False,
        None,
        "You need a phone in hand, or a payphone here "
        "(furniture or a room with a payphone).",
    )


def charge_payphone(character):
    """Deduct payphone fee. Returns True on success."""
    fee = hooks.phone_payphone_fee()
    coins = economy_wallet.wallet_dollars(character)
    if coins < fee:
        return False
    economy_wallet.set_wallet(character, coins - fee, 0)
    return True


def find_item_by_number(game, number):
    """Find a phone Item (and optional holder Character) by number.

    Returns (item, holder_or_none, room_or_none).
    """
    want = normalize_number(number)
    if not want or _is_service_number(want):
        return None, None, None
    rooms = getattr(game, "rooms", None) or {}
    # Characters (inventory).
    for ch in iter_characters(game):
        for piece in list(getattr(ch, "inventory", None) or []):
            if not is_phone_item(piece):
                continue
            got = normalize_number(getattr(piece, "phone_number", None))
            if got == want:
                return piece, ch, getattr(ch, "location", None)
    # Room furniture / floor.
    for room in rooms.values():
        for obj in list(getattr(room, "contents", None) or []):
            if not is_phone_item(obj):
                continue
            got = normalize_number(getattr(obj, "phone_number", None))
            if got == want:
                return obj, None, room
    return None, None, None


def _resolve_dial_number(character, raw_head, game):
    """Resolve dial head to a number via hook then default phonebook/number."""
    resolved = hooks.phone_dial_alias_resolver(raw_head, character, game)
    if resolved is not None:
        return normalize_number(resolved), None
    text = (raw_head or "").strip()
    if not text:
        return None, "Dial which number? Try 'dial 555-0142' or 'call dean'."
    alias_key = text.split(None, 1)[0].lower()
    contacts = getattr(character, "phone_contacts", None) or {}
    if isinstance(contacts, dict) and alias_key in contacts:
        return normalize_number(contacts[alias_key]), None
    if " " not in text and alias_key in (contacts or {}):
        return normalize_number(contacts[alias_key]), None
    num = normalize_number(text.split(None, 1)[0])
    if not num:
        return None, "That does not look like a phone number or saved alias."
    return num, None


def contacts_dict(character):
    """Sanitized alias → number map on the character."""
    raw = getattr(character, "phone_contacts", None)
    if not isinstance(raw, dict):
        character.phone_contacts = {}
        return character.phone_contacts
    return raw


def save_contact(character, alias, number):
    """Save phonebook alias → number. Returns (ok, message)."""
    label = (alias or "").strip().lower()
    if not label or not re.match(r"^[a-z][a-z0-9_-]{0,23}$", label):
        return False, (
            "Alias must start with a letter "
            f"(up to {MAX_ALIAS_LEN} letters/digits/_-)."
        )
    if label in ("wknz", "save", "forget", "contacts", "number", "ask"):
        return False, "That alias is reserved."
    num = normalize_number(number)
    if not num or _is_service_number(num):
        return False, "Save a real phone number (not WKNZ)."
    book = contacts_dict(character)
    if label not in book and len(book) >= MAX_CONTACTS:
        return False, f"Phonebook full ({MAX_CONTACTS} aliases)."
    book[label] = num
    character.phone_contacts = book
    return True, f"Saved {label} → {num}."


def forget_contact(character, alias):
    """Drop a phonebook alias. Returns (ok, message)."""
    label = (alias or "").strip().lower()
    book = contacts_dict(character)
    if label not in book:
        return False, f"No saved alias '{label}'."
    del book[label]
    return True, f"Forgot {label}."


def echo_voicemail_on(character):
    """True when this Echo declines phone pickup."""
    return bool(getattr(character, "echo_voicemail", False))


def active_call(character):
    """Return the live call dict, or None."""
    call = getattr(character, "phone_call", None)
    return call if isinstance(call, dict) else None


def clear_call(character):
    """Drop active / ringing call state."""
    character.phone_call = None


def _send(character, text):
    """Session send when present (Echoes without Session stay silent)."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _room_emote_phone(character, verb_phrase):
    """Roommates see a labeled emote; caller also gets it if Sessioned."""
    room = getattr(character, "location", None)
    if room is None:
        return
    face = getattr(character, "key", "Someone")
    try:
        from command_support import _display_name

        face = _display_name(character, None) or face
    except Exception:
        pass
    line = f"{face} {verb_phrase}."
    # Tagged for a11y when we send to the actor; room broadcast is plain.
    room.broadcast(line, exclude=character)
    tag = hooks.phone_tag(character)
    _send(character, f"{tag} {line}" if tag else line)


def hangup_pair(a, b, *, reason="hangup"):
    """End a call between two characters (safe if one is None)."""
    for ch in (a, b):
        if ch is None:
            continue
        call = active_call(ch)
        if call:
            clear_call(ch)
            tag = hooks.phone_call_tag(ch)
            if reason == "hangup":
                _send(ch, f"{tag} Call ended." if tag else "Call ended.")
            elif reason == "busy":
                _send(ch, f"{tag} Line went dead." if tag else "Line went dead.")
            elif reason == "plane":
                _send(
                    ch,
                    f"{tag} The signal dies at the plane's edge."
                    if tag
                    else "The signal dies at the plane's edge.",
                )


def begin_ring(caller, callee, *, caller_number, callee_number):
    """Start ringing both sides. Callee must ``answer`` (or Echo auto)."""
    caller.phone_call = {
        "state": "dialing",
        "peer_key": getattr(callee, "key", None),
        "peer_number": callee_number,
        "my_number": caller_number,
        "outbound": True,
    }
    callee.phone_call = {
        "state": "ringing",
        "peer_key": getattr(caller, "key", None),
        "peer_number": caller_number,
        "my_number": callee_number,
        "outbound": False,
    }
    _room_emote_phone(caller, "dials a number and holds a phone to their ear")
    ptag = hooks.phone_tag(caller)
    ctag = hooks.phone_tag(callee)
    _send(
        caller,
        f"{ptag} Ringing {callee_number}…" if ptag else f"Ringing {callee_number}…",
    )
    _send(
        callee,
        (
            f"{ctag} Incoming call from {caller_number} — "
            "type 'answer' or 'hangup'."
        )
        if ctag
        else (
            f"Incoming call from {caller_number} — "
            "type 'answer' or 'hangup'."
        ),
    )
    room = getattr(callee, "location", None)
    if room is not None and getattr(callee, "session", None) is None:
        # Offline Echo in the room: soft tell for watchers / snoops.
        face = getattr(callee, "key", "Someone")
        room.broadcast(
            f"{face}'s phone rings.",
            exclude=None,
        )


def connect_call(caller, callee):
    """Mark both sides connected after answer / Echo pickup."""
    for ch, peer in ((caller, callee), (callee, caller)):
        call = active_call(ch) or {}
        call["state"] = "connected"
        call["peer_key"] = getattr(peer, "key", None)
        ch.phone_call = call
    ctag = hooks.phone_call_tag(caller)
    _send(caller, f"{ctag} Connected." if ctag else "Connected.")
    ctag = hooks.phone_call_tag(callee)
    _send(callee, f"{ctag} Connected." if ctag else "Connected.")


def peer_on_call(character, game):
    """Return the other Character on an active call, or None."""
    call = active_call(character)
    if not call:
        return None
    key = call.get("peer_key")
    if not key:
        return None
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return None
    peer = finder(key)
    if peer is None:
        return None
    # Peer must still think they are on this call.
    peer_call = active_call(peer)
    if not peer_call or peer_call.get("peer_key") != getattr(
        character, "key", None
    ):
        return None
    return peer


def phone_say(character, game, text):
    """Private line speech + room emote. Returns player message."""
    body = (text or "").strip()
    if not body:
        return "Say what? Try 'phone say hello'."
    call = active_call(character)
    if not call or call.get("state") != "connected":
        return "You are not on a connected call."
    peer = peer_on_call(character, game)
    if peer is None:
        clear_call(character)
        return "The line is dead."
    if not same_plane(character, peer):
        hangup_pair(character, peer, reason="plane")
        tag = hooks.phone_call_tag(character)
        msg = "The signal dies at the plane's edge."
        return f"{tag} {msg}" if tag else msg
    face = getattr(character, "key", "Someone")
    try:
        from command_support import _display_name

        face = _display_name(character, peer) or face
    except Exception:
        pass
    ptag = hooks.phone_call_tag(peer)
    _send(
        peer,
        f"{ptag} {face} (phone): {body}" if ptag else f"{face} (phone): {body}",
    )
    _room_emote_phone(character, "talks into a phone")
    tag = hooks.phone_call_tag(character)
    you = f"You say (phone): {body}"
    return f"{tag} {you}" if tag else you


def _voicemail_stub(caller, callee_number):
    """Short refusal when Echo has voicemail on."""
    return hooks.phone_voicemail_line(caller, callee_number)


def dial(character, game, raw_args):
    """Outbound dial to a handset number. Returns message for the caller."""
    ok, mode, detail = can_place_call(character, game)
    if not ok:
        return detail

    # Already on a call?
    if active_call(character):
        return "You are already on a call. 'hangup' first."

    parts = (raw_args or "").strip().split(None, 1)
    if not parts:
        return "Dial which number? Try 'dial 555-0142' or 'call dean'."
    head = parts[0]

    number, err = _resolve_dial_number(character, head, game)
    if err:
        return err

    fee = hooks.phone_payphone_fee()
    # Payphone fee at start of successful attempt.
    if mode == "payphone":
        if not charge_payphone(character):
            return (
                f"The payphone wants {fee} dollars "
                f"(you have {economy_wallet.wallet_dollars(character)})."
            )

    my_phone = primary_handset(character)
    my_number = None
    if my_phone is not None:
        my_number = ensure_phone_number(my_phone, game)
    elif mode == "payphone":
        # Outbound-only booth: ephemeral caller-id.
        my_number = "PAYPHONE"

    # Service numbers (WKNZ, …) are handled by the game ``dial`` wrapper.
    if _is_service_number(number):
        return (
            f"{hooks.phone_tag(character)} No answer — number not in service."
            if hooks.phone_tag(character)
            else "No answer — number not in service."
        )

    item, holder, _room = find_item_by_number(game, number)
    if item is None:
        ptag = hooks.phone_tag(character)
        return (
            f"{ptag} No answer — number not in service."
            if ptag
            else "No answer — number not in service."
        )

    if is_payphone_item(item) and holder is None:
        ptag = hooks.phone_tag(character)
        return (
            f"{ptag} That payphone does not take inbound "
            "calls — try a handset number."
        )

    if holder is None:
        ptag = hooks.phone_tag(character)
        return (
            f"{ptag} No one is carrying that phone."
            if ptag
            else "No one is carrying that phone."
        )

    if holder is character:
        return "You cannot call your own handset."

    if not same_plane(character, holder):
        ptag = hooks.phone_tag(character)
        return (
            f"{ptag} No signal — that phone is on another "
            "plane."
        )

    if active_call(holder):
        ptag = hooks.phone_tag(character)
        return f"{ptag} Busy signal." if ptag else "Busy signal."

    callee_number = ensure_phone_number(item, game) or number
    begin_ring(
        character,
        holder,
        caller_number=my_number or "UNKNOWN",
        callee_number=callee_number,
    )
    call = active_call(character)
    if call and call.get("state") == "connected":
        ctag = hooks.phone_call_tag(character)
        return f"{ctag} Connected." if ctag else "Connected."
    ptag = hooks.phone_tag(character)
    return (
        f"{ptag} Ringing {callee_number}…"
        if ptag
        else f"Ringing {callee_number}…"
    )


def answer_call(character, game):
    """Accept a ringing call."""
    call = active_call(character)
    if not call or call.get("state") != "ringing":
        return "No incoming call."
    peer = peer_on_call(character, game)
    if peer is None:
        # peer_on_call requires mutual peer_key; during ring both have state.
        key = call.get("peer_key")
        finder = getattr(game, "find_character", None)
        peer = finder(key) if callable(finder) and key else None
    if peer is None:
        clear_call(character)
        return "The caller hung up."
    if not same_plane(character, peer):
        hangup_pair(character, peer, reason="plane")
        tag = hooks.phone_call_tag(character)
        msg = "The signal dies at the plane's edge."
        return f"{tag} {msg}" if tag else msg
    connect_call(peer, character)
    _room_emote_phone(character, "answers a phone")
    tag = hooks.phone_call_tag(character)
    return f"{tag} Connected." if tag else "Connected."


def hangup(character, game):
    """End ringing or connected call."""
    _ = game
    call = active_call(character)
    if not call:
        return "You are not on a call."
    key = call.get("peer_key")
    finder = getattr(game, "find_character", None)
    peer = finder(key) if callable(finder) and key else None
    hangup_pair(character, peer, reason="hangup")
    _room_emote_phone(character, "hangs up a phone")
    tag = hooks.phone_call_tag(character)
    return f"{tag} Call ended." if tag else "Call ended."


def status_text(character, game):
    """Bare ``phone`` cheat-sheet / status."""
    lines = [f"{hooks.phone_tag(character)} Phone" if hooks.phone_tag(character) else "Phone"]
    phones = portable_phones_held(character)
    if phones:
        for p in phones:
            num = ensure_phone_number(p, game) or "?"
            lines.append(f"  Handset: {getattr(p, 'key', 'phone')} — {num}")
    else:
        lines.append("  Handset: (none — buy a flip phone, or use a payphone)")
    room = getattr(character, "location", None)
    fee = hooks.phone_payphone_fee()
    if room_has_payphone(room):
        lines.append(f"  Payphone here: yes ({fee} dollars per call)")
    else:
        lines.append("  Payphone here: no")
    call = active_call(character)
    if call:
        lines.append(
            f"  Call: {call.get('state')} "
            f"peer={call.get('peer_key')} "
            f"their#={call.get('peer_number')}"
        )
    else:
        lines.append("  Call: (none)")
    book = contacts_dict(character)
    if book:
        bits = ", ".join(f"{a}={n}" for a, n in sorted(book.items())[:8])
        more = "" if len(book) <= 8 else f" (+{len(book) - 8} more)"
        lines.append(f"  Contacts: {bits}{more}")
    else:
        lines.append("  Contacts: (none — 'phone save <alias> <number>')")
    vm = "on" if echo_voicemail_on(character) else "off"
    lines.append(f"  Echo voicemail: {vm} ('echo voicemail on|off')")
    lines.append(
        "  Try: dial/call <number|alias> | answer | hangup | "
        "phone say <text> | phone ask group|food|water|help | "
        "phone request <song> | phone save|forget|contacts"
    )
    return "\r\n".join(lines)
