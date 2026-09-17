"""
phone.py -- generic physical phones, numbers, plane-local calls.

Ring/voicemail state, dial/answer/hangup shell, and payphone economics live
here. Game-specific switchboard lines (radio station call-ins, etc.) and
Echo phone favors register via hooks (wired in ``supers/bootstrap.py``).

Zero ``supers`` imports.
"""

from __future__ import annotations

import difflib
import re

from engine.char_index import iter_characters
import engine.systems.economy as economy_wallet

PHONE_CATALOG_IDS = frozenset({"flip_phone", "smartphone", "payphone"})
PAYPHONE_CATALOG_IDS = frozenset({"payphone"})
PORTABLE_PHONE_CATALOG_IDS = frozenset({"flip_phone", "smartphone"})
LANDLINE_CATALOG_IDS = frozenset({"house_landline", "motel_phone"})

PAYPHONE_FEE = 1
MAX_CONTACTS = 40
MAX_ALIAS_LEN = 24
# Player-to-player SMS body cap (shorter than IC mail).
SMS_TEXT_MAX = 320
# Handset inbox caps (threads live on the Item, not the character).
THREAD_MSG_CAP = 40
THREAD_LIST_CAP = 20
VOICEMAIL_CAP = 15
OFFICIAL_THREAD_PREFIX = "official:"

_phone_catalog_ids = set(PHONE_CATALOG_IDS)
_payphone_catalog_ids = set(PAYPHONE_CATALOG_IDS)
_portable_catalog_ids = set(PORTABLE_PHONE_CATALOG_IDS)
_landline_catalog_ids = set(LANDLINE_CATALOG_IDS)
_special_dial_checker = None
_phone_switchboard = None
_echo_ask_handler = None


def set_phone_catalog_ids(portable, payphone=None, landline=None):
    """Override default portable / payphone / landline catalog id sets at boot."""
    global _phone_catalog_ids, _payphone_catalog_ids, _portable_catalog_ids
    global _landline_catalog_ids
    _portable_catalog_ids = set(portable or ())
    _payphone_catalog_ids = set(payphone or ())
    _landline_catalog_ids = set(landline or ())
    _phone_catalog_ids = (
        _portable_catalog_ids | _payphone_catalog_ids | _landline_catalog_ids
    )


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


def _game_ticks(game):
    """Current game tick counter for thread timestamps."""
    if game is None:
        return 0
    return int(getattr(game, "game_time_ticks", 0) or 0)


def _format_thread_time(game, ticks):
    """Player-facing time stamp for SMS / voicemail rows (game-day clock)."""
    try:
        ticks = int(ticks or 0)
    except (TypeError, ValueError):
        ticks = 0
    if ticks <= 0:
        return "?"
    try:
        from engine import game_calendar as cal_mod

        cal = cal_mod.breakdown(ticks)
        return cal_mod.format_clock(cal)
    except Exception:
        return f"tick {ticks}"


def _ensure_sms_threads(item):
    """Return a mutable sms_threads list on a handset Item."""
    raw = getattr(item, "sms_threads", None)
    if not isinstance(raw, list):
        item.sms_threads = []
        return item.sms_threads
    return raw


def _ensure_voicemail(item):
    """Return a mutable voicemail list on a handset Item."""
    raw = getattr(item, "voicemail", None)
    if not isinstance(raw, list):
        item.voicemail = []
        return item.voicemail
    return raw


def official_thread_key(sender_label):
    """Stable thread peer id for WEA / sheriff / system SMS."""
    label = (sender_label or "Sheriff's Office").strip() or "Sheriff's Office"
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "official"
    return f"{OFFICIAL_THREAD_PREFIX}{slug}"


def _official_sender_label(peer_key):
    """Turn ``official:sheriff`` back into player prose."""
    if not peer_key or not str(peer_key).startswith(OFFICIAL_THREAD_PREFIX):
        return None
    slug = str(peer_key)[len(OFFICIAL_THREAD_PREFIX):]
    if not slug:
        return "Official"
    return slug.replace("_", " ").title()


def _find_thread(threads, peer_key):
    """Return the thread dict for ``peer_key``, or None."""
    want = (peer_key or "").strip()
    if not want:
        return None
    for row in threads:
        if isinstance(row, dict) and (row.get("peer") or "").strip() == want:
            return row
    return None


def _append_thread_message(item, peer_key, *, from_label, body, at_ticks):
    """Append one SMS row to a handset thread (caps enforced)."""
    if item is None or not (body or "").strip():
        return
    threads = _ensure_sms_threads(item)
    want = (peer_key or "").strip()
    if not want:
        return
    row = _find_thread(threads, want)
    if row is None:
        row = {"peer": want, "messages": []}
        threads.insert(0, row)
    msgs = row.get("messages")
    if not isinstance(msgs, list):
        msgs = []
        row["messages"] = msgs
    msgs.append({
        "from": (from_label or "?").strip() or "?",
        "at": int(at_ticks or 0),
        "body": (body or "").strip(),
    })
    if len(msgs) > THREAD_MSG_CAP:
        row["messages"] = msgs[-THREAD_MSG_CAP:]
    # Most-recent thread first.
    threads[:] = [row] + [t for t in threads if t is not row]
    if len(threads) > THREAD_LIST_CAP:
        del threads[THREAD_LIST_CAP:]


def append_sms_to_handset(item, peer_key, *, from_label, body, game):
    """Record one inbound/outbound SMS on a portable handset Item."""
    if item is None or not is_portable_phone(item):
        return
    _append_thread_message(
        item,
        peer_key,
        from_label=from_label,
        body=body,
        at_ticks=_game_ticks(game),
    )


def append_voicemail(item, *, from_label, body, game):
    """Append a missed-call tape row on a portable handset."""
    if item is None or not is_portable_phone(item):
        return
    tape = _ensure_voicemail(item)
    tape.insert(0, {
        "from": (from_label or "?").strip() or "?",
        "at": _game_ticks(game),
        "body": (body or "").strip() or "Missed call.",
    })
    if len(tape) > VOICEMAIL_CAP:
        del tape[VOICEMAIL_CAP:]


def _contact_alias_for_number(character, number):
    """Reverse phonebook lookup: number -> saved alias (if any)."""
    want = normalize_number(number)
    if not want:
        return None
    book = getattr(character, "phone_contacts", None) or {}
    if not isinstance(book, dict):
        return None
    for alias, num in book.items():
        if normalize_number(num) == want:
            return alias
    return None


