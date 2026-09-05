"""
gmcp.py -- Generic Mud Communication Protocol (telnet option 201).

GMCP carries JSON packages out-of-band so clients (Mudlet, etc.) can drive
gauges and UI without scraping prose. Wire framing lives in engine/telnet.py;
this module owns encode/decode, Core.Hello / Core.Supports, and the push
helpers that Session / verbs call.

Pure engine: no supers imports. SUPERS vitals/status payloads come in through
engine.hooks so two-repo purity stays intact.
"""

import asyncio
import json
import logging
import os

from engine import telnet

log = logging.getLogger(__name__)

# Advertised in Core.Hello -- bump when the GMCP surface changes meaningfully.
GMCP_SERVER_NAME = "Riftforge"
GMCP_SERVER_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Mudlet auto-install / auto-update (GMCP Client.GUI).
#
# Mudlet natively downloads + installs the package at ``url`` and upgrades it
# whenever ``version`` changes (docs: Manual:GMCP_Extensions). We host the
# .mpackage on the live player site ``play.riftforge.me`` -- nginx serves it
# straight from ``/var/www/mm`` (a copy of the repo ``www/`` dir) with the
# valid play.riftforge.me TLS cert. (The apex ``riftforge.me`` mixes GitHub
# Pages + droplet A records, so its HTTPS cert does not match the host -- do
# not use it.) ``tools/mudlet/pack_mortals.py`` writes both the www copy and
# the version sidecar this module reads, so the announced version always
# matches the hosted bytes.
# ---------------------------------------------------------------------------
MUDLET_GUI_URL = "https://play.riftforge.me/MortalsAndMonsters.mpackage"
# Human-friendly package name for player-facing verbs (help/mpkg).
MUDLET_PACKAGE_LABEL = "Mortals and Monsters"
# tools/mudlet/MortalsAndMonsters.version (content hash written by pack).
_MUDLET_VERSION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools",
    "mudlet",
    "MortalsAndMonsters.version",
)
# Cache the file read: (mtime, version) so an edit on disk is still picked up
# without hitting the filesystem on every login.
_mudlet_version_cache = (None, None)


def _mudlet_package_version():
    """Return the current package version string, or None if unavailable.

    Read from the sidecar file pack_mortals.py writes; cached by mtime so a
    rebuild on the live tree is noticed without a per-login stat storm.
    """
    global _mudlet_version_cache
    try:
        mtime = os.path.getmtime(_MUDLET_VERSION_FILE)
    except OSError:
        return None
    cached_mtime, cached_version = _mudlet_version_cache
    if cached_mtime == mtime and cached_version:
        return cached_version
    try:
        with open(_MUDLET_VERSION_FILE, encoding="utf-8") as handle:
            version = handle.read().strip()
    except OSError:
        return None
    if not version:
        return None
    _mudlet_version_cache = (mtime, version)
    return version


def maybe_push_client_gui(session):
    """Offer the Mudlet auto-install package to a Mudlet client, once.

    Only Mudlet (identified by its Core.Hello ``client`` field) understands
    Client.GUI, so we gate on it -- the browser / telnet never see it. Sending
    is idempotent per session; Mudlet itself no-ops when already up to date.
    """
    if session is None or not getattr(session, "gmcp_enabled", False):
        return
    if getattr(session, "_client_gui_sent", False):
        return
    if str(getattr(session, "gmcp_client", "") or "").lower() != "mudlet":
        return
    version = _mudlet_package_version()
    if not version:
        return
    session._client_gui_sent = True
    session.send_gmcp("Client.GUI", {"version": version, "url": MUDLET_GUI_URL})


def force_push_client_gui(session) -> bool:
    """Manually (re)send Client.GUI to a Mudlet client. Returns True if sent.

    Backs the in-game ``mpkg download`` verb: unlike maybe_push_client_gui this
    ignores the once-per-session guard, so a player can pull or re-pull the
    package on demand (fresh install, or force an upgrade). Non-Mudlet clients
    get nothing -- the verb prints copy-paste instructions instead.
    """
    if session is None or not getattr(session, "gmcp_enabled", False):
        return False
    if str(getattr(session, "gmcp_client", "") or "").lower() != "mudlet":
        return False
    version = _mudlet_package_version()
    if not version:
        return False
    session._client_gui_sent = True
    session.send_gmcp("Client.GUI", {"version": version, "url": MUDLET_GUI_URL})
    return True

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
    # Live combat-pit / Mudlet spectator channel (supers/combat_viz.py).
    "RiftForge.Combat 1",
    "RiftForge.Char 1",
    # America overland camera + Mudlet mapper dock + browser HUD (engine/map_ui.py).
    "RiftForge.Map 1",
    # Interior zone mapper (engine/zone_hud.py) -- streets + visited rooms.
    "RiftForge.Zone 1",
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


