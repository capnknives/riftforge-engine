"""
phone.py -- generic physical phones, numbers, plane-local calls.

Ring/voicemail state, dial/answer/hangup shell, and payphone economics live
here. Game-specific switchboard lines (radio station call-ins, etc.) and
Echo phone favors register via hooks (wired in ``supers/bootstrap.py``).

Zero ``supers`` imports.
"""

from __future__ import annotations

import re

from engine.char_index import iter_characters
import engine.systems.economy as economy_wallet

PHONE_CATALOG_IDS = frozenset({"flip_phone", "payphone"})
PAYPHONE_CATALOG_IDS = frozenset({"payphone"})
PORTABLE_PHONE_CATALOG_IDS = frozenset({"flip_phone"})

PAYPHONE_FEE = 1
MAX_CONTACTS = 40
MAX_ALIAS_LEN = 24

_phone_catalog_ids = set(PHONE_CATALOG_IDS)
_payphone_catalog_ids = set(PAYPHONE_CATALOG_IDS)
_portable_catalog_ids = set(PORTABLE_PHONE_CATALOG_IDS)
_special_dial_checker = None
_phone_switchboard = None
_echo_ask_handler = None


def set_phone_catalog_ids(portable, payphone=None):
    """Override default portable / payphone catalog id sets at boot."""
    global _phone_catalog_ids, _payphone_catalog_ids, _portable_catalog_ids
    _portable_catalog_ids = set(portable or ())
    _payphone_catalog_ids = set(payphone or portable or ())
    _phone_catalog_ids = _portable_catalog_ids | _payphone_catalog_ids


def set_phone_special_dial_checker(fn):
    """Register fn(raw) -> bool for non-number dial strings (WKNZ, …)."""
    global _special_dial_checker
    _special_dial_checker = fn


def set_phone_switchboard(fn):
    """Register fn(character, game, number, rest, mode) -> message | None."""
    global _phone_switchboard
    _phone_switchboard = fn


def set_phone_echo_ask_handler(fn):
    """Register fn(caller, echo, game, kind) -> player-facing message."""
    global _echo_ask_handler
    _echo_ask_handler = fn


def _paint(character, role, text):
    """Optional ANSI via style; always keep the plain tag in ``text``."""
    try:
        from engine import style as style_mod
        return style_mod.paint_for(character, role, text)
    except Exception:
        return text


def tag_phone(character=None):
    """Plain + painted [PHONE] -- never color alone."""
    return _paint(character, "teal", "[PHONE]")


def tag_call(character=None):
    """Plain + painted [CALL] -- never color alone."""
    return _paint(character, "teal", "[CALL]")


def deliver_giver_tip_text(character, giver_name, summary, *, kind=None):
    """One-way tip from a mission giver (no ring / answer required)."""
    _ = kind
    if character is None or not summary:
        return False
    name = (giver_name or "someone").strip() or "someone"
    tip = (summary or "").strip()
    from engine import snoop as snoop_module

    if has_portable_phone(character) and getattr(character, "session", None):
        tag = tag_phone(character)
        snoop_module.tell_paragraph(
            character,
            f"{tag} {name}: {tip}",
        )
        return True
    if getattr(character, "session", None) is not None:
        snoop_module.tell_paragraph(
            character,
            f"Word from {name} reaches you -- {tip}",
        )
        return True
    return False


def deliver_official_text(character, sender_label, body):
    """One-way official SMS. Queues for offline handset holders."""
    if character is None or not (body or "").strip():
        return False
    if not has_portable_phone(character):
        return False
    sender = (sender_label or "Sheriff's Office").strip() or "Sheriff's Office"
    msg = f"{tag_phone(character)} {sender}: {(body or '').strip()}"
    session = getattr(character, "session", None)
    if session is not None:
        from engine import snoop as snoop_module
        snoop_module.tell_paragraph(character, msg)
        return True
    pending = getattr(character, "pending_phone_texts", None)
    if not isinstance(pending, list):
        character.pending_phone_texts = []
        pending = character.pending_phone_texts
    pending.append(msg)
    return True