def peer_display_label(character, peer_key, game=None):
    """Player prose for a thread peer (alias, number, or official sender)."""
    _ = game
    official = _official_sender_label(peer_key)
    if official:
        return official
    num = normalize_number(peer_key) or (peer_key or "?")
    alias = _contact_alias_for_number(character, num)
    if alias:
        return f"{alias.title()} ({num})"
    return num


def sms_threads_on_primary(character):
    """Thread list on the primary portable (empty when none)."""
    phone = primary_handset(character)
    if phone is None:
        return []
    return list(_ensure_sms_threads(phone))


def voicemail_on_primary(character):
    """Voicemail tape on the primary portable."""
    phone = primary_handset(character)
    if phone is None:
        return []
    return list(_ensure_voicemail(phone))


def format_sms_thread_list(character, game):
    """Numbered inbox for ``phone texts``."""
    phone = primary_handset(character)
    if phone is None:
        return "You are not carrying a voice handset."
    threads = _ensure_sms_threads(phone)
    if not threads:
        return (
            f"{tag_phone(character)} No text threads yet. "
            "Try phone text <alias|number> <message>."
        )
    lines = [f"{tag_phone(character)} Text threads:"]
    for idx, row in enumerate(threads[:THREAD_LIST_CAP], start=1):
        if not isinstance(row, dict):
            continue
        peer = row.get("peer") or "?"
        label = peer_display_label(character, peer, game)
        msgs = row.get("messages") or []
        preview = ""
        if msgs and isinstance(msgs[-1], dict):
            preview = (msgs[-1].get("body") or "").strip()
            if len(preview) > 48:
                preview = preview[:45] + "..."
        count = len(msgs) if isinstance(msgs, list) else 0
        tail = f" — {preview}" if preview else ""
        lines.append(f"  {idx}. {label} ({count} msg){tail}")
    lines.append("  phone texts <n>  -- read a thread")
    lines.append("  phone texts clear  -- wipe all threads on this handset")
    return "\r\n".join(lines)


def format_sms_thread_detail(character, game, index):
    """Full thread for ``phone texts <n>`` (1-based index)."""
    phone = primary_handset(character)
    if phone is None:
        return "You are not carrying a voice handset."
    try:
        n = int(index)
    except (TypeError, ValueError):
        return "Usage: phone texts <number>"
    threads = _ensure_sms_threads(phone)
    if n < 1 or n > len(threads):
        return f"No text thread #{n}. Try phone texts."
    row = threads[n - 1]
    if not isinstance(row, dict):
        return f"No text thread #{n}."
    peer = row.get("peer") or "?"
    label = peer_display_label(character, peer, game)
    lines = [f"{tag_phone(character)} Thread: {label}"]
    msgs = row.get("messages") or []
    if not msgs:
        lines.append("  (empty)")
    else:
        for msg in msgs[-THREAD_MSG_CAP:]:
            if not isinstance(msg, dict):
                continue
            when = _format_thread_time(game, msg.get("at"))
            who = (msg.get("from") or "?").strip()
            body = (msg.get("body") or "").strip()
            lines.append(f"  [{when}] {who}: {body}")
    return "\r\n".join(lines)


def format_voicemail_list(character, game):
    """Numbered tape for ``phone voicemail`` / ``phone vm``."""
    phone = primary_handset(character)
    if phone is None:
        return "You are not carrying a voice handset."
    tape = _ensure_voicemail(phone)
    if not tape:
        return (
            f"{tag_phone(character)} Voicemail empty. "
            "Missed calls on your handset leave a tape here."
        )
    lines = [f"{tag_phone(character)} Voicemail:"]
    for idx, row in enumerate(tape[:VOICEMAIL_CAP], start=1):
        if not isinstance(row, dict):
            continue
        when = _format_thread_time(game, row.get("at"))
        who = (row.get("from") or "?").strip()
        body = (row.get("body") or "").strip()
        if len(body) > 52:
            body = body[:49] + "..."
        lines.append(f"  {idx}. [{when}] {who} — {body}")
    lines.append(
        "  phone voicemail <n>  -- listen | "
        "phone voicemail delete <n>"
    )
    return "\r\n".join(lines)


def play_voicemail(character, game, index):
    """Play one voicemail row. Returns player message."""
    phone = primary_handset(character)
    if phone is None:
        return "You are not carrying a voice handset."
    try:
        n = int(index)
    except (TypeError, ValueError):
        return "Usage: phone voicemail <number>"
    tape = _ensure_voicemail(phone)
    if n < 1 or n > len(tape):
        return f"No voicemail #{n}. Try phone voicemail."
    row = tape[n - 1]
    if not isinstance(row, dict):
        return f"No voicemail #{n}."
    when = _format_thread_time(game, row.get("at"))
    who = (row.get("from") or "?").strip()
    body = (row.get("body") or "").strip()
    return (
        f"{tag_phone(character)} Voicemail #{n} [{when}] "
        f"{who}: {body}"
    )


def delete_voicemail(character, index):
    """Delete one voicemail row. Returns (ok, message)."""
    phone = primary_handset(character)
    if phone is None:
        return False, "You are not carrying a voice handset."
    try:
        n = int(index)
    except (TypeError, ValueError):
        return False, "Usage: phone voicemail delete <number>"
    tape = _ensure_voicemail(phone)
    if n < 1 or n > len(tape):
        return False, f"No voicemail #{n}."
    del tape[n - 1]
    return True, f"{tag_phone(character)} Deleted voicemail #{n}."


def clear_sms_threads(character):
    """Wipe every SMS thread on the primary handset. Returns (ok, message)."""
    phone = primary_handset(character)
    if phone is None:
        return False, "You are not carrying a voice handset."
    threads = _ensure_sms_threads(phone)
    if not threads:
        return True, (
            f"{tag_phone(character)} No text threads to clear."
        )
    count = len(threads)
    threads.clear()
    return True, (
        f"{tag_phone(character)} Cleared {count} text thread"
        f"{'' if count == 1 else 's'} from your handset."
    )


def handset_has_sms_threads(item):
    """True when a portable handset Item already holds one or more threads."""
    if item is None or not is_portable_phone(item):
        return False
    return bool(_ensure_sms_threads(item))


def pickup_threads_nudge(character, item):
    """Short inbox hint after get/unstow when threads live on the handset."""
    if not handset_has_sms_threads(item):
        return None
    return (
        f"{tag_phone(character)} Texts waiting on this handset — "
        "phone texts."
    )


