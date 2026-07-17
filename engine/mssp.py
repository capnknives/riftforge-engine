"""
mssp.py -- Mud Server Status Protocol (telnet option 70).

MSSP lets listing crawlers (TinTin MSSP crawler, MudVerse, Grapevine, Mudlet)
learn NAME / PLAYERS / UPTIME and related metadata without logging in as a
player. Wire framing lives in engine/telnet.py; this module owns the status
dict, SB encode, MudVerse text fallback, and negotiate / offer helpers.

Spec: https://tintin.mudhalla.net/protocols/mssp/

Pure engine: no supers imports. Static fields are constants here; PLAYERS and
UPTIME read live Game state.
"""

from engine import telnet

# Subnegotiation markers inside IAC SB MSSP ... IAC SE (not printable ASCII).
MSSP_VAR = 1
MSSP_VAL = 2

# Listing-facing game name -- matches Session.run's welcome banner.
MSSP_NAME = "Mortals and Monsters"

# Fixed metadata crawlers expect. Omit CONTACT / WEBSITE / DISCORD until
# those URLs exist in-repo (do not invent).
_STATIC_FIELDS = (
    ("NAME", MSSP_NAME),
    ("PORT", "4000"),
    ("CODEBASE", "Riftforge"),
    ("FAMILY", "Custom"),
    ("GENRE", "Horror"),
    ("SUBGENRE", "Urban Fantasy"),
    ("STATUS", "Alpha"),
    ("GAMEPLAY", "Roleplaying"),
    ("GAMESYSTEM", "Custom"),
    ("LANGUAGE", "English"),
    ("CHARSET", "ASCII"),
    ("ANSI", "1"),
    ("UTF-8", "0"),
    ("PAY TO PLAY", "0"),
    ("PAY FOR PERKS", "0"),
    ("CRAWL DELAY", "-1"),
)

# Text probes some listing sites send at the name prompt when WILL MSSP
# never arrived (MudVerse / Grapevine fallback). Exact match, case-folded.
TEXT_PROBE_NAMES = frozenset({"mssp", "mssp-request"})


def player_count(game) -> int:
    """How many logged-in sessions are online (same notion as `who`)."""
    if game is None:
        return 0
    sessions = getattr(game, "sessions", None) or []
    return len(sessions)


def build_status(game):
    """Return MSSP (name, value) pairs for binary SB or text reply.

    Required variables NAME / PLAYERS / UPTIME come first; static catalog
    fields follow. Values are always strings (numeric fields as decimal).
    """
    started = getattr(game, "started_at", None) if game is not None else None
    if started is None:
        # Smoke / half-built Game: UPTIME 0 rather than crash a crawler.
        uptime = "0"
    else:
        uptime = str(int(started))
    pairs = [
        ("NAME", MSSP_NAME),
        ("PLAYERS", str(player_count(game))),
        ("UPTIME", uptime),
    ]
    # Skip the duplicate NAME already emitted as required.
    for key, value in _STATIC_FIELDS:
        if key == "NAME":
            continue
        pairs.append((key, value))
    return pairs


def encode_subneg(pairs) -> bytes:
    """Build IAC SB MSSP (VAR name VAL value)* IAC SE as raw socket bytes.

    Variable/value bytes must not contain VAR, VAL, IAC, or NUL -- our
    catalog is ASCII-safe. Still escape IAC in the payload via escape_iac
    so a future field with 0xFF cannot break framing.
    """
    body = bytearray()
    for name, value in pairs:
        body.append(MSSP_VAR)
        body.extend(str(name).encode("ascii", errors="replace"))
        body.append(MSSP_VAL)
        body.extend(str(value).encode("ascii", errors="replace"))
    escaped = telnet.escape_iac(bytes(body))
    return (
        bytes((telnet.IAC, telnet.SB, telnet.TELOPT_MSSP))
        + escaped
        + bytes((telnet.IAC, telnet.SE))
    )


def format_text_reply(pairs) -> str:
    """MudVerse / Grapevine plaintext MSSP block (tab-separated fields).

    Sent when a crawler types mssp / mssp-request at the login name prompt
    instead of negotiating telnet option 70.
    """
    lines = ["MSSP-REPLY-START"]
    for name, value in pairs:
        lines.append(f"{name}\t{value}")
    lines.append("MSSP-REPLY-END")
    return "\r\n".join(lines)


def send_status(session):
    """Push one MSSP SB frame built from the session's Game."""
    if session is None or not getattr(session, "alive", False):
        return
    pairs = build_status(getattr(session, "game", None))
    session._write_raw(encode_subneg(pairs))


def offer_mssp(session):
    """Send IAC WILL MSSP at connect / copyover resume."""
    session._write_raw(telnet.will(telnet.TELOPT_MSSP))


def handle_negotiate(session, cmd):
    """Answer a client's WILL/WONT/DO/DONT for option MSSP.

    Spec handshake: server WILL, client DO, server sends SB once. Client
    WILL/WONT are ignored (MSSP is server-driven). DONT means they declined.
    """
    if cmd == telnet.DO:
        send_status(session)
        return
    if cmd == telnet.DONT:
        return
    # Client offering WILL MSSP is unusual; we do not DO them -- we already
    # WILL'd at connect. Ignore WILL/WONT from the client.
    return


def is_text_probe(raw_name: str) -> bool:
    """True when the login name line is a known MSSP text fallback probe."""
    return (raw_name or "").strip().lower() in TEXT_PROBE_NAMES


def reply_text_probe(session):
    """Send the MudVerse text MSSP block to a name-prompt probe session."""
    pairs = build_status(getattr(session, "game", None))
    # One send() so each line is a normal telnet prose line (\\r\\n).
    # format_text_reply already embeds \\r\\n between rows; Session.send
    # appends another -- split and send line-by-line instead.
    for line in format_text_reply(pairs).split("\r\n"):
        session.send(line)