def _wants_mapper_gmcp(character) -> bool:
    """False when the body asked for spoken/plain output (no glyph dumps).

    Phone apps such as MudRammer print unknown GMCP as one story line.
    The America atlas payload is thousands of glyphs with no newlines --
    that is the giant line after every copyover (bug report 1221).
    Screenreader mode already hides the HUD maps; skip mapper packages.
    """
    if character is None:
        return True
    return not bool(getattr(character, "screenreader", False))


def _notify_gateway_gmcp_supports(session) -> None:
    """Copy this session's Core.Supports map onto the long-lived gateway slot.

    Browser WebSocket clients already stash Supports in the gateway. Telnet
    (Mudlet, MudRammer, TinTin++) never hit that path, so reattach used to
    assume the full SERVER_SUPPORTS list -- including RiftForge.Map.Atlas.
    """
    bridge = getattr(session, "gateway_bridge", None)
    sid = getattr(session, "gateway_session_id", None)
    if bridge is None or not sid:
        return
    notify = getattr(bridge, "notify_gmcp_supports", None)
    if not callable(notify):
        return
    mapping = dict(getattr(session, "gmcp_supports", None) or {})
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(notify(sid, mapping))

    def _done(done):
        err = done.exception() if not done.cancelled() else None
        if err is None:
            return
        print(
            f"[gmcp] gateway supports notify failed sid={sid}: {err!r}",
            flush=True,
        )

    task.add_done_callback(_done)


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


def _refresh_after_supports(session):
    """Re-push identity / room / America atlas after the client changes Supports.

    Stock Mudlet sends Core.Supports.Set without RiftForge.Map. The mapper
    package then sends Core.Supports.Add once its script loads -- often
    *after* login -- so Atlas would never go out if we only handled Set.
    """
    if session.character is None:
        return
    push_char_identity(session.character)
    push_vitals(session.character)
    push_room(session.character)
    push_map(session.character, force_grid=True)
    push_map_atlas(session.character)


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
            session.gmcp_client = str(cid or "")
            log.debug("GMCP Core.Hello from client=%s version=%s", cid, cver)
        # Mudlet identifies itself here; offer the auto-install package.
        maybe_push_client_gui(session)
        return
    if package == "Core.Supports.Set":
        session.gmcp_supports = _parse_supports_list(payload)
        # Client just told us what it wants -- push identity packages if
        # a character is already attached (copyover / late Supports).
        _notify_gateway_gmcp_supports(session)
        _refresh_after_supports(session)
        return
    if package == "Core.Supports.Add":
        session.gmcp_supports.update(_parse_supports_list(payload))
        _notify_gateway_gmcp_supports(session)
        _refresh_after_supports(session)
        return
    if package == "Core.Supports.Remove":
        for name in _parse_supports_list(payload):
            session.gmcp_supports.pop(name, None)
        _notify_gateway_gmcp_supports(session)
        return
    if package == "RiftForge.Client":
        # Browser HUD prefs (gag captured comms from the main transcript).
        # Telnet clients never send this package, so they keep full prose.
        if isinstance(payload, dict) and "gag_comms" in payload:
            session.gag_captured_comms = _gag_comms_flag(payload.get("gag_comms"))
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
        if telnet.handle_negotiate(session, _cmd, option):
            return
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
                # Chargen / account-link can take many read_line turns after
                # the client already sent Core.Supports.Set with no character
                # attached -- re-push once GMCP is live when a body exists.
                _refresh_after_supports(session)
            return
        if _cmd == telnet.DONT:
            session.gmcp_enabled = False
            session.gmcp_supports = {}
            session.gag_captured_comms = False
            return
        if _cmd == telnet.WILL:
            # Client offers GMCP -- ask them to enable it.
            session._write_raw(telnet.do(telnet.TELOPT_GMCP))
            return
        if _cmd == telnet.WONT:
            session.gmcp_enabled = False
            session.gmcp_supports = {}
            session.gag_captured_comms = False
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