def _garment_holding_handset(character, handset):
    """Return worn clothing when ``handset`` is tucked in that garment's pocket."""
    if character is None or handset is None:
        return None
    from engine.systems import clothing_pockets as pocket_mod
    from engine.systems.wearables import iter_worn_clothing

    for _slot, garment in iter_worn_clothing(character):
        if handset in pocket_mod.pocket_contents(garment):
            return garment
    return None


def _pocket_text_buzz_room_line(character, garment):
    """Third-person line when an inbound SMS buzzes a pocketed handset."""
    face = getattr(character, "key", "Someone")
    try:
        from command_support import _display_name

        face = _display_name(character, None) or face
    except Exception:
        pass
    gname = getattr(garment, "key", None) or "their clothes"
    return f"{face}'s phone buzzes in {gname}."


def _append_pending_text(character, line):
    """Queue a formatted one-way SMS line for login / ``phone`` flush."""
    pending = getattr(character, "pending_phone_texts", None)
    if not isinstance(pending, list):
        character.pending_phone_texts = []
        pending = character.pending_phone_texts
    pending.append(line)


def _queue_plane_sms(holder, sender, notify_line):
    """Hold one inbound SMS buzz until sender and holder share a plane."""
    if holder is None or sender is None:
        return
    pending = getattr(holder, "pending_plane_sms", None)
    if not isinstance(pending, list):
        holder.pending_plane_sms = []
        pending = holder.pending_plane_sms
    sender_key = (getattr(sender, "key", None) or "").strip()
    if not sender_key:
        return
    row = {
        "sender_key": sender_key,
        "line": (notify_line or "").strip(),
    }
    if row["line"]:
        pending.append(row)


def _deliver_plane_sms_row(holder, row, game):
    """Deliver one queued plane SMS when the sender is reachable."""
    if holder is None or not row:
        return False
    sender_key = (row.get("sender_key") or "").strip()
    line = (row.get("line") or "").strip()
    if not sender_key or not line:
        return True
    finder = getattr(game, "find_character", None)
    sender = finder(sender_key) if callable(finder) else None
    if sender is None or not same_plane(holder, sender):
        return False
    _deliver_text_line(holder, line)
    return True


def flush_cross_plane_phone_texts(character, game):
    """Deliver plane-queued SMS after login, move, or ``phone``."""
    if character is None or game is None:
        return
    pending = getattr(character, "pending_plane_sms", None)
    if isinstance(pending, list) and pending:
        still = []
        for row in pending:
            if not _deliver_plane_sms_row(character, row, game):
                still.append(row)
        character.pending_plane_sms = still
    my_key = (getattr(character, "key", None) or "").strip()
    if not my_key:
        return
    for other in list(getattr(game, "characters", ()) or ()):
        if other is character:
            continue
        opending = getattr(other, "pending_plane_sms", None)
        if not isinstance(opending, list) or not opending:
            continue
        still_other = []
        for row in opending:
            if (row.get("sender_key") or "").strip() != my_key:
                still_other.append(row)
                continue
            if not _deliver_plane_sms_row(other, row, game):
                still_other.append(row)
        other.pending_plane_sms = still_other


# Handset cosmetic pings (mirror supers/phone_apps allowlists; engine reads Item fields).
_TEXT_TONE_LINES = {
    "default": "Your phone buzzes — new text.",
    "ping": "Ping — new text.",
    "silent": None,
}
_RINGTONE_LINES = {
    "classic": "Ring ring.",
    "chime": "Chime.",
    "vibrate": "Buzz buzz.",
}


def _handset_cosmetic(handset, field, default):
    """Read one cosmetic field from a handset Item with a safe default."""
    if handset is None:
        return default
    raw = getattr(handset, field, None)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    return text or default


def incoming_text_tone_line(character):
    """Short ping before an SMS body; None when text tone is silent."""
    if not has_portable_phone(character):
        return None
    handset = primary_handset(character)
    tone = _handset_cosmetic(handset, "text_tone", "default")
    body = _TEXT_TONE_LINES.get(tone, _TEXT_TONE_LINES["default"])
    if not body:
        return None
    return f"{tag_phone(character)} {body}"


def incoming_ringtone_line(character):
    """Short ring tell before incoming-call UI."""
    if not has_portable_phone(character):
        return None
    handset = primary_handset(character)
    ring = _handset_cosmetic(handset, "ringtone", "classic")
    body = _RINGTONE_LINES.get(ring, _RINGTONE_LINES["classic"])
    if not body:
        return None
    return f"{tag_phone(character)} {body}"


def _deliver_text_line(character, line):
    """Deliver one SMS line now, or queue when offline / asleep.

    Sleep is world-closed (bug report 1550): a sleeping character does not
    read text content, but a text still stirs them toward waking rather
    than vanishing unnoticed. It flushes in full on ``wake``.
    """
    session = getattr(character, "session", None)
    if session is not None and not getattr(character, "asleep", False):
        from engine import snoop as snoop_module

        # Pocketed primary handsets buzz audibly in the room (bug 1069 path)
        # while the owner still gets the private tone + SMS body.
        handset = primary_handset(character)
        garment = _garment_holding_handset(character, handset)
        if garment is not None:
            room = getattr(character, "location", None)
            if room is not None:
                room.broadcast(
                    _pocket_text_buzz_room_line(character, garment),
                    exclude=character,
                )
        tone = incoming_text_tone_line(character)
        if tone:
            snoop_module.tell_paragraph(character, tone)
        snoop_module.tell_paragraph(character, line)
        return True
    _append_pending_text(character, line)
    if session is not None and getattr(character, "asleep", False):
        from engine import snoop as snoop_module

        snoop_module.tell_paragraph(
            character,
            "Something stirs at the edge of your dreams, tugging you "
            "toward waking.",
        )
    return True


def deliver_official_text(character, sender_label, body, game=None):
    """One-way official SMS. Queues for offline handset holders."""
    if character is None or not (body or "").strip():
        return False
    if not has_portable_phone(character):
        return False
    sender = (sender_label or "Sheriff's Office").strip() or "Sheriff's Office"
    text = (body or "").strip()
    phone = primary_handset(character)
    if phone is not None:
        append_sms_to_handset(
            phone,
            official_thread_key(sender),
            from_label=sender,
            body=text,
            game=game,
        )
    msg = f"{tag_phone(character)} {sender}: {text}"
    return _deliver_text_line(character, msg)


