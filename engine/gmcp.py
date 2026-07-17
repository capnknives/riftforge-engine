"""
gmcp.py -- Generic Mud Communication Protocol (telnet option 201).

GMCP carries JSON packages out-of-band so clients (Mudlet, etc.) can drive
gauges and UI without scraping prose. Wire framing lives in engine/telnet.py;
this module owns encode/decode, Core.Hello / Core.Supports, and the push
helpers that Session / verbs call.

Pure engine: no supers imports. SUPERS vitals/status payloads come in through
engine.hooks so two-repo purity stays intact.
"""

import json
import logging

from engine import telnet

log = logging.getLogger(__name__)

# Advertised in Core.Hello -- bump when the GMCP surface changes meaningfully.
GMCP_SERVER_NAME = "Riftforge"
GMCP_SERVER_VERSION = "1.0"

# Packages this server can emit (Mudlet Core.Supports.Set style: "Name Ver").
SERVER_SUPPORTS = (
    "Char 1",
    "Char.Name 1",
    "Char.Status 1",
    "Char.Vitals 1",
    "Room 1",
    "Room.Info 1",
    "Comm 1",
    "Comm.Channel 1",
)


def encode_package(package: str, payload) -> bytes:
    """Build IAC SB GMCP <package> <json> IAC SE as raw socket bytes.

    Mudlet convention: package name, a single space, then JSON. payload may
    be a dict, list, or other json-serializable value.
    """
    body = f"{package} {json.dumps(payload, separators=(',', ':'))}"
    escaped = telnet.escape_iac(body.encode("utf-8"))
    return (
        bytes((telnet.IAC, telnet.SB, telnet.TELOPT_GMCP))
        + escaped
        + bytes((telnet.IAC, telnet.SE))
    )


def decode_subneg(data: bytes):
    """Parse GMCP SB payload bytes into (package_name, json_value_or_None).

    Returns (None, None) on empty / malformed input -- callers ignore quietly.
    """
    if not data:
        return None, None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    text = text.strip()
    if not text:
        return None, None
    # First token is the package; remainder (after one space) is JSON.
    if " " in text:
        package, raw_json = text.split(" ", 1)
    else:
        package, raw_json = text, ""
    package = package.strip()
    if not package:
        return None, None
    if not raw_json.strip():
        return package, None
    try:
        return package, json.loads(raw_json)
    except json.JSONDecodeError:
        # Some clients send bare strings; keep the raw remainder.
        return package, raw_json.strip()


def client_supports(session, package: str) -> bool:
    """True when the client listed this package (or its parent module).

    After Core.Supports.Set, session.gmcp_supports maps name -> version int.
    Parent check: Char.Vitals is covered by an entry for Char alone.
    """
    if not getattr(session, "gmcp_enabled", False):
        return False
    supports = getattr(session, "gmcp_supports", None) or {}
    if not supports:
        return False
    if package in supports:
        return True
    parent = package.split(".", 1)[0]
    return parent in supports


def send_hello(session):
    """Outbound Core.Hello identifying this server."""
    session.send_gmcp(
        "Core.Hello",
        {"client": GMCP_SERVER_NAME, "version": GMCP_SERVER_VERSION},
        force=True,
    )


def send_supports(session):
    """Outbound Core.Supports.Set advertising packages we can send."""
    session.send_gmcp(
        "Core.Supports.Set",
        list(SERVER_SUPPORTS),
        force=True,
    )


def _parse_supports_list(payload) -> dict:
    """Turn Core.Supports payload into {name: version_int}.

    Accepts ["Char 1", "Room.Info 1"] or a single string.
    """
    out = {}
    if payload is None:
        return out
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    for item in items:
        if not isinstance(item, str):
            continue
        parts = item.split()
        if not parts:
            continue
        name = parts[0]
        ver = 1
        if len(parts) > 1:
            try:
                ver = int(parts[1])
            except ValueError:
                ver = 1
        out[name] = ver
    return out


def handle_inbound(session, package: str, payload):
    """Dispatch one inbound GMCP package from the client.

    Unknown packages are ignored (clients probe many IRE-style names).
    """
    if not package:
        return
    if package == "Core.Hello":
        # Optional client id/version -- log at debug for support, no gameplay.
        if isinstance(payload, dict):
            cid = payload.get("client") or payload.get("name")
            cver = payload.get("version")
            log.debug("GMCP Core.Hello from client=%s version=%s", cid, cver)
        return
    if package == "Core.Supports.Set":
        session.gmcp_supports = _parse_supports_list(payload)
        # Client just told us what it wants -- push identity packages if
        # a character is already attached (copyover / late Supports).
        if session.character is not None:
            push_char_identity(session.character)
            push_vitals(session.character)
            push_room(session.character)
        return
    if package == "Core.Supports.Add":
        session.gmcp_supports.update(_parse_supports_list(payload))
        return
    if package == "Core.Supports.Remove":
        for name in _parse_supports_list(payload):
            session.gmcp_supports.pop(name, None)
        return
    # Everything else: ignore.


