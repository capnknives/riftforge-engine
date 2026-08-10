"""
telnet.py -- minimal telnet option / subnegotiation parser.

Riftforge historically stripped IAC sequences in connection._clean and never
negotiated options. GMCP (option 201) needs a real parse so IAC SB ... IAC SE
frames are not eaten as garbage and so WILL/DO/WONT/DONT can be answered.

Stdlib only; no third-party telnet libraries. Learning-project comments
explain the wire bytes beginners usually have not seen.
"""

# Telnet command bytes (RFC 854). IAC = "Interpret As Command".
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250   # Subnegotiation Begin
SE = 240   # Subnegotiation End

# Telnet option numbers we care about.
# 1 = ECHO -- remote echo on/off (password masking at login).
# 31 = NAWS (Negotiate About Window Size) -- client reports cols/rows.
# 70 = MSSP (Mud Server Status Protocol) -- listing crawlers / Mudlet.
# 201 = GMCP (Generic Mud Communication Protocol) -- Mudlet gauges / UI.
TELOPT_ECHO = 1
TELOPT_NAWS = 31
TELOPT_MSSP = 70
TELOPT_GMCP = 201

# Event kinds returned by parse_stream (string tags, easy to match on).
EV_NEGOTIATE = "negotiate"   # WILL/WONT/DO/DONT + option
EV_SUBNEG = "subneg"         # SB option + payload bytes (IAC IAC already unescaped)


def will(option: int) -> bytes:
    """Build IAC WILL <option> (server offers to enable an option)."""
    return bytes((IAC, WILL, option & 0xFF))


def wont(option: int) -> bytes:
    """Build IAC WONT <option> (server refuses / disables an option)."""
    return bytes((IAC, WONT, option & 0xFF))


def do(option: int) -> bytes:
    """Build IAC DO <option> (server asks the client to enable an option)."""
    return bytes((IAC, DO, option & 0xFF))


def dont(option: int) -> bytes:
    """Build IAC DONT <option> (server asks the client to disable an option)."""
    return bytes((IAC, DONT, option & 0xFF))


def unescape_iac(data: bytes) -> bytes:
    """Turn doubled IAC (0xFF 0xFF) inside SB payloads back into a single 0xFF.

    Telnet escapes a literal 255 in subnegotiation data as IAC IAC so it is
    not mistaken for a real command. Callers that build SB frames must
    re-escape with escape_iac().
    """
    if IAC not in data:
        return data
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == IAC and i + 1 < n and data[i + 1] == IAC:
            out.append(IAC)
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def escape_iac(data: bytes) -> bytes:
    """Double every 0xFF so a SB payload cannot inject a real IAC command."""
    if IAC not in data:
        return data
    return data.replace(bytes((IAC,)), bytes((IAC, IAC)))


def parse_stream(buf: bytes):
    """Parse a receive buffer into text, events, and an unconsumed remainder.

    Returns (text_bytes, events, remainder):
      - text_bytes: raw application data (may include CR/LF); not yet stripped
        to printable ASCII -- Session decides how to turn this into a line.
      - events: list of ("negotiate", cmd, option) or ("subneg", option, data)
      - remainder: bytes that need more input (incomplete IAC / SB)

    Incomplete sequences stay in remainder so the next read can finish them.
    """
    text = bytearray()
    events = []
    i, n = 0, len(buf)

    while i < n:
        b = buf[i]
        if b != IAC:
            text.append(b)
            i += 1
            continue

        # Need at least IAC + one command byte.
        if i + 1 >= n:
            break

        cmd = buf[i + 1]

        # IAC IAC -> literal 0xFF in the text stream (rare in MUD input).
        if cmd == IAC:
            text.append(IAC)
            i += 2
            continue

        # WILL / WONT / DO / DONT need an option byte.
        if cmd in (WILL, WONT, DO, DONT):
            if i + 2 >= n:
                break
            option = buf[i + 2]
            events.append((EV_NEGOTIATE, cmd, option))
            i += 3
            continue

        # Subnegotiation: IAC SB <opt> <data...> IAC SE
        if cmd == SB:
            if i + 2 >= n:
                break
            option = buf[i + 2]
            # Scan for IAC SE, treating IAC IAC as escaped data.
            j = i + 3
            payload = bytearray()
            complete = False
            while j < n:
                if buf[j] != IAC:
                    payload.append(buf[j])
                    j += 1
                    continue
                if j + 1 >= n:
                    # Trailing IAC -- wait for more bytes.
                    break
                nxt = buf[j + 1]
                if nxt == IAC:
                    payload.append(IAC)
                    j += 2
                    continue
                if nxt == SE:
                    complete = True
                    j += 2
                    break
                # Unexpected IAC+cmd inside SB -- treat as end of this SB
                # attempt and let the outer loop re-parse from here.
                break
            if not complete:
                break
            events.append((EV_SUBNEG, option, bytes(payload)))
            i = j
            continue

        # Other IAC commands (NOP, GA, …): skip IAC + cmd only.
        i += 2

    remainder = bytes(buf[i:])
    return bytes(text), events, remainder