def send_player_text(sender, target_raw, body, game):
    """Queue or deliver a player SMS to a handset holder.

    Addressing uses phonebook alias or number (not character name).
    True-offline recipients (``session is None``) queue until login;
    live and idlemode sessions get immediate delivery. Cadence never
    auto-replies to inbound texts.
    """
    def _done(ok, msg):
        note_phone_attempt(sender, kind="sms", ok=ok, detail=msg)
        return ok, msg

    if not has_portable_phone(sender):
        return _done(False, "You need a phone on you to text.")
    label = (target_raw or "").strip()
    text = (body or "").strip()
    if not label or not text:
        return _done(False, "Usage: phone text <alias|number> <message>")
    if len(text) > SMS_TEXT_MAX:
        return _done(False, f"Texts are limited to {SMS_TEXT_MAX} characters.")
    if is_special_dial(label):
        return _done(False, "That line does not take texts.")
    number, err = resolve_dial_target(sender, label, game)
    if err:
        return _done(False, err)
    if not number:
        return _done(False, "That does not look like a phone number or saved alias.")
    item, holder, _room = find_item_by_number(game, number)
    if item is None or holder is None:
        stashed_holder = _holder_with_phone_in_unworn_bag(game, number)
        if stashed_holder is not None:
            return _done(
                False,
                f"{tag_phone(sender)} That number is stashed in an unworn bag "
                "-- they cannot receive texts until they wear the bag or "
                "carry the handset.",
            )
        return _done(False, f"{tag_phone(sender)} No handset at that number.")
    if is_payphone_item(item):
        return _done(False, "Payphones are not SMS inboxes.")
    if holder is sender:
        return _done(False, "You cannot text your own handset.")
    if not has_portable_phone(holder):
        return _done(
            False,
            f"{tag_phone(sender)} They are not carrying a phone.",
        )
    my_phone = primary_handset(sender)
    from_number = (
        ensure_phone_number(my_phone, game) if my_phone is not None else "?"
    )
    sender_name = (getattr(sender, "key", None) or "someone").strip()
    text = (body or "").strip()
    # Threads live on the handset Items (steal the phone, steal the inbox).
    append_sms_to_handset(
        item,
        from_number,
        from_label=f"{sender_name} ({from_number})",
        body=text,
        game=game,
    )
    if my_phone is not None:
        append_sms_to_handset(
            my_phone,
            number,
            from_label="You",
            body=text,
            game=game,
        )
    msg = (
        f"{tag_phone(holder)} SMS from {sender_name} ({from_number}): {text}"
    )
    on_shared_plane = same_plane(sender, holder)
    if on_shared_plane:
        _deliver_text_line(holder, msg)
    else:
        _queue_plane_sms(holder, sender, msg)
    session = getattr(holder, "session", None)
    if not on_shared_plane:
        return _done(
            True,
            f"You text {number}. It will deliver when you share a plane.",
        )
    if session is None:
        return _done(True, f"You text {number}. It will arrive when they log in.")
    return _done(True, f"You text {number}.")


def flush_pending_phone_texts(character, game=None):
    """Deliver queued one-way texts after login or on ``phone``."""
    if game is not None:
        flush_cross_plane_phone_texts(character, game)
    pending = getattr(character, "pending_phone_texts", None)
    if not pending:
        return
    session = getattr(character, "session", None)
    if session is None:
        return
    from engine import snoop as snoop_module
    for line in list(pending):
        tone = incoming_text_tone_line(character)
        if tone:
            snoop_module.tell_paragraph(character, tone)
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
    """True when a world Item is a voice phone (portable, payphone, landline)."""
    if item is None:
        return False
    if bool(getattr(item, "is_pager", False)):
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


def is_landline_item(item):
    """True when the Item is a furniture copper landline."""
    if item is None:
        return False
    cat = getattr(item, "catalog_id", None)
    if cat in _landline_catalog_ids:
        return True
    return bool(getattr(item, "is_landline", False))


def is_portable_phone(item):
    """True when the Item is a carried voice handset (flip or smartphone)."""
    if item is None:
        return False
    if bool(getattr(item, "is_pager", False)):
        return False
    cat = getattr(item, "catalog_id", None)
    if cat in _portable_catalog_ids:
        return True
    if is_payphone_item(item) or is_landline_item(item):
        return False
    if bool(getattr(item, "furniture", False)):
        return False
    return bool(getattr(item, "is_phone", False))


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


META_KEY = "phone_number_pool"


def ensure_phone_pool(game):
    """``game.retired_phone_numbers`` + ``game.claimed_phone_numbers``.

    Retired rows wait ``free_after_ticks`` (one game-day at stock 1x) before
    ``next_assignable_number`` reuses them. Claimed numbers stay out of the
    recycle pool even after that deadline.
    """
    if game is None:
        return
    blob = getattr(game, "retired_phone_numbers", None)
    if not isinstance(blob, list):
        game.retired_phone_numbers = []
    claimed = getattr(game, "claimed_phone_numbers", None)
    if not isinstance(claimed, set):
        if isinstance(claimed, (list, tuple)):
            game.claimed_phone_numbers = set(
                str(x).strip() for x in claimed if str(x).strip()
            )
        else:
            game.claimed_phone_numbers = set()


def export_meta(game):
    """Persist blob for ``save_meta_json`` (retired pool + claimed numbers)."""
    ensure_phone_pool(game)
    retired = []
    for row in list(getattr(game, "retired_phone_numbers", None) or []):
        if not isinstance(row, dict):
            continue
        num = normalize_number(row.get("number"))
        if not num:
            continue
        try:
            free_at = int(row.get("free_after_ticks", 0) or 0)
        except (TypeError, ValueError):
            free_at = 0
        retired.append({"number": num, "free_after_ticks": free_at})
    claimed = sorted(
        normalize_number(x) or str(x).strip()
        for x in (getattr(game, "claimed_phone_numbers", None) or set())
        if x
    )
    return {"retired": retired, "claimed": [c for c in claimed if c]}


def import_meta(game, blob):
    """Restore retired/claimed phone numbers from meta JSON."""
    ensure_phone_pool(game)
    if not isinstance(blob, dict):
        return
    retired = []
    for row in blob.get("retired") or []:
        if not isinstance(row, dict):
            continue
        num = normalize_number(row.get("number"))
        if not num:
            continue
        try:
            free_at = int(row.get("free_after_ticks", 0) or 0)
        except (TypeError, ValueError):
            free_at = 0
        retired.append({"number": num, "free_after_ticks": free_at})
    game.retired_phone_numbers = retired
    claimed = set()
    for raw in blob.get("claimed") or []:
        num = normalize_number(raw)
        if num:
            claimed.add(num)
    game.claimed_phone_numbers = claimed