def resume_gmcp_after_gateway_reattach(session):
    """Restore GMCP Supports after a game-only gateway reload.

    ``reset_gmcp()`` wipes ``gmcp_supports``; Mudlet does not re-send
    Core.Supports.Set after the Veil. The gateway stashes the last client
    Supports map on ``session._gateway_reattach_gmcp_supports`` (browser
    WebSocket path, plus telnet once the game copies Supports back to the
    gateway slot). Restore that map and re-push. When the stash is empty,
    do **not** assume SERVER_SUPPORTS -- that list includes RiftForge.Map
    and dumps the full America atlas as one GMCP frame. Phone apps such as
    MudRammer print that JSON as a giant story line after every copyover
    (bug report 1221). Offer WILL GMCP and wait for a real Supports frame.
    """
    saved = getattr(session, "_gateway_reattach_gmcp_supports", None)
    session._gateway_reattach_gmcp_supports = None
    session._recv_buf = bytearray()
    session._text_buf = bytearray()
    session._pending_lines.clear()
    session._web_map_grid_ids = set()
    session._web_map_atlas_id = None
    session.gag_captured_comms = False
    if isinstance(saved, dict) and saved:
        session.gmcp_supports = dict(saved)
    else:
        session.gmcp_supports = {}
    session.gmcp_enabled = bool(session.gmcp_supports)
    if session.gmcp_enabled:
        send_hello(session)
        send_supports(session)
        _refresh_after_supports(session)
        return
    offer_gmcp(session)


# --- Outbound package helpers ---------------------------------------------