def handle_telnet_event(session, event):
    """Apply one telnet parse event to the Session (negotiate or GMCP SB).

    Also routes MSSP (option 70) negotiate events to engine.mssp -- kept here
    so connection._ingest_bytes stays a single dispatch call site.
    """
    kind = event[0]
    if kind == telnet.EV_NEGOTIATE:
        _cmd, option = event[1], event[2]
        if option == telnet.TELOPT_MSSP:
            # Listing crawlers -- server-driven status (engine/mssp.py).
            from engine import mssp
            mssp.handle_negotiate(session, _cmd)
            return
        if option != telnet.TELOPT_GMCP:
            # Refuse options we do not speak (MCCP, MSDP, …) politely.
            if _cmd == telnet.WILL:
                session._write_raw(telnet.dont(option))
            elif _cmd == telnet.DO:
                session._write_raw(telnet.wont(option))
            return
        if _cmd == telnet.DO:
            # Client accepted our WILL GMCP.
            if not session.gmcp_enabled:
                session.gmcp_enabled = True
                send_hello(session)
                send_supports(session)
            return
        if _cmd == telnet.DONT:
            session.gmcp_enabled = False
            session.gmcp_supports = {}
            return
        if _cmd == telnet.WILL:
            # Client offers GMCP -- ask them to enable it.
            session._write_raw(telnet.do(telnet.TELOPT_GMCP))
            return
        if _cmd == telnet.WONT:
            session.gmcp_enabled = False
            session.gmcp_supports = {}
            return
        return

    if kind == telnet.EV_SUBNEG:
        option, data = event[1], event[2]
        if option == telnet.TELOPT_MSSP:
            # Clients do not send MSSP SB (server emits it). Ignore quietly.
            return
        if option != telnet.TELOPT_GMCP:
            return
        # Receiving a GMCP SB also implies the channel is live.
        session.gmcp_enabled = True
        package, payload = decode_subneg(data)
        handle_inbound(session, package, payload)


def offer_gmcp(session):
    """Send IAC WILL GMCP at connect / copyover resume."""
    session._write_raw(telnet.will(telnet.TELOPT_GMCP))


# --- Outbound package helpers ---------------------------------------------

def push_char_name(character):
    """Send Char.Name for an online character (no-op if unsupported)."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Char.Name"):
        return
    key = getattr(character, "key", "") or ""
    session.send_gmcp("Char.Name", {"name": key, "fullname": key})


def push_char_status(character):
    """Send Char.Status (engine base + optional SUPERS hook extras)."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Char.Status"):
        return
    from engine import hooks
    payload = {
        "name": getattr(character, "key", "") or "",
        "idle": "1" if getattr(character, "idle_mode", False) else "0",
        "gm": "1" if getattr(character, "gm_mode", False) else "0",
    }
    extras = hooks.gmcp_char_status(character)
    if isinstance(extras, dict):
        payload.update(extras)
    session.send_gmcp("Char.Status", payload)


def push_vitals(character):
    """Send Char.Vitals (SUPERS hook builds the dict; engine sends it)."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Char.Vitals"):
        return
    from engine import hooks
    payload = hooks.gmcp_char_vitals(character)
    if not isinstance(payload, dict):
        # Bare-engine fallback: HP only if present.
        hp = getattr(character, "hp", None)
        payload = {}
        if hp is not None:
            payload["hp"] = str(hp)
            payload["maxhp"] = str(getattr(character, "max_hp", hp))
    if payload:
        session.send_gmcp("Char.Vitals", payload)


def push_char_identity(character):
    """Name + Status together (login / Supports / rename)."""
    push_char_name(character)
    push_char_status(character)


def room_info_payload(character):
    """Build a Room.Info dict from the character's current location.

    Engine-safe fields only (no SUPERS). Returns None when there is no room.
    Dark rooms still get id/name/area; desc and exits are omitted until seen.
    """
    room = getattr(character, "location", None)
    if room is None:
        return None
    from engine import hooks
    from engine import vision as vision_mod

    can_see = vision_mod.can_see_room(character, room)
    game = None
    sess = getattr(character, "session", None)
    if sess is not None:
        game = getattr(sess, "game", None)

    exits = {}
    if can_see:
        for direction, dest in (getattr(room, "exits", None) or {}).items():
            if dest is None:
                continue
            if not hooks.look_exit_visible(dest, game):
                continue
            exits[direction] = getattr(dest, "key", str(dest))

    payload = {
        "num": getattr(room, "key", ""),
        "name": getattr(room, "key", ""),
        "area": getattr(room, "area_type", "plains") or "plains",
        "exits": exits,
    }
    if can_see:
        desc = getattr(room, "description", None) or getattr(room, "desc", None)
        if desc:
            payload["desc"] = desc
    return payload


def push_room(character):
    """Send Room.Info for the character's current room."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Room.Info"):
        return
    payload = room_info_payload(character)
    if payload:
        session.send_gmcp("Room.Info", payload)


def push_comm(session, chan: str, msg: str, player: str):
    """Send Comm.Channel to one session (parallel to prose, never instead)."""
    if session is None or not client_supports(session, "Comm.Channel"):
        return
    session.send_gmcp(
        "Comm.Channel",
        {"chan": chan, "msg": msg, "player": player},
    )


def on_session_attach(character, game=None):
    """Push identity + vitals after login/reconnect (Room comes from look)."""
    push_char_identity(character)
    push_vitals(character)