def parse_naws_payload(data: bytes):
    """Decode NAWS SB payload into (width, height) or (None, None).

    RFC 1073: four bytes, 16-bit width then 16-bit height, each in network
    (big-endian) byte order. Returns (None, None) when too short or invalid.
    """
    if len(data) < 4:
        return None, None
    width = (data[0] << 8) | data[1]
    height = (data[2] << 8) | data[3]
    # Sanity floor -- zero-width terminals are useless for layout.
    if width < 20 or height < 4:
        return None, None
    return width, height


def offer_naws(session) -> None:
    """Ask the client to send window size (IAC DO NAWS).

    Clients that ignore NAWS are unaffected -- no reply, defaults stay.
    """
    session._write_raw(do(TELOPT_NAWS))


def set_client_echo(session, enabled: bool) -> None:
    """Toggle remote echo for password prompts (IAC WILL / WONT ECHO).

    RFC 857: when the server sends WILL ECHO it takes over echoing, so the
    client stops local echo (password masking). WONT ECHO releases echo back
    to the client for normal typing.

    ``enabled=True`` restores local echo after a masked password read.
    ``enabled=False`` masks the next line on clients that honor ECHO.
    Plain ``nc`` clients ignore the negotiation and still show keystrokes --
    that is acceptable graceful fallback.
    """
    if enabled:
        session._write_raw(wont(TELOPT_ECHO))
    else:
        session._write_raw(will(TELOPT_ECHO))


def handle_negotiate(session, cmd: int, option: int) -> bool:
    """Apply one WILL/WONT/DO/DONT event. Return True when handled here.

    NAWS and ECHO are engine-owned; GMCP/MSSP stay in their modules.
    Unhandled options return False so gmcp.handle_telnet_event can refuse
    politely (DONT/WONT) without double-replying.
    """
    if option == TELOPT_NAWS:
        if cmd == WILL:
            # Client will send NAWS SB when the window changes -- ack it.
            session._write_raw(do(TELOPT_NAWS))
        elif cmd == DO:
            # Client asks us to send NAWS -- we only consume, never emit.
            session._write_raw(wont(TELOPT_NAWS))
        elif cmd in (WONT, DONT):
            # Client declined -- leave term_width/term_height unset.
            pass
        return True
    if option == TELOPT_ECHO:
        # Acknowledge client ECHO negotiation without fighting local echo
        # policy (password masking uses set_client_echo on the server side).
        if cmd == DO:
            session._write_raw(will(TELOPT_ECHO))
        elif cmd == WILL:
            session._write_raw(do(TELOPT_ECHO))
        elif cmd == DONT:
            session._write_raw(wont(TELOPT_ECHO))
        elif cmd == WONT:
            session._write_raw(dont(TELOPT_ECHO))
        return True
    return False


def handle_naws_subneg(session, data: bytes) -> None:
    """Store NAWS width/height on the Session for layout formatters."""
    from engine import display_prefs

    width, height = parse_naws_payload(data)
    if width is None:
        return
    # Share the same clamp as ``config width`` player prefs.
    session.term_width = max(
        display_prefs.WIDTH_MIN,
        min(display_prefs.WIDTH_MAX, int(width)),
    )
    session.term_height = int(height)


def text_to_command_line(text: bytes, *, keep_indent: bool = False) -> str:
    """Turn accumulated application text into one printable line.

    Matches the old _clean() printable-ASCII policy for login / commands so
    control bytes and high-bit garbage never become Character keys. Newlines
    and CR are discarded (the caller already split on line endings).

    Default ``keep_indent=False`` strips leading and trailing spaces (normal
    command lines). Pass ``keep_indent=True`` for modal editors / paste
    capture (hedit, bug/suggest) so intentional leading indent survives --
    only trailing whitespace is trimmed then.
    """
    out = []
    for b in text:
        if 32 <= b < 127:
            out.append(chr(b))
    line = "".join(out)
    # Editors need leading spaces; commands do not.
    return line.rstrip() if keep_indent else line.strip()
