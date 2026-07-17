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
# 70 = MSSP (Mud Server Status Protocol) -- listing crawlers / Mudlet.
# 201 = GMCP (Generic Mud Communication Protocol) -- Mudlet gauges / UI.
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


def text_to_command_line(text: bytes) -> str:
    """Turn accumulated application text into one stripped printable line.

    Matches the old _clean() printable-ASCII policy for login / commands so
    control bytes and high-bit garbage never become Character keys. Newlines
    and CR are discarded (the caller already split on line endings).
    """
    out = []
    for b in text:
        if 32 <= b < 127:
            out.append(chr(b))
    return "".join(out).strip()