def flush_pending_phone_texts(character):
    """Deliver queued one-way texts after login or on ``phone``."""
    pending = getattr(character, "pending_phone_texts", None)
    if not pending:
        return
    session = getattr(character, "session", None)
    if session is None:
        return
    from engine import snoop as snoop_module
    for line in list(pending):
        snoop_module.tell_paragraph(character, line)
    character.pending_phone_texts = []


def normalize_number(raw):
    """Canonical phone number string, or None if empty/invalid."""
    text = (raw or "").strip().upper()
    if not text:
        return None
    compact = re.sub(r"[^0-9A-Z]+", "", text)
    if not compact:
        return None
    if compact.isdigit() and len(compact) == 7:
        return f"{compact[:3]}-{compact[3:]}"
    if compact.isdigit() and len(compact) == 10:
        return f"{compact[:3]}-{compact[3:6]}-{compact[6:]}"
    return compact if len(compact) <= 16 else compact[:16]


def is_special_dial(raw):
    """True when the dial string targets a registered special line."""
    if _special_dial_checker is not None:
        try:
            return bool(_special_dial_checker(raw))
        except Exception:
            return False
    return False


def is_phone_item(item):
    """True when a world Item is a phone (portable or payphone)."""
    if item is None:
        return False
    cat = getattr(item, "catalog_id", None)
    if cat in _phone_catalog_ids:
        return True
    return bool(getattr(item, "is_phone", False))


def is_payphone_item(item):
    """True when the Item is payphone furniture."""
    if item is None:
        return False
    cat = getattr(item, "catalog_id", None)
    if cat in _payphone_catalog_ids:
        return True
    return bool(getattr(item, "is_payphone", False))


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
    """Stamp a unique ``phone_number`` on a phone Item if missing."""
    if not is_phone_item(item):
        return None
    existing = getattr(item, "phone_number", None)
    if isinstance(existing, str) and existing.strip():
        return normalize_number(existing) or existing.strip().upper()
    if game is None:
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
    """Return (ok, mode, detail) for outbound dial."""
    room = getattr(character, "location", None)
    if room is None:
        return False, None, "You are nowhere."
    if has_portable_phone(character):
        return True, "portable", None
    if room_has_payphone(room):
        coins = economy_wallet.wallet_dollars(character)
        if coins < PAYPHONE_FEE:
            return (
                False,
                "payphone",
                f"The payphone wants {PAYPHONE_FEE} dollars "
                f"(you have {coins}).",
            )
        return True, "payphone", PAYPHONE_FEE
    return (
        False,
        None,
        "You need a phone in hand, or a payphone here "
        "(furniture or a room with a payphone).",
    )


def charge_payphone(character):
    """Deduct payphone fee. Returns True on success."""
    coins = economy_wallet.wallet_dollars(character)
    if coins < PAYPHONE_FEE:
        return False
    economy_wallet.set_wallet(character, coins - PAYPHONE_FEE, 0)
    return True


def find_item_by_number(game, number):
    """Find a phone Item (and optional holder Character) by number."""
    want = normalize_number(number)
    if not want or is_special_dial(want):
        return None, None, None
    rooms = getattr(game, "rooms", None) or {}
    for ch in iter_characters(game):
        for piece in list(getattr(ch, "inventory", None) or []):
            if not is_phone_item(piece):
                continue
            got = normalize_number(getattr(piece, "phone_number", None))
            if got == want:
                return piece, ch, getattr(ch, "location", None)
    for room in rooms.values():
        for obj in list(getattr(room, "contents", None) or []):
            if not is_phone_item(obj):
                continue
            got = normalize_number(getattr(obj, "phone_number", None))
            if got == want:
                return obj, None, room
    return None, None, None