def _retired_row(game, number):
    """Return the retired-pool dict for ``number``, or None."""
    ensure_phone_pool(game)
    want = normalize_number(number)
    if not want:
        return None
    for row in list(getattr(game, "retired_phone_numbers", None) or []):
        if not isinstance(row, dict):
            continue
        if normalize_number(row.get("number")) == want:
            return row
    return None


def retire_number(game, number, *, free_after_ticks: int):
    """Disable ``number`` until ``free_after_ticks``, then it may recycle.

    A live handset may still carry the stamp (corpse loot); ``number_is_live``
    treats a retired number as disconnected until it is reclaimed or a
    player ``claim_number``s it. Essential / immersion faces should not
    call this -- they keep the number across Chuck remake (Slice 6).
    """
    if game is None:
        return
    want = normalize_number(number)
    if not want:
        return
    ensure_phone_pool(game)
    try:
        free_at = int(free_after_ticks)
    except (TypeError, ValueError):
        free_at = 0
    # Refresh the deadline if this number is retired twice (re-death).
    kept = [
        row for row in game.retired_phone_numbers
        if isinstance(row, dict)
        and normalize_number(row.get("number")) != want
    ]
    kept.append({"number": want, "free_after_ticks": free_at})
    game.retired_phone_numbers = kept


def number_is_live(game, number) -> bool:
    """True when ``number`` is assigned and has not been retired.

    Retired-and-not-yet-reclaimed numbers return False so dialers hear
    disconnected prose even if the corpse still holds the handset.
    """
    want = normalize_number(number)
    if not want or game is None:
        return False
    if _retired_row(game, want) is not None:
        return False
    item, _holder, _room = find_item_by_number(game, want)
    if item is not None:
        return True
    claimed = getattr(game, "claimed_phone_numbers", None) or set()
    return want in claimed


def next_assignable_number(game) -> str:
    """Prefer a retired-and-due number from the pool, else ``_next_number``."""
    if game is None:
        return _next_number(game)
    ensure_phone_pool(game)
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    claimed = getattr(game, "claimed_phone_numbers", None) or set()
    still = []
    reuse = None
    for row in list(game.retired_phone_numbers):
        if not isinstance(row, dict):
            continue
        num = normalize_number(row.get("number"))
        if not num:
            continue
        try:
            free_at = int(row.get("free_after_ticks", 0) or 0)
        except (TypeError, ValueError):
            free_at = 0
        # Claimed numbers never return to the pool.
        if num in claimed:
            continue
        if free_at > now:
            still.append({"number": num, "free_after_ticks": free_at})
            continue
        if reuse is None:
            reuse = num
        else:
            still.append({"number": num, "free_after_ticks": free_at})
    game.retired_phone_numbers = still
    if reuse:
        return reuse
    return _next_number(game)


def claim_number(character, number, game) -> tuple[bool, str]:
    """Lock ``number`` onto the caller's flip phone and out of the recycle pool.

    Nested hub: ``phone claim <number>``. Fails if the number is still live
    on someone else's handset (not retired).
    """
    want = normalize_number(number)
    if not want:
        return False, "That does not look like a phone number."
    if is_special_dial(want):
        return False, "Special lines cannot be claimed."
    phone = primary_handset(character)
    if phone is None:
        return False, "You need a flip phone on you to claim a number."
    if game is None:
        return False, "No exchange is open to claim that number."
    ensure_phone_pool(game)
    # Live = assigned and not retired. A retired corpse number is claimable.
    if number_is_live(game, want):
        existing = normalize_number(getattr(phone, "phone_number", None))
        if existing != want:
            return False, "That number is already in service."
        game.claimed_phone_numbers.add(want)
        return True, f"Your handset is already {want} -- claimed, it will not recycle."
    # Drop from the retired pool so next_assignable cannot reuse it.
    game.retired_phone_numbers = [
        row for row in game.retired_phone_numbers
        if isinstance(row, dict)
        and normalize_number(row.get("number")) != want
    ]
    game.claimed_phone_numbers.add(want)
    phone.phone_number = want
    return True, f"Your handset is now {want}. That number will not recycle."


def _handset_label(item):
    """Player-facing label for a portable phone Item."""
    return (getattr(item, "key", None) or "phone").strip() or "phone"