def push_char_name(character):
    """Send Char.Name for an online character (no-op if unsupported)."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Char.Name"):
        return
    # Public face (Wits(GM) / Jimmy Novak) -- never gmspirit: / husk: keys.
    from engine.command_support import _display_name
    name = _display_name(character) if character is not None else ""
    session.send_gmcp("Char.Name", {"name": name, "fullname": name})


def push_char_status(character):
    """Send Char.Status (engine base + optional SUPERS hook extras)."""
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "Char.Status"):
        return
    from engine import hooks
    from engine.command_support import _display_name
    payload = {
        "name": _display_name(character) if character is not None else "",
        "idle": "1" if getattr(character, "idle_mode", False) else "0",
        "gm": "1" if getattr(character, "gm_mode", False) else "0",
        # Browser HUD hides the atlas when this is "1" (help browser).
        "screenreader": (
            "1" if getattr(character, "screenreader", False) else "0"
        ),
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


def push_combat_swing(character, event):
    """Send RiftForge.Combat.Swing (live pit / Mudlet spectator).

    Emits whenever the session has GMCP enabled. The package is advertised
    in Core.Supports.Set; clients that ignore unknown packages stay fine.
    Never invents outcomes — payload is built upstream from a frozen brief.
    """
    session = getattr(character, "session", None)
    if session is None or not getattr(session, "gmcp_enabled", False):
        return
    if not isinstance(event, dict):
        return
    session.send_gmcp("RiftForge.Combat.Swing", event)


def push_char_pose(character, event):
    """Send RiftForge.Char.Pose (personal-mode idle facing for the pit)."""
    session = getattr(character, "session", None)
    if session is None or not getattr(session, "gmcp_enabled", False):
        return
    if not isinstance(event, dict):
        return
    session.send_gmcp("RiftForge.Char.Pose", event)


def push_char_identity(character):
    """Name + Status together (login / Supports / rename)."""
    push_char_name(character)
    push_char_status(character)


def room_info_payload(character):
    """Build a Room.Info dict from the character's current location.

    Engine-safe fields only (no SUPERS). Returns None when there is no room.
    Dark rooms still get id/name/area; desc and exits are omitted until seen.

    Hand rooms with a ``vnum`` emit a Mudlet-safe packed integer as ``num``
    plus the human code as ``id``. Grid / unstamped rooms use ``num`` 0.
    Exit targets are packed ints when the destination has a vnum, else -1.
    """
    room = getattr(character, "location", None)
    if room is None:
        return None
    from engine import hooks
    from engine import room_vnum as room_vnum_mod
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
            dest_vnum = getattr(dest, "vnum", None)
            if dest_vnum:
                exits[direction] = room_vnum_mod.pack_vnum(dest_vnum)
            else:
                # Stock Mudlet mappers need integers; unknown = -1.
                exits[direction] = -1

    look_name = room.look_title() if hasattr(room, "look_title") else (
        getattr(room, "key", "") or ""
    )
    from engine.systems import vehicles as vehicles_mod

    area_room = vehicles_mod.look_area_source_room(game, room)
    zone = getattr(area_room, "zone", None)
    map_id = getattr(area_room, "map_id", None)
    if zone:
        area = str(zone)
    elif map_id:
        area = str(map_id)
    else:
        area = "world"
    environment = getattr(area_room, "area_type", "plains") or "plains"

    vnum = getattr(room, "vnum", None)
    if vnum:
        num = room_vnum_mod.pack_vnum(vnum)
        human_id = room_vnum_mod.validate_vnum(vnum)
    else:
        num = 0
        human_id = None

    payload = {
        "num": num,
        "name": look_name,
        "area": area,
        "environment": environment,
        "exits": exits,
    }
    if human_id:
        payload["id"] = human_id
    if can_see:
        desc = getattr(room, "description", None) or getattr(room, "desc", None)
        if desc:
            payload["desc"] = desc
    return payload


def _gag_comms_flag(value) -> bool:
    """Parse inbound gag_comms (true/false, 1/0, \"1\"/\"0\")."""
    if value is True or value == 1:
        return True
    if value is False or value == 0 or value is None:
        return False
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    return False


def push_room(character):
    """Send Room.Info for the character's current room."""
    session = getattr(character, "session", None)
    if session is None:
        return
    if client_supports(session, "Room.Info"):
        payload = room_info_payload(character)
        if payload:
            session.send_gmcp("Room.Info", payload)
        push_map(character)
    # Fog + interior graph only for HUD/Mudlet clients that asked for them.
    # Telnet does not grow visited_room_keys on every look.
    if _wants_mapper_gmcp(character) and (
        client_supports(session, "RiftForge.Zone")
        or client_supports(session, "RiftForge.Map")
    ):
        from engine import zone_hud

        zone_hud.record_visit(character)
        push_zone(character)


def push_zone(character):
    """Send RiftForge.Zone (interior streets + visited rooms).

    Advertised as its own package; the browser also lists RiftForge.Map,
    so a Map-only client still gets the interior graph.
    """
    session = getattr(character, "session", None)
    if session is None:
        return
    if not _wants_mapper_gmcp(character):
        return
    if not (
        client_supports(session, "RiftForge.Zone")
        or client_supports(session, "RiftForge.Map")
    ):
        return
    from engine import zone_hud

    game = getattr(session, "game", None)
    payload = zone_hud.build_zone_payload(character, game)
    if not payload:
        return
    session.send_gmcp("RiftForge.Zone", payload)


def push_map(character, *, force_grid=False):
    """Send RiftForge.Map you-are-here + landmarks (no 96x60 grid).

    The full-country silhouette is ``RiftForge.Map.Atlas`` (compact rows)
    so a 64 KB WebSocket frame is not truncated. The HUD keeps that
    country map until the plane / atlas id changes. ``atlas`` in the
    transcript is the screen-sized camera (``RiftForge.Map.View``) --
    the HUD does not swap to it.
    """
    session = getattr(character, "session", None)
    if session is None or not client_supports(session, "RiftForge.Map"):
        return
    if not _wants_mapper_gmcp(character):
        return
    game = getattr(session, "game", None)
    from engine import map_hud

    atlas = getattr(game, "overland_atlas", None) if game is not None else None
    map_id = str(getattr(atlas, "map_id", "") or "") if atlas is not None else ""
    last_atlas = getattr(session, "_web_map_atlas_id", None)
    if map_id and (force_grid or map_id != last_atlas):
        session._web_map_atlas_id = map_id
        push_map_atlas(character, game=game)
    payload = map_hud.build_map_payload(
        character, game, include_grid=False
    )
    if not payload:
        return
    session.send_gmcp("RiftForge.Map", payload)
    # Compact here-ping so Mudlet can move @ on the country silhouette
    # without waiting for the telnet camera (Map.View). look / overland
    # hops already call push_room -> push_map; View is only atlas / map big.
    _push_map_here(
        session,
        payload.get("you"),
        atlas=payload.get("title") or payload.get("map"),
    )


def push_comm(session, chan: str, msg: str, player: str):
    """Send Comm.Channel to one session (parallel to prose, never instead)."""
    if session is None or not client_supports(session, "Comm.Channel"):
        return
    character = getattr(session, "character", None) or getattr(session, "owner", None)
    if character is not None:
        from engine import display_prefs

        display_prefs.ensure_display_defaults(character)
        if display_prefs.wants_plain_comms(character):
            # Mudlet/Mudrammer render Comm.Channel with bracket chrome from
            # raw player/msg fields — skip when plain telnet is authoritative.
            return
    session.send_gmcp(
        "Comm.Channel",
        {"chan": chan, "msg": msg, "player": player},
    )


def deliver_comm(session, chan: str, msg: str, player: str, *prose_lines):
    """Always push Comm.Channel; skip transcript prose when the HUD gags.

    Gateway batches leftover telnet into one op:text frame, so the browser
    cannot reliably strip OOC/tells itself. When session.gag_captured_comms
    is True the pane still gets GMCP; the main log does not. Telnet never
    sets the flag, so it keeps every line. Room say/emote must not use this
    helper -- those stay in the transcript even if a later slice captures
    them on Comm.Channel.
    """
    push_comm(session, chan, msg, player)
    if session is None:
        return
    gagged = bool(getattr(session, "gag_captured_comms", False))
    # Only skip prose when this client actually receives Comm.Channel;
    # otherwise gag would mute with no pane to show the line.
    if gagged and client_supports(session, "Comm.Channel"):
        character = getattr(session, "character", None) or getattr(
            session, "owner", None
        )
        if character is not None:
            from engine import display_prefs

            display_prefs.ensure_display_defaults(character)
            if display_prefs.wants_plain_comms(character):
                gagged = False
    if gagged and client_supports(session, "Comm.Channel"):
        return
    for line in prose_lines:
        if line is None:
            continue
        session.send(line)


def _push_map_here(session, you, *, atlas=None, origin=None):
    """Compact you-are-here ping so a mapper can move @ without a glyph redraw.

    Optional ``route`` (drive-preview overlay) stays on View only: Here is
    a position ping (``you`` / ``origin``) so clients can skip a country
    redraw. The trail is a full-window glyph change that belongs with
    ``rows`` on View.
    """
    if session is None:
        return
    character = getattr(session, "character", None)
    if not _wants_mapper_gmcp(character):
        return
    if not (
        client_supports(session, "RiftForge.Map")
        or client_supports(session, "RiftForge.Map.Here")
    ):
        return
    session.send_gmcp(
        "RiftForge.Map.Here",
        {
            "you": you,
            "atlas": atlas,
            "origin": origin,
        },
    )


def push_map_view(character, payload):
    """Send RiftForge.Map.View (viewport glyphs + you-are-here)."""
    session = getattr(character, "session", None)
    if session is None or not payload:
        return
    if not _wants_mapper_gmcp(character):
        return
    if not (
        client_supports(session, "RiftForge.Map")
        or client_supports(session, "RiftForge.Map.View")
    ):
        return
    session.send_gmcp("RiftForge.Map.View", payload)
    _push_map_here(
        session,
        payload.get("you"),
        atlas=payload.get("atlas"),
        origin=payload.get("origin"),
    )


def push_map_atlas(character, game=None):
    """Send RiftForge.Map.Atlas once so Mudlet can draw the full country.

    Compact north-first glyph rows, no ANSI. Safe to call often -- skipped
    when the client does not advertise RiftForge.Map.
    """
    session = getattr(character, "session", None)
    if session is None:
        return
    if not _wants_mapper_gmcp(character):
        return
    if not (
        client_supports(session, "RiftForge.Map")
        or client_supports(session, "RiftForge.Map.Atlas")
    ):
        return
    if game is None:
        game = getattr(session, "game", None)
    atlas = getattr(game, "overland_atlas", None) if game is not None else None
    if atlas is None:
        return
    from engine import atlas_geo

    width = int(atlas.width)
    height = int(atlas.height)
    prefix = getattr(atlas, "prefix", None) or "America Overland"
    rows = []
    for y in range(height - 1, -1, -1):
        cells = []
        for x in range(width):
            cell = atlas.terrain.get((x, y)) or {}
            authored = str(cell.get("map_glyph") or "").strip()
            if authored:
                cells.append(authored[0])
                continue
            area = atlas.terrain_at(x, y)
            # Tiny alphabet so a mapper theme can color by letter.
            glyph = {
                "ocean": "~",
                "lake": "o",
                "plains": ".",
                "forest": "T",
                "mountains": "^",
                "hills": "n",
                "desert": ",",
                "swamp": ",",
                "wetland": "'",
                "city": "*",
                "highway": "=",
                "road": "=",
                "void": " ",
            }.get(area or "", ".")
            cells.append(glyph)
        rows.append("".join(cells))
    session.send_gmcp(
        "RiftForge.Map.Atlas",
        {
            "id": getattr(atlas, "map_id", None) or "earth_america",
            "prefix": prefix,
            "width": width,
            "height": height,
            "projection": atlas_geo.projection_for_size(width, height),
            "rows": rows,
        },
    )


def on_session_attach(character, game=None):
    """Push identity + vitals after login/reconnect (Room comes from look)."""
    push_char_identity(character)
    push_vitals(character)
    push_room(character)
    push_map(character, force_grid=True)
    push_map_atlas(character, game=game)