def resolve_dial_target(character, raw, game):
    """Resolve dial args to a number string."""
    text = (raw or "").strip()
    if not text:
        return None, "Dial which number? Try 'dial 555-0142' or 'call dean'."
    alias_key = text.split(None, 1)[0].lower()
    contacts = getattr(character, "phone_contacts", None) or {}
    if isinstance(contacts, dict) and alias_key in contacts:
        return normalize_number(contacts[alias_key]), None
    if " " not in text and alias_key in (contacts or {}):
        return normalize_number(contacts[alias_key]), None
    head = text.split(None, 1)[0]
    if is_special_dial(head):
        return head.upper(), None
    num = normalize_number(head)
    if not num:
        return None, "That does not look like a phone number or saved alias."
    return num, None


def contacts_dict(character):
    """Sanitized alias -> number map on the character."""
    raw = getattr(character, "phone_contacts", None)
    if not isinstance(raw, dict):
        character.phone_contacts = {}
        return character.phone_contacts
    return raw


def save_contact(character, alias, number):
    """Save phonebook alias -> number. Returns (ok, message)."""
    label = (alias or "").strip().lower()
    if not label or not re.match(r"^[a-z][a-z0-9_-]{0,23}$", label):
        return False, (
            "Alias must start with a letter "
            f"(up to {MAX_ALIAS_LEN} letters/digits/_-)."
        )
    if label in ("wknz", "save", "forget", "contacts", "number", "ask"):
        return False, "That alias is reserved."
    num = normalize_number(number)
    if not num or is_special_dial(num):
        return False, "Save a real phone number (not a special line)."
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
    """Session send when present."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def _idlemode_watcher(character):
    """True when this body has a watching Session (idle_mode on).

    Includes SilentSession during npc_do so Cadence phone verbs still
    produce second-person lines for the [Echo] relay.
    """
    if character is None:
        return False
    if not getattr(character, "idle_mode", False):
        return False
    return getattr(character, "session", None) is not None


def _peer_name(peer, fallback="someone"):
    """Display name for a call peer."""
    name = (getattr(peer, "key", None) or fallback or "someone").strip()
    return name or "someone"


def _third_person_to_you(third_line, actor_key):
    """Rewrite ``{Name} verbs…`` / ``{Name}'s …`` for idlemode watchers."""
    if not third_line or not actor_key:
        return third_line
    name = str(actor_key)
    text = str(third_line)
    possessive = f"{name}'s "
    if text.startswith(possessive):
        return "Your " + text[len(possessive):]
    prefix = f"{name} "
    if not text.startswith(prefix):
        return text
    rest = text[len(prefix):]
    if not rest:
        return "You"
    parts = rest.split(None, 1)
    verb = parts[0]
    rem = parts[1] if len(parts) > 1 else ""
    tail = ""
    word = verb
    if verb and not verb[-1].isalpha():
        tail = verb[-1]
        word = verb[:-1]
    if word.endswith("s") and len(word) > 1:
        you_verb = word[:-1]
    elif word.endswith("ies") and len(word) > 3:
        you_verb = word[:-3] + "y"
    else:
        you_verb = word
    you = you_verb + tail
    if rem:
        return f"You {you} {rem}"
    return f"You {you}"


def _send_phone_watcher(character, text):
    """Paragraph-style phone feedback for idlemode spectators."""
    line = (text or "").strip()
    if not line:
        return
    _send(character, f"[Echo] {line}")
    _send(character, "")



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
    room.broadcast(line, exclude=character)
    if _idlemode_watcher(character):
        _send_phone_watcher(
            character,
            _third_person_to_you(line, getattr(character, "key", None) or face),
        )
    else:
        _send(character, f"{tag_phone(character)} {line}")