def _match_handset_hint(phones, hint):
    """Pick one portable phone whose key contains ``hint`` (case-insensitive)."""
    needle = (hint or "").strip().lower()
    if not needle:
        return None
    matches = [
        p for p in phones
        if needle in _handset_label(p).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        keys = ", ".join(_handset_label(p) for p in matches[:4])
        return ("ambiguous", keys)
    return None


def _retire_replaced_number(game, number):
    """Return a freshly assigned number to the recycle pool after one game-day."""
    want = normalize_number(number)
    if not want or is_special_dial(want):
        return
    ensure_phone_pool(game)
    claimed = getattr(game, "claimed_phone_numbers", None) or set()
    if want in claimed:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    try:
        from engine.game_clock_tuning import TICKS_PER_GAME_DAY
        free_at = now + int(TICKS_PER_GAME_DAY)
    except Exception:
        free_at = now + 9600
    retire_number(game, want, free_after_ticks=free_at)


def transfer_number(character, game, raw_args="") -> tuple[bool, str]:
    """Move a number between two portable phones the caller carries.

    Nested hub forms:
      phone transfer
      phone transfer to <handset>
      phone transfer <number>
      phone transfer <number> to <handset>
    """
    if game is None:
        return False, "No exchange is open to transfer that number."
    if active_call(character):
        return False, "Hang up before you swap numbers between handsets."
    phones = portable_phones_held(character)
    if len(phones) < 2:
        return (
            False,
            "You need two handsets on you to transfer a number "
            "(buy a new flip or smartphone, then phone transfer).",
        )
    bits = (raw_args or "").strip().split()
    number_raw = None
    dest_hint = None
    if not bits:
        pass
    elif bits[0].lower() == "to":
        dest_hint = " ".join(bits[1:]).strip() or None
    elif len(bits) >= 3 and bits[-2].lower() == "to":
        number_raw = " ".join(bits[:-2]).strip() or None
        dest_hint = bits[-1]
    elif len(bits) == 1:
        if normalize_number(bits[0]):
            number_raw = bits[0]
        else:
            dest_hint = bits[0]
    else:
        joined = " ".join(bits)
        if " to " in joined.lower():
            left, right = joined.rsplit(" to ", 1)
            number_raw = left.strip() or None
            dest_hint = right.strip() or None
        elif normalize_number(bits[0]):
            number_raw = bits[0]
            dest_hint = " ".join(bits[1:]).strip() or None
        else:
            dest_hint = joined

    source = None
    want = normalize_number(number_raw) if number_raw else None
    if want:
        if is_special_dial(want):
            return False, "Special lines cannot be moved between handsets."
        for piece in phones:
            got = normalize_number(getattr(piece, "phone_number", None))
            if got == want:
                source = piece
                break
        if source is None:
            return False, "You are not carrying a handset with that number."
    else:
        source = primary_handset(character)
        want = normalize_number(getattr(source, "phone_number", None))
        if not want:
            return False, "Your primary handset has no number to move."

    dest_candidates = [p for p in phones if p is not source]
    if not dest_candidates:
        return False, "You need a second handset to receive that number."

    dest = None
    if dest_hint:
        picked = _match_handset_hint(dest_candidates, dest_hint)
        if picked is None:
            return (
                False,
                f"No other handset matches '{dest_hint}'. "
                "Try 'phone number' to see what you carry.",
            )
        if isinstance(picked, tuple) and picked[0] == "ambiguous":
            return (
                False,
                f"Which handset? Several match '{dest_hint}': {picked[1]}.",
            )
        dest = picked
    elif len(dest_candidates) == 1:
        dest = dest_candidates[0]
    else:
        keys = ", ".join(_handset_label(p) for p in dest_candidates[:4])
        return (
            False,
            "Which handset gets the number? "
            f"Try 'phone transfer to <handset>' ({keys}).",
        )

    if dest is source:
        return False, "Pick a different handset to receive the number."

    dest_old = normalize_number(getattr(dest, "phone_number", None))
    if dest_old == want:
        return True, f"{_handset_label(dest)} already has {want}."

    ensure_phone_pool(game)
    # Stamp a fresh line on the old handset so it stops ringing at ``want``.
    source.phone_number = None
    fresh = next_assignable_number(game)
    source.phone_number = fresh
    if dest_old and dest_old != want:
        _retire_replaced_number(game, dest_old)
    dest.phone_number = want
    game.claimed_phone_numbers.add(want)
    game.retired_phone_numbers = [
        row for row in game.retired_phone_numbers
        if isinstance(row, dict)
        and normalize_number(row.get("number")) != want
    ]
    src_label = _handset_label(source)
    dst_label = _handset_label(dest)
    return (
        True,
        f"{want} now rings {_handset_label(dest)} "
        f"({dst_label}). {_handset_label(source)} ({src_label}) "
        f"picked up {fresh}.",
    )


def ensure_phone_number(item, game=None):
    """Stamp a unique ``phone_number`` on a phone Item if missing."""
    if not is_phone_item(item):
        return None
    existing = getattr(item, "phone_number", None)
    if isinstance(existing, str) and existing.strip():
        return normalize_number(existing) or existing.strip().upper()
    if game is None:
        return None
    number = next_assignable_number(game)
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
    """List portable phone Items anywhere on the body (not furniture).

    Open inventory, worn kit-bag rows, clothing pockets, and the pocket
    wallet all count. Boot identity heal uses ``has_portable_phone`` to
    skip re-issuing a starter flip -- a phone stashed only in a worn bag
    or jeans pocket must still be seen (bug report 1069).
    """
    from engine.systems import containers as containers_mod
    from engine.systems import wallet_container as wallet_mod
    from engine.systems import clothing_pockets as pocket_mod
    from engine.systems.wearables import iter_worn_clothing

    seen: set[int] = set()
    out = []

    def _add(piece):
        if piece is None:
            return
        token = id(piece)
        if token in seen:
            return
        if not is_portable_phone(piece):
            return
        seen.add(token)
        out.append(piece)

    for piece in containers_mod.iter_carried_items(character):
        _add(piece)
    for _slot, garment in iter_worn_clothing(character):
        for piece in pocket_mod.pocket_contents(garment):
            _add(piece)
    wallet_item = wallet_mod.designated_wallet(character)
    if wallet_item is not None:
        for piece in wallet_mod.wallet_contents(wallet_item):
            _add(piece)
    return out


def has_portable_phone(character):
    """True when the character carries at least one voice handset."""
    return bool(portable_phones_held(character))


def _phone_in_unworn_bags(character, number):
    """True when ``number`` is on a handset inside a carried but unworn bag."""
    want = normalize_number(number)
    if not want or character is None:
        return False
    from engine.systems import containers as containers_mod

    worn_ids = {id(bag) for bag in containers_mod.worn_bags(character)}
    for piece in list(getattr(character, "inventory", None) or []):
        if id(piece) in worn_ids:
            continue
        inner_rows = containers_mod.bag_contents(piece)
        if not inner_rows and getattr(piece, "bag_contents", None) is None:
            continue
        for inner in inner_rows:
            if not is_portable_phone(inner):
                continue
            got = normalize_number(getattr(inner, "phone_number", None))
            if got == want:
                return True
    return False


def _holder_with_phone_in_unworn_bag(game, number):
    """Character holding ``number`` only inside an unworn kit bag, if any."""
    if game is None:
        return None
    for ch in iter_characters(game):
        if _phone_in_unworn_bags(ch, number):
            return ch
    return None


def _ensure_handset_uid(item):
    """Allocate a stable uid on a handset when missing (old saves / loot)."""
    uid = getattr(item, "uid", None)
    if isinstance(uid, str) and uid.strip():
        return uid.strip()
    import uuid

    item.uid = uuid.uuid4().hex
    return item.uid


def note_phone_attempt(character, *, kind, ok, detail=None):
    """Stamp the last SMS / ringtone / dial attempt for bug tickets.

    Session-only -- not persisted. Compact so a failed ``phone text`` or
    a ringtone that never played is still on the next ``bug`` snapshot.
    """
    if character is None:
        return
    row = {"kind": str(kind or ""), "ok": bool(ok)}
    if detail:
        row["detail"] = str(detail)[:80]
    character._last_phone_attempt = row


def _pick_default_primary_handset(phones):
    """Prefer a smartphone over a starter flip when order would lie.

    ``portable_phones_held`` lists open inventory before clothing pockets,
    so a pocketed smartphone can sit after a loose flip -- outbound SMS and
    ``phone number`` must not silently switch to the flip (bug reports 1306/1307).
    """
    if not phones:
        return None
    for piece in phones:
        if getattr(piece, "is_smartphone", False):
            return piece
    return phones[0]


def primary_handset(character):
    """Preferred portable for speak/text/apps; ring-all uses every number."""
    phones = portable_phones_held(character)
    if not phones:
        return None
    want_uid = getattr(character, "phone_primary_uid", None)
    if isinstance(want_uid, str) and want_uid.strip():
        for piece in phones:
            if getattr(piece, "uid", None) == want_uid.strip():
                return piece
    primary = _pick_default_primary_handset(phones)
    character.phone_primary_uid = _ensure_handset_uid(primary)
    return primary


def set_primary_handset(character, item, game=None):
    """Pick which portable speaks/texts. Returns (ok, message)."""
    _ = game
    if item is None:
        return False, "Which handset?"
    if not is_portable_phone(item):
        return False, "That is not a portable handset."
    held = portable_phones_held(character)
    if item not in held:
        return False, "You are not carrying that handset."
    uid = _ensure_handset_uid(item)
    character.phone_primary_uid = uid
    num = ensure_phone_number(item, game) if game is not None else (
        getattr(item, "phone_number", None) or "?"
    )
    nick = getattr(item, "nickname", None)
    label = (nick or getattr(item, "key", "handset")).strip()
    return True, f"Primary handset: {label} ({num})."


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
    if room_has_landline(room):
        return True, "landline", None
    return (
        False,
        None,
        "You need a phone on you, a house/motel landline, or a payphone here.",
    )


def charge_payphone(character):
    """Deduct payphone fee. Returns True on success."""
    coins = economy_wallet.wallet_dollars(character)
    if coins < PAYPHONE_FEE:
        return False
    economy_wallet.set_wallet(character, coins - PAYPHONE_FEE, 0)
    return True


def _is_character(obj):
    """True for world Character actors (not furniture Items)."""
    if obj is None:
        return False
    from engine.world import Character

    return isinstance(obj, Character)


def _actors_in_room(room):
    """Characters standing in ``room`` (not items)."""
    out = []
    if room is None:
        return out
    for obj in list(getattr(room, "contents", None) or []):
        if _is_character(obj):
            out.append(obj)
    return out


def room_has_landline(room):
    """True when copper house/motel furniture is in the room."""
    if room is None:
        return False
    for obj in list(getattr(room, "contents", None) or []):
        if is_landline_item(obj):
            return True
    return False


def landline_in_room(room):
    """First landline furniture item in ``room``, or None."""
    if room is None:
        return None
    for obj in list(getattr(room, "contents", None) or []):
        if is_landline_item(obj):
            return obj
    return None


def resolve_landline_callee(item, room, game, exclude=None):
    """Pick who answers a furniture copper line (no character holder).

    Motel phones: occupants of that guest room only.
    House landlines: living-room occupants plus the homestead owner if they
    are still in that house (same ``homestead_owner`` stamp).
    """
    if not is_landline_item(item) or room is None or game is None:
        return None
    cat = getattr(item, "catalog_id", None)
    pool = list(_actors_in_room(room))
    if cat != "motel_phone":
        owner_key = getattr(room, "homestead_owner", None)
        if owner_key:
            finder = getattr(game, "find_character", None)
            owner = finder(owner_key) if callable(finder) else None
            loc = getattr(owner, "location", None) if owner else None
            if owner is not None and loc is not None:
                same_room = loc is room
                same_house = getattr(loc, "homestead_owner", None) == owner_key
                if same_room or same_house:
                    if owner not in pool:
                        pool.insert(0, owner)
    if exclude is not None:
        pool = [ch for ch in pool if ch is not exclude]
    for ch in pool:
        if getattr(ch, "session", None) is not None:
            return ch
    return pool[0] if pool else None


def bind_voice_callee(item, holder, room, game, exclude=None):
    """Holder for a dialed number, including landline furniture."""
    if holder is not None:
        return holder
    return resolve_landline_callee(item, room, game, exclude=exclude)


def find_item_by_number(game, number):
    """Find a phone Item (and optional holder Character) by number."""
    want = normalize_number(number)
    if not want or is_special_dial(want):
        return None, None, None
    rooms = getattr(game, "rooms", None) or {}
    for ch in iter_characters(game):
        # Same body scope as ``portable_phones_held`` (inventory, bags,
        # clothing pockets, wallet) so pocketed handsets still ring (1069/1306).
        for piece in portable_phones_held(ch):
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


def _phonebook_fuzzy_match(alias_key, contacts):
    """Resolve a typed label to one saved alias (prefix or close spelling).

    ``phone save alie 555-1575`` then ``phone text alice …`` should still
    hit the saved row when the name is a unique near-match.
    """
    if not alias_key or not contacts:
        return None
    hits = []
    for saved_alias, number in contacts.items():
        if alias_key == saved_alias:
            return (saved_alias, number)
        if len(saved_alias) >= 3 and len(alias_key) >= 3:
            if alias_key.startswith(saved_alias) or saved_alias.startswith(alias_key):
                hits.append((saved_alias, number))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = ", ".join(sorted(a for a, _n in hits))
        return ("__ambiguous__", names)
    close = difflib.get_close_matches(
        alias_key, list(contacts.keys()), n=2, cutoff=0.84,
    )
    if len(close) == 1:
        saved_alias = close[0]
        return (saved_alias, contacts[saved_alias])
    if len(close) > 1:
        names = ", ".join(sorted(close))
        return ("__ambiguous__", names)
    return None


def resolve_dial_target(character, raw, game):
    """Resolve dial args to a number string."""
    text = (raw or "").strip()
    if not text:
        return None, "Dial which number? Try 'dial 555-0142' or 'call dean'."
    alias_key = text.split(None, 1)[0].lower()
    contacts = getattr(character, "phone_contacts", None) or {}
    if isinstance(contacts, dict) and alias_key in contacts:
        return normalize_number(contacts[alias_key]), None
    if isinstance(contacts, dict) and contacts:
        fuzzy_hit = _phonebook_fuzzy_match(alias_key, contacts)
        if fuzzy_hit is not None:
            saved_alias, number = fuzzy_hit
            if saved_alias == "__ambiguous__":
                return None, f"Which contact? Saved aliases match: {number}."
            return normalize_number(number), None
    head = text.split(None, 1)[0]
    try:
        from engine import hooks as hooks_mod

        hooked = hooks_mod.phone_dial_alias_resolver(head, character, game)
        if hooked:
            return normalize_number(hooked), None
    except Exception as exc:
        from engine import log_util

        log_util.ops(
            "phone",
            f"dial alias resolver failed head={head!r}",
            exc=exc,
        )
    if is_special_dial(head):
        return head.upper(), None
    num = normalize_number(head)
    if not num:
        return None, "That does not look like a phone number or saved alias."
    # Names like Alice normalize to letter-only pseudo-numbers — do not dial.
    if num.isalpha() and not is_special_dial(num):
        if contacts:
            return None, (
                f"No saved alias '{alias_key}'. "
                "Try 'phone contacts' or phone save <alias> <number>."
            )
        return None, "That does not look like a phone number or saved alias."
    return num, None


def contacts_dict(character):
    """Sanitized alias -> number map on the character."""
    raw = getattr(character, "phone_contacts", None)
    if not isinstance(raw, dict):
        character.phone_contacts = {}
        return character.phone_contacts
    return raw


def save_contact(character, alias, number, game=None):
    """Save phonebook alias -> number. Returns (ok, message)."""
    label = (alias or "").strip().lower()
    if not label or not re.match(r"^[a-z][a-z0-9_-]{0,23}$", label):
        return False, (
            "Alias must start with a letter "
            f"(up to {MAX_ALIAS_LEN} letters/digits/_-)."
        )
    if label in (
        "wknz", "save", "forget", "contacts", "number", "ask", "text",
        "claim", "transfer", "to",
    ):
        return False, "That alias is reserved."
    num = normalize_number(number)
    if not num or is_special_dial(num):
        return False, "Save a real phone number (not a special line)."
    if game is not None and not number_is_live(game, num):
        return False, "That number is not in service."
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


def _maybe_record_missed_call_voicemail(caller, callee, game):
    """When a ring is abandoned, leave a tape on the callee's portable."""
    if caller is None or callee is None:
        return
    callee_call = active_call(callee)
    if not callee_call or callee_call.get("state") != "ringing":
        return
    caller_call = active_call(caller)
    if not caller_call:
        return
    # Caller hung up while still ringing, or callee declined without answer.
    caller_number = (
        callee_call.get("peer_number")
        or caller_call.get("my_number")
        or "unknown"
    )
    callee_number = callee_call.get("my_number")
    if not callee_number:
        return
    item, holder, _room = find_item_by_number(game, callee_number)
    if item is None or holder is not callee:
        return
    if not is_portable_phone(item) or is_payphone_item(item):
        return
    caller_face = _peer_name(caller, caller_number)
    append_voicemail(
        item,
        from_label=caller_number,
        body=f"Missed call from {caller_face} ({caller_number}).",
        game=game,
    )


def hangup_pair(a, b, *, reason="hangup", game=None):
    """End a call between two characters (safe if one is None)."""
    if reason == "hangup" and game is not None:
        _maybe_record_missed_call_voicemail(a, b, game)
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
    ring_line = incoming_ringtone_line(callee)
    note_phone_attempt(
        callee, kind="ringtone", ok=bool(ring_line), detail=ring_line,
    )
    note_phone_attempt(caller, kind="dial", ok=True, detail=callee_number)
    if ring_line:
        _send(callee, ring_line)
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
    """Return the other Character on an active call, or None.

    1:1 calls require reciprocal ``peer_key``. Conference calls stamp a
    shared ``members`` list — any other live member counts as the peer so
    ``phone ask`` does not report a dead line on a three-way.
    """
    call = active_call(character)
    if not call:
        return None
    finder = getattr(game, "find_character", None)
    if not callable(finder):
        return None
    my_key = getattr(character, "key", None)
    members = call.get("members")
    if isinstance(members, list) and len(members) >= 2:
        for key in members:
            if not key or key == my_key:
                continue
            peer = finder(key)
            if peer is None:
                continue
            peer_call = active_call(peer)
            if not peer_call:
                continue
            peer_members = peer_call.get("members")
            if isinstance(peer_members, list) and my_key in peer_members:
                return peer
            if peer_call.get("peer_key") == my_key:
                return peer
        return None
    key = call.get("peer_key")
    if not key:
        return None
    peer = finder(key)
    if peer is None:
        return None
    peer_call = active_call(peer)
    if not peer_call or peer_call.get("peer_key") != my_key:
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
    elif mode == "landline":
        wall = landline_in_room(getattr(character, "location", None))
        if wall is not None:
            my_number = ensure_phone_number(wall, game)

    if is_special_dial(number) and _phone_switchboard is not None:
        msg = _phone_switchboard(character, game, number, rest, mode)
        if msg is not None:
            return msg

    # Retired numbers ring disconnected even if a corpse still holds the
    # handset. Missing numbers keep the existing "not in service" line.
    if not is_special_dial(number) and _retired_row(game, number) is not None:
        return (
            f"{tag_phone(character)} Disconnected — that number is "
            "out of service."
        )

    item, holder, furniture_room = find_item_by_number(game, number)
    if item is None:
        return f"{tag_phone(character)} No answer — number not in service."

    if is_payphone_item(item) and holder is None:
        return (
            f"{tag_phone(character)} That payphone does not take inbound "
            "calls — try a handset number."
        )

    holder = bind_voice_callee(
        item, holder, furniture_room, game, exclude=character,
    )
    if holder is None:
        if is_landline_item(item):
            return (
                f"{tag_phone(character)} The phone rings in an empty room — "
                "no one picks up."
            )
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
    if is_landline_item(item) and furniture_room is not None:
        furniture_room.broadcast(
            "The copper phone rings.",
            exclude=holder,
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
    hangup_pair(character, peer, reason="hangup", game=game)
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
    flush_pending_phone_texts(character, game)
    lines = [f"{tag_phone(character)} Phone"]
    phones = portable_phones_held(character)
    primary = primary_handset(character)
    primary_uid = getattr(primary, "uid", None) if primary else None
    if phones:
        for p in phones:
            num = ensure_phone_number(p, game) or "?"
            nick = getattr(p, "nickname", None)
            label = nick or getattr(p, "key", "phone")
            mark = " (primary)" if getattr(p, "uid", None) == primary_uid else ""
            kind = "smartphone" if getattr(p, "is_smartphone", False) else "flip"
            lines.append(f"  Handset: {label} [{kind}]{mark} — {num}")
    else:
        lines.append(
            "  Handset: (none — buy a flip or smartphone, or use a payphone)"
        )
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
        "phone primary <name|nick> | phone text <alias|number> <msg> | "
        "phone texts | phone voicemail | "
        "phone say <text> | phone ask group|food|water|help | "
        "phone request <song> | phone save|forget|contacts | "
        "phone claim <number> | phone transfer [to <handset>] | "
        "nickname <item> as <nick>"
    )
    return "\r\n".join(lines)