def hangup_pair(a, b, *, reason="hangup"):
    """End a call between two characters (safe if one is None)."""
    for ch in (a, b):
        if ch is None:
            continue
        call = active_call(ch)
        if call:
            clear_call(ch)
            if reason == "hangup":
                if _idlemode_watcher(ch):
                    _send_phone_watcher(ch, "You hang up the phone.")
                else:
                    _send(ch, f"{tag_call(ch)} Call ended.")
            elif reason == "busy":
                if _idlemode_watcher(ch):
                    _send_phone_watcher(ch, "The line goes dead.")
                else:
                    _send(ch, f"{tag_call(ch)} Line went dead.")
            elif reason == "plane":
                if _idlemode_watcher(ch):
                    _send_phone_watcher(
                        ch, "The signal dies at the plane's edge.",
                    )
                else:
                    _send(
                        ch,
                        f"{tag_call(ch)} The signal dies at the plane's edge.",
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
    if _idlemode_watcher(caller):
        who = _peer_name(callee, callee_number)
        _send_phone_watcher(caller, f"You dial {who}.")
        _send_phone_watcher(caller, "The line rings…")
    else:
        _send(
            caller,
            f"{tag_phone(caller)} Ringing {callee_number}…",
        )
    _send(
        callee,
        f"{tag_phone(callee)} Incoming call from {caller_number} — "
        "type 'answer' or 'hangup'.",
    )
    room = getattr(callee, "location", None)
    if room is not None and getattr(callee, "session", None) is None:
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
    if _idlemode_watcher(caller):
        _send_phone_watcher(caller, "Connected.")
    else:
        _send(caller, f"{tag_call(caller)} Connected.")
    if _idlemode_watcher(callee):
        peer = _peer_name(caller)
        _send_phone_watcher(callee, f"{peer} is on the line.")
    else:
        _send(callee, f"{tag_call(callee)} Connected.")


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
        return f"{tag_call(character)} The signal dies at the plane's edge."
    face = getattr(character, "key", "Someone")
    try:
        from command_support import _display_name
        face = _display_name(character, peer) or face
    except Exception:
        pass
    _send(
        peer,
        f"{tag_call(peer)} {face} (phone): {body}",
    )
    _room_emote_phone(character, "talks into a phone")
    return f"{tag_call(character)} You say (phone): {body}"


def _voicemail_stub(caller, callee_number):
    """Short refusal when Echo has voicemail on."""
    return (
        f"{tag_phone(caller)} {callee_number} — voicemail. "
        "The Echo is not taking calls "
        "('echo voicemail off' on their body)."
    )


def try_echo_auto_answer(caller, callee, game):
    """If callee is an answering Echo, auto-connect. Returns message or None."""
    session = getattr(callee, "session", None)
    acts_echo = False
    if hasattr(callee, "acts_as_echo"):
        try:
            acts_echo = bool(callee.acts_as_echo())
        except Exception:
            acts_echo = session is None
    else:
        acts_echo = session is None

    if session is not None and not acts_echo:
        return None

    if getattr(callee, "folded", False):
        hangup_pair(caller, callee, reason="busy")
        return _voicemail_stub(
            caller, (active_call(caller) or {}).get("peer_number") or "the line"
        )
    if echo_voicemail_on(callee):
        hangup_pair(caller, callee, reason="busy")
        return _voicemail_stub(
            caller, (active_call(caller) or {}).get("peer_number") or "the line"
        )
    connect_call(caller, callee)
    callee_name = _peer_name(callee)
    if _idlemode_watcher(caller):
        _send_phone_watcher(caller, f"{callee_name}'s Echo picks up.")
    else:
        _send(
            caller,
            f"{tag_call(caller)} Their Echo picks up. "
            "Try 'phone ask group|food|water|help'.",
        )
    return None


def handle_echo_ask(caller, echo, game, kind):
    """Cadence-shaped phone favors from an answering Echo."""
    if _echo_ask_handler is not None:
        return _echo_ask_handler(caller, echo, game, kind)
    return (
        "Ask for what? Try 'phone ask group', "
        "'phone ask food', 'phone ask water', or 'phone ask help'."
    )


def dial(character, game, raw_args):
    """Outbound dial. Returns message for the caller."""
    ok, mode, detail = can_place_call(character, game)
    if not ok:
        return detail

    if active_call(character):
        return "You are already on a call. 'hangup' first."

    parts = (raw_args or "").strip().split(None, 1)
    if not parts:
        return "Dial which number? Try 'dial 555-0142' or 'call dean'."
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    number, err = resolve_dial_target(character, head, game)
    if err:
        return err

    if mode == "payphone":
        if not charge_payphone(character):
            return (
                f"The payphone wants {PAYPHONE_FEE} dollars "
                f"(you have {economy_wallet.wallet_dollars(character)})."
            )

    my_phone = primary_handset(character)
    my_number = None
    if my_phone is not None:
        my_number = ensure_phone_number(my_phone, game)
    elif mode == "payphone":
        my_number = "PAYPHONE"

    if is_special_dial(number) and _phone_switchboard is not None:
        msg = _phone_switchboard(character, game, number, rest, mode)
        if msg is not None:
            return msg

    item, holder, _room = find_item_by_number(game, number)
    if item is None:
        return f"{tag_phone(character)} No answer — number not in service."

    if is_payphone_item(item) and holder is None:
        return (
            f"{tag_phone(character)} That payphone does not take inbound "
            "calls — try a handset number."
        )

    if holder is None:
        return f"{tag_phone(character)} No one is carrying that phone."

    if holder is character:
        return "You cannot call your own handset."

    if not same_plane(character, holder):
        return (
            f"{tag_phone(character)} No signal — that phone is on another "
            "plane."
        )

    if active_call(holder):
        return f"{tag_phone(character)} Busy signal."

    callee_number = ensure_phone_number(item, game) or number
    begin_ring(
        character,
        holder,
        caller_number=my_number or "UNKNOWN",
        callee_number=callee_number,
    )
    auto_msg = try_echo_auto_answer(character, holder, game)
    if auto_msg:
        return auto_msg
    call = active_call(character)
    if call and call.get("state") == "connected":
        return f"{tag_call(character)} Connected."
    return f"{tag_phone(character)} Ringing {callee_number}…"


def answer_call(character, game):
    """Accept a ringing call."""
    call = active_call(character)
    if not call or call.get("state") != "ringing":
        return "No incoming call."
    peer = peer_on_call(character, game)
    if peer is None:
        key = call.get("peer_key")
        finder = getattr(game, "find_character", None)
        peer = finder(key) if callable(finder) and key else None
    if peer is None:
        clear_call(character)
        return "The caller hung up."
    if not same_plane(character, peer):
        hangup_pair(character, peer, reason="plane")
        return f"{tag_call(character)} The signal dies at the plane's edge."
    connect_call(peer, character)
    _room_emote_phone(character, "answers a phone")
    return f"{tag_call(character)} Connected."


def hangup(character, game):
    """End ringing or connected call."""
    call = active_call(character)
    if not call:
        return "You are not on a call."
    key = call.get("peer_key")
    finder = getattr(game, "find_character", None)
    peer = finder(key) if callable(finder) and key else None
    hangup_pair(character, peer, reason="hangup")
    _room_emote_phone(character, "hangs up a phone")
    return f"{tag_call(character)} Call ended."


def phone_request_song(character, game, song):
    """Queue a song request via phone (game switchboard when special)."""
    ok, mode, detail = can_place_call(character, game)
    if not ok:
        return detail
    if mode == "payphone" and not charge_payphone(character):
        return (
            f"The payphone wants {PAYPHONE_FEE} dollars "
            f"(you have {economy_wallet.wallet_dollars(character)})."
        )
    title = (song or "").strip()
    if not title:
        return "Request which song? Try 'phone request Dust on the Highway'."
    if _phone_switchboard is not None:
        msg = _phone_switchboard(
            character, game, "SONG_REQUEST", title, mode,
        )
        if msg is not None:
            return msg
    return f"{tag_phone(character)} No request line is available here."


def status_text(character, game):
    """Bare ``phone`` cheat-sheet / status."""
    flush_pending_phone_texts(character)
    lines = [f"{tag_phone(character)} Phone"]
    phones = portable_phones_held(character)
    if phones:
        for p in phones:
            num = ensure_phone_number(p, game) or "?"
            lines.append(f"  Handset: {getattr(p, 'key', 'phone')} — {num}")
    else:
        lines.append("  Handset: (none — buy a flip phone, or use a payphone)")
    room = getattr(character, "location", None)
    if room_has_payphone(room):
        lines.append(f"  Payphone here: yes ({PAYPHONE_FEE} dollars per call)")
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
