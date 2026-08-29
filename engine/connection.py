"""
connection.py — one Session per connected client.

The Session is the ONLY thing that touches the network. It runs a small state
machine: greet -> ask for a name -> then loop reading commands until the client
disconnects. Everything it reads gets handed to commands.dispatch().

'async def' functions are coroutines: they can pause at an 'await' (e.g. while
waiting for the player to type) and let OTHER players' sessions run in the
meantime — all on a single thread. That's how one program serves many players.
"""

import asyncio
import collections
import itertools
import re
import unicodedata
from engine import auth
from engine import hooks
from engine.world import Character, break_follows
from commands import dispatch
# Chargen is registered by the game (supers.bootstrap / server.py) via
# engine.hooks -- this module must not import chargen/supers directly
# (docs/plans/two_repo_purity.md).


# How many recent command lines (plus any traceback they raised) to keep on
# each Session for bug/suggest reports. Tunable placeholder -- same spirit as
# training.py's constants block.
RECENT_HISTORY_SIZE = 10

# Some web/telnet clients (notably darkwiz.org/play multi-window) prepend
# session routing tags like "P1" / "P4" onto every outbound line. Without
# stripping, those tags bake into Character.key at chargen (bug_reports.log
# #28: P1P1Darren / P4P4Darrel). Doubled tags happen when the client tags an
# already-tagged or echoed value -- loop until none remain.
_CLIENT_SESSION_TAG = re.compile(r"^P\d+", re.IGNORECASE)

# Login names after tag strip: letters only, 2-16 chars (no digit sandwiches
# that look like client tags).
LOGIN_NAME_MIN = 2
LOGIN_NAME_MAX = 16

# Failed-login backoff (pen-test H5): after this many wrong passwords for
# the same name+IP, delay further attempts. In-memory only -- clears on
# process restart / copyover (acceptable for a light throttle).
_LOGIN_FAIL_THRESHOLD = 5
_LOGIN_BACKOFF_BASE_SEC = 2.0
_LOGIN_BACKOFF_CAP_SEC = 30.0
# key -> fail_count (cleared on success)
_login_fail_counts = {}


def _log_connection(message):
    """Tagged stderr line for connect/disconnect/login audit (ops grep)."""
    from engine import log_util
    log_util.ops("connection", message)


def history_line_for_storage(line):
    """Return a history line safe to keep in Session.history / bug reports.

    Redacts setpass / gm setpass / castpass / gm castpass so plaintext
    passwords never land in bug_reports.log or squashbugs webhooks
    (pen-test H2).
    """
    text = line if isinstance(line, str) else str(line or "")
    low = text.strip().lower()
    if (
        low.startswith("setpass")
        or low.startswith("gm setpass")
        or low.startswith("castpass")
        or low.startswith("gm castpass")
    ):
        return "[redacted setpass]"
    return text


def expand_bang_repeat(line, last_command):
    """Expand a lone ``!`` to *last_command* (suggestion report 263).

    Returns ``(expanded_line, error_message)``. When *error_message* is
    set, *expanded_line* is ``None`` and the caller should print the error
    without dispatching.
    """
    text = line if isinstance(line, str) else str(line or "")
    if text.strip() != "!":
        return text, None
    if not last_command:
        return None, "No previous command."
    return last_command, None


def tracks_last_command(line):
    """True when *line* should become the session's repeatable last command."""
    return history_line_for_storage(line) != "[redacted setpass]"


def _activity_logger_call(character, method, *args):
    """Duck-typed activity_logger side channel; never abort play or logout.

    Engine must not import supers.activity_log. Player pacelog CMD / SESSION
    lines use the same attach slot as Echo kit soaks.
    """
    if character is None:
        return
    logger = getattr(character, "activity_logger", None)
    if logger is None:
        return
    fn = getattr(logger, method, None)
    if not callable(fn):
        return
    try:
        fn(*args)
    except Exception:
        import traceback
        key = getattr(character, "key", None)
        print(
            f"[connection] activity {method} failed for {key!r}:",
            flush=True,
        )
        traceback.print_exc()


def _login_fail_key(name, peer):
    """Stable tracker key for failed-password backoff."""
    return f"{(name or '').lower()}|{(peer or '')}"


def _login_backoff_seconds(name, peer):
    """Seconds to wait before the next password try (0 if under threshold)."""
    count = _login_fail_counts.get(_login_fail_key(name, peer), 0)
    if count < _LOGIN_FAIL_THRESHOLD:
        return 0.0
    # 5 fails -> 2s, 6 -> 4s, … capped.
    over = count - _LOGIN_FAIL_THRESHOLD + 1
    return min(_LOGIN_BACKOFF_CAP_SEC, _LOGIN_BACKOFF_BASE_SEC * (2 ** (over - 1)))


def _note_login_failure(name, peer):
    """Increment the fail counter for this name+IP."""
    key = _login_fail_key(name, peer)
    _login_fail_counts[key] = _login_fail_counts.get(key, 0) + 1


def _clear_login_failures(name, peer):
    """Forget failures after a successful password."""
    _login_fail_counts.pop(_login_fail_key(name, peer), None)


def normalize_input_line(line: str) -> str:
    """NFKC-normalize player input before command parsing.

    Strips homoglyph / compatibility-character exploits (e.g. Cyrillic
    look-alikes) while leaving ordinary ASCII and accented letters intact.
    """
    if line is None:
        return ""
    if not line:
        return line
    return unicodedata.normalize("NFKC", line)


# Async menus (makecar, phone Carvana, rechargen) call session.read_line()
# from a create_task. play() also calls read_line() for commands. Without
# this gate the command loop steals numbered picks and names (bug report 837).
_MODAL_PROMPT_FLAGS = (
    "_rechargen_running",
    "_makecar_running",
    "_carvana_running",
)


def session_modal_prompt_running(session):
    """True when a verb's async menu owns this session's read_line."""
    if session is None:
        return False
    return any(getattr(session, attr, False) for attr in _MODAL_PROMPT_FLAGS)


def spawn_modal_prompt(session, coro, flag_attr):
    """Schedule ``coro`` and mark ``flag_attr`` before play() can race it.

    The flag is set *synchronously* so the next play() iteration sleeps
    instead of consuming the first menu answer as a command. ``coro`` is
    an already-created coroutine (pass ``_flow(session, ...)``).
    """
    if flag_attr not in _MODAL_PROMPT_FLAGS:
        raise ValueError(f"unknown modal prompt flag {flag_attr!r}")
    setattr(session, flag_attr, True)

    async def _runner():
        try:
            await coro
        finally:
            setattr(session, flag_attr, False)

    asyncio.get_running_loop().create_task(_runner())


def strip_client_session_tags(raw: str) -> str:
    """Remove leading multi-window client tags (P1, P4, …) from a line.

    Returns the remainder unchanged when no tag is present. Safe to call on
    passwords too -- only the known Pn pattern is stripped, not arbitrary
    leading digits.
    """
    if not raw:
        return raw or ""
    text = raw
    # While-loop: P1P1Darren -> P1Darren -> Darren.
    while True:
        match = _CLIENT_SESSION_TAG.match(text)
        if not match:
            break
        text = text[match.end():]
    return text


def normalize_login_name(raw: str):
    """Clean and validate a login name.

    Returns (cleaned_name, error_or_None, was_stripped).
    error_or_None is a player-facing refusal string when invalid.
    was_stripped is True when client session tags were removed.

    Always capitalizes the first letter (Velan, not velan) so a forgotten
    shift key at creation does not leave the only lowercased name on `who`.
    """
    stripped = strip_client_session_tags((raw or "").strip())
    was_stripped = stripped != (raw or "").strip()
    if (
        not stripped
        or not stripped.isalpha()
        or not (LOGIN_NAME_MIN <= len(stripped) <= LOGIN_NAME_MAX)
    ):
        return (
            stripped,
            (
                "Names are 2-16 letters (no digits). "
                "Drop client window prefixes like P1."
            ),
            was_stripped,
        )
    # Title-case the leading letter only; keep the rest as typed
    # (McSomething stays McSomething if they typed it that way).
    cleaned = stripped[0].upper() + stripped[1:]
    return cleaned, None, was_stripped


def apply_login_name_case(character, preferred_name, game=None):
    """If ``preferred_name`` is the same letters as ``character.key`` but
    different casing, rewrite the key (and relationship / mail pointers).

    Used on reconnect so a forgotten shift at creation (``velan``) is fixed
    the next time they log in as ``Velan`` / ``velan`` (normalize capitalizes).
    Returns True when the key changed.
    """
    if not character or not preferred_name:
        return False
    if character.key == preferred_name:
        return False
    if character.key.lower() != preferred_name.lower():
        return False
    old_key = character.key
    character.key = preferred_name
    if game is None:
        return True
    # Mirror GM rename bookkeeping for relationship tags / mail from.
    from engine.world import Character as WorldCharacter
    old_lower = old_key.lower()
    for room in getattr(game, "rooms", {}).values():
        for obj in room.contents:
            if not isinstance(obj, WorldCharacter):
                continue
            rel = getattr(obj, "relationships", None) or {}
            kind = None
            matched_key = None
            for k in list(rel):
                if k.lower() == old_lower:
                    matched_key = k
                    kind = rel.pop(k)
                    break
            if matched_key is not None and kind is not None:
                rel[preferred_name] = kind
                obj.relationships = rel
            box = getattr(obj, "mail_inbox", None) or []
            for letter in box:
                if (letter.get("from") or "").lower() == old_lower:
                    letter["from"] = preferred_name
    return True


def _clean(data: bytes) -> str:
    """Legacy printable-ASCII strip for a single chunk (tests / helpers).

    Live Session input goes through engine.telnet.parse_stream instead so
    GMCP subnegotiation is handled; this remains for callers that still
    pass a finished line's bytes through a simple scrubber.
    """
    from engine import telnet
    text, _events, _rest = telnet.parse_stream(data)
    return telnet.text_to_command_line(text)


# Monotonic ids for stderr correlation on direct telnet (no gateway sid).
_log_session_serial = itertools.count(1)


class Session:
    def __init__(self, reader, writer, game, gateway_session_id=None):
        # reader/writer are asyncio's stream objects for THIS one client's socket
        # (or IPC adapters when RIFTFORGE_GATEWAY=1 — see engine/gateway_client).
        self.reader = reader
        self.writer = writer
        self.game = game
        self.character = None     # set once they pick a name and log in
        # Staff account name while this Session rides GM form / cast / alts.
        self.staff_account = None
        # RPC cast grant ride: account name + PC key to return to on playcast off.
        self.cast_grant_account = None
        self.cast_grant_return_key = None
        self.alive = True         # flips to False on quit/disconnect; ends the loop
        # Coalesce duplicate blank lines (see Session.send).
        self._last_output_empty = False
        # Gateway IPC: fixed session id + optional bridge for bound/kick CTRL.
        # None when speaking telnet directly (RIFTFORGE_GATEWAY=0).
        self.gateway_session_id = gateway_session_id
        self.gateway_bridge = None
        # Short stable id for stderr correlation when not on the gateway.
        self._log_session_id = f"s{next(_log_session_serial)}"
        # Post-login snapshot is deferred until after the first ``look`` so a
        # full-world SQLite pass cannot wedge the menu→play handoff (~30s on
        # large live DBs). See ``_defer_login_save`` / ``play()``.
        self._deferred_login_save = False
        self._deferred_attach_done = False
        # Set by gateway_client on reattach: skip login and jump to play().
        self._gateway_reattach_name = None
        # Ring buffer of recent play-loop lines for bug/suggest reports.
        # Each entry is [raw_line, traceback_or_None] -- a mutable list so the
        # except block below can fill in a traceback after a failed dispatch.
        # collections.deque(maxlen=N) auto-drops the oldest entry when full.
        self.history = collections.deque(maxlen=RECENT_HISTORY_SIZE)
        # Last dispatched play-loop line for bare ``!`` repeat (suggestion 263).
        self.last_command = None
        # Last non-empty client lines for bug-report triage (see
        # engine.report_context.note_report_output).
        self._report_output_ring = collections.deque(maxlen=8)
        # Multi-line bug/suggest capture (a live report: pasting a multi-
        # line message into 'suggest' split across several 'Unknown
        # command' lines instead of landing as one report -- a raw telnet
        # paste arrives as several separate lines on the wire, indistin-
        # guishable from several separate Enter presses, so line 1 alone
        # got treated as the whole report). None when not capturing;
        # otherwise {"kind": reports.BUG|SUGGEST, "lines": [...]} -- see
        # commands.cmd_bug/cmd_suggest (which starts it) and
        # play()/_handle_report_capture_line below (which ends it).
        # Optional ``on_finish`` / ``on_cancel`` callables let other paste
        # UIs (GM MOTD editor) reuse this gate without going through
        # bug_filing -- engine stays generic; the game sets the callbacks.
        self.report_capture = None
        # Modal helpfile line editor (GM `hedit` command; docs/plans/
        # helpfile_editing_system.md). Same shape of gate as report_capture
        # above -- while set, EVERY line goes to _handle_help_edit_line
        # instead of dispatch(). None when not editing; otherwise a dict
        # with the keyword being edited, the body/syntax line buffers, and
        # the category/gm_only/is_ic/aliases metadata collected so far.
        self.help_edit = None
        # Telnet / GMCP state (engine/telnet.py + engine/gmcp.py).
        # _recv_buf holds incomplete IAC/SB bytes across reads; _text_buf
        # accumulates application data until a CR/LF completes a line;
        # _pending_lines queues fully parsed command strings.
        self._recv_buf = bytearray()
        self._text_buf = bytearray()
        self._pending_lines = collections.deque()
        self.gmcp_enabled = False
        self.gmcp_supports = {}
        self._web_map_grid_ids = set()
        self._web_map_atlas_id = None
        # Browser HUD: skip OOC/tell/custom-channel prose in the main log
        # while the communication pane is showing them (GMCP Comm.Channel).
        # Telnet never sets this; default False so full prose still prints.
        self.gag_captured_comms = False
        # Per-session wire counters for GM `host` (bytes on the socket,
        # including telnet/GMCP framing). Stdlib-only ops pulse -- not
        # host-wide NIC stats.
        self.bytes_in = 0
        self.bytes_out = 0
        # NAWS (telnet option 31): when negotiated, overrides framed layout width.
        self.term_width = None
        self.term_height = None
        # Staff-only login phase for `gm users` (not public `who`):
        # "login" = name/password prompts; "creating" = mid-chargen;
        # None = fully in play (on game.sessions) or not yet registered.
        self.login_stage = None
        # Soft ``logout`` (character select without closing TCP): play()
        # exits the command loop, disconnect(keep_connection=True) parks
        # the body as an Echo, then _run_inner re-enters login.
        self._soft_logout = False
        self._soft_relogin = False
        # Account name to prefer for the soft-logout character pick
        # (skip re-typing ``account`` when the body was linked).
        self._soft_logout_account = None
        # Character key of whoever last sent this Session a tell (for ``reply``).
        self.last_tell_from = None
        # Nesting depth for read_password_line telnet ECHO masking. While >0,
        # handle_negotiate may accept DO ECHO; during normal play we refuse
        # server-side echo so clients keep local keystroke echo (bug report 423).
        self._echo_password_mask = 0
        # True while ``rechargen`` owns read_line (idea 220) -- play() waits.
        self._rechargen_running = False

    def _register_connecting(self, stage="login"):
        """Track this socket on game.connecting_sessions for GM users.

        Intentionally separate from game.sessions so public `who` and
        room broadcasts never see half-made characters. Safe to call
        more than once (idempotent membership).
        """
        self.login_stage = stage
        bucket = getattr(self.game, "connecting_sessions", None)
        if bucket is None:
            # Older Game stubs / smoke FakeGames may omit the list.
            self.game.connecting_sessions = []
            bucket = self.game.connecting_sessions
        if self not in bucket:
            bucket.append(self)

    def _set_creating(self):
        """Mark mid-chargen (password stuck; prompts still running)."""
        self.login_stage = "creating"
        # Ensure the bucket still holds us (password path already registered).
        self._register_connecting("creating")
        # Gateway reattach after a Veil rewrite keys off this bind name.
        char = getattr(self, "character", None)
        key = getattr(char, "key", None) if char is not None else None
        if key:
            self._notify_gateway_bound(key)

    def _leave_connecting(self):
        """Drop from connecting_sessions (play promote or disconnect)."""
        bucket = getattr(self.game, "connecting_sessions", None)
        if bucket is not None and self in bucket:
            bucket.remove(self)

    def _promote_to_sessions(self):
        """Leave connecting bucket and join game.sessions for who/play."""
        self._leave_connecting()
        self.login_stage = None
        char = getattr(self, "character", None)
        if char is not None:
            from engine.session_attach import detach_stale_sessions

            dropped = detach_stale_sessions(char, self.game, self)
            if dropped:
                _log_connection(
                    f"detached {dropped} stale session(s) "
                    f"char={getattr(char, 'key', '?')}"
                )
        if self not in self.game.sessions:
            self.game.sessions.append(self)

    def _notify_gateway_bound(self, name: str):
        """Tell the gateway this sid is logged in (for reattach after restart).

        Always bind the **login body** name, never ``gmspirit:`` / ``husk:``.
        """
        from engine.command_support import strip_ephemeral_storage_prefix
        bind = strip_ephemeral_storage_prefix(name)
        char = getattr(self, "character", None)
        if char is not None:
            body_key = getattr(char, "gm_body_key", None)
            if body_key:
                bind = strip_ephemeral_storage_prefix(body_key)
            elif getattr(char, "gm_spirit", False) or getattr(
                char, "gm_mode", False
            ):
                # Spirit storage key is gmspirit:Login -- peel to Login.
                # Do NOT use the raw spirit key as the bind name.
                bind = strip_ephemeral_storage_prefix(
                    getattr(char, "key", None) or bind
                )
            else:
                # Corporeal body (including gm_away Echo after quit intent).
                bind = strip_ephemeral_storage_prefix(
                    getattr(char, "key", None) or bind
                )
        bridge = self.gateway_bridge
        sid = self.gateway_session_id
        if bridge is None or not sid or not bind or bind == "?":
            return
        from engine import ooc_channel

        ooc_face = ooc_channel.speaker_face_for_session(self, self.game)
        head_gm = ooc_channel.session_is_head_gm(self, self.game)
        staff_gm = ooc_channel.session_is_staff_gm(self, self.game)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            bridge.notify_bound(
                sid,
                bind,
                ooc_face=ooc_face,
                head_gm=head_gm,
                staff_gm=staff_gm,
            )
        )

    def _kick_gateway_client(self):
        """Ask the gateway to drop the public TCP (quit / intentional close)."""
        bridge = self.gateway_bridge
        sid = self.gateway_session_id
        if bridge is None or not sid:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(bridge.kick_client(sid))

    def _notify_gateway_unbound(self):
        """Clear the gateway's bound character name (soft logout / swap).

        Keeps the public TCP held; the next successful login calls
        ``_notify_gateway_bound`` with the new body name.
        """
        bridge = self.gateway_bridge
        sid = self.gateway_session_id
        if bridge is None or not sid:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(bridge.notify_unbound(sid))

    # --- output ------------------------------------------------------------
    def _write_raw(self, data: bytes):
        """Push raw bytes to the socket (no \\r\\n, no ANSI, no snoop).

        Used for telnet negotiation and GMCP frames -- binary that must not
        be treated as a prose line.
        """
        if data:
            self.bytes_out += len(data)
            self.writer.write(data)

    def _write(self, message):
        """Push one line to the socket (no snoop fanout).

        Split out of send() so engine.snoop can relay mirrored lines to a
        GM without re-entering mirror_output (A snoops B snoops A loops).
        """
        # Add the telnet line break, then .encode() turns the string into
        # bytes, which is what the socket actually sends.
        payload = (message + "\r\n").encode()
        self.bytes_out += len(payload)
        self.writer.write(payload)
    def send(self, message):
        """Queue a line to the client.

        writer.write() is NOT a coroutine — it hands the bytes to asyncio, which
        flushes them to the socket on its own. So delivery is automatic; we only
        need drain() (below, in the loop) to apply backpressure if a client is
        slow. That's why command handlers can stay simple synchronous functions.

        Color choke point (suggestions.log #51): when the attached character
        has use_color False, strip ANSI escapes here so every caller can emit
        styled text without checking the preference. Login prompts (no
        character yet) stay as written -- usually plain ASCII.

        After the client write, any GM snooping this character also gets a
        tagged copy (engine/snoop.py) -- classic MUD viewpoint mirroring.
        """
        if self.alive:
            # Strip gothic ANSI when the player turned color off. Import
            # locally so connection.py stays light at module load and the
            # style helpers stay the single source of strip_ansi.
            if self.character is not None and not getattr(
                self.character, "use_color", True
            ):
                from engine import style
                message = style.strip_ansi(message)
            is_empty = not str(message).strip()
            # Coalesce duplicate blanks -- callers (Room.broadcast's
            # blank_after, look's trailing "", send_prompt's leading "")
            # sometimes stack two intentional blanks back to back; only
            # the first reaches the wire. This is a pure safety net -- it
            # never removes a blank a caller actually wanted to be visible
            # (a single blank always survives), and it does NOT add any
            # spacing on its own (see engine.world.Room.broadcast and
            # cmd_tell for where airy paragraph spacing is actually
            # decided -- never here, so an internal loop of many
            # session.send() calls for one logical block, e.g. a herb
            # list or tutorial page, never gets split apart).
            if is_empty and self._last_output_empty:
                return
            self._write(message)
            # Fan out to GM snoopers after the real client has the line.
            if self.character is not None:
                from engine import snoop
                snoop.mirror_output(self.character, message)
            self._last_output_empty = is_empty
            if not is_empty:
                from engine.report_context import note_report_output
                note_report_output(self, message)

    def send_gmcp(self, package, payload, force=False):
        """Send one GMCP package as a telnet subnegotiation frame.

        No-op unless the session is alive and GMCP is enabled (or force=True
        for Core.Hello / Core.Supports during negotiation). Never snoops --
        binary/JSON would be noise on a GM terminal.
        """
        if not self.alive:
            return
        if not force and not self.gmcp_enabled:
            return
        from engine import gmcp
        self._write_raw(gmcp.encode_package(package, payload))

    def close(self):
        """Ask the loop to end this session (used by the 'quit' command)."""
        self.alive = False

    def reset_gmcp(self):
        """Clear negotiation state (copyover resume re-offers WILL GMCP)."""
        self.gmcp_enabled = False
        self.gmcp_supports = {}
        self._web_map_grid_ids = set()
        self._web_map_atlas_id = None
        self.gag_captured_comms = False
        self._recv_buf = bytearray()
        self._text_buf = bytearray()
        self._pending_lines.clear()

    def _ingest_bytes(self, data: bytes):
        """Feed raw socket bytes through the telnet parser into lines/events."""
        from engine import gmcp
        from engine import telnet

        if data:
            self.bytes_in += len(data)
        self._recv_buf.extend(data)
        text, events, remainder = telnet.parse_stream(bytes(self._recv_buf))
        self._recv_buf = bytearray(remainder)
        for event in events:
            if event[0] == telnet.EV_SUBNEG and event[1] == telnet.TELOPT_NAWS:
                telnet.handle_naws_subneg(self, event[2])
                continue
            gmcp.handle_telnet_event(self, event)
        if text:
            self._text_buf.extend(text)
        # Split completed lines out of _text_buf (CRLF / LF / CR).
        while True:
            raw = bytes(self._text_buf)
            nl = -1
            sep_len = 0
            for sep in (b"\r\n", b"\n", b"\r"):
                idx = raw.find(sep)
                if idx == -1:
                    continue
                # Prefer the earliest break; at the same index prefer the
                # longer sep so CRLF is consumed as one unit, not CR then LF.
                if nl == -1 or idx < nl or (idx == nl and len(sep) > sep_len):
                    nl = idx
                    sep_len = len(sep)
            if nl == -1:
                break
            line_bytes = raw[:nl]
            self._text_buf = bytearray(raw[nl + sep_len :])
            # HEDIT / report paste: keep leading indent (space is content).
            keep_indent = (
                self.help_edit is not None or self.report_capture is not None
            )
            line = telnet.text_to_command_line(
                line_bytes, keep_indent=keep_indent,
            )
            self._pending_lines.append(line)

    def _pop_normalized_line(self):
        """Return the next queued line with NFKC normalization applied."""
        if not self._pending_lines:
            return None
        return normalize_input_line(self._pending_lines.popleft())

    # --- input -------------------------------------------------------------
    async def read_line(self):
        """Await one command line, processing interleaved telnet/GMCP.

        Uses reader.read() (not readline) so a client can send IAC SB GMCP
        without a trailing newline and still be heard -- readline would block
        forever waiting for \\n on a pure-GMCP frame.
        """
        while True:
            if self._pending_lines:
                return self._pop_normalized_line()
            # Prefer read() when available (real streams + updated mocks).
            read = getattr(self.reader, "read", None)
            if read is not None:
                data = await read(4096)
            else:
                data = await self.reader.readline()
            if not data:
                # Flush a trailing partial line (client hung up mid-type).
                if self._text_buf:
                    from engine import telnet
                    line = telnet.text_to_command_line(bytes(self._text_buf))
                    self._text_buf = bytearray()
                    return normalize_input_line(line)
                return None
            self._ingest_bytes(data)

    async def read_password_line(self):
        """Like ``read_line``, but mask input with telnet ECHO when supported.

        Always restores echo afterward so the play loop stays readable.
        Clients that ignore IAC WILL/WONT ECHO still work (graceful no-op).
        """
        from engine import telnet

        self._echo_password_mask += 1
        telnet.set_client_echo(self, False)
        try:
            line = await self.read_line()
        finally:
            telnet.set_client_echo(self, True)
            self._echo_password_mask = max(0, self._echo_password_mask - 1)
        if line is None:
            return None
        return strip_client_session_tags(line or "")

    # --- the session lifecycle --------------------------------------------
    async def run(self):
        # Always drop from connecting_sessions on exit (mid-login hangup,
        # chargen abort, or play() end). Promote-to-play already removes us;
        # this is the safety net for early ``return`` paths that skip
        # disconnect(). Soft ``logout`` parks the body as an Echo and
        # re-enters ``_run_inner`` on the same TCP (character select).
        try:
            while True:
                await self._run_inner()
                if not getattr(self, "_soft_relogin", False):
                    break
                self._soft_relogin = False
                self.alive = True
        finally:
            self._leave_connecting()

    async def _wait_veil_world_ready(self):
        """Gateway copyover: block play() until the veil playable gate opens.

        Copyover reattach opens this as soon as sessions are rebound
        (Circle/Diku copyover_recover). Gap-fill heals continue in the
        background and must not hold the gate.
        """
        game = getattr(self, "game", None)
        if game is None:
            return
        if getattr(game, "_veil_world_ready", True):
            return
        import asyncio

        ev = getattr(game, "_veil_ready_event", None)
        if ev is None:
            ev = asyncio.Event()
            game._veil_ready_event = ev
        if not ev.is_set():
            await ev.wait()

    async def _run_inner(self):
        # Gateway reattach: game restarted while this telnet client stayed
        # held -- skip account/character login and resume play() like copyover.
        # Bind name is the character storage key (not account name); account-first
        # login does not change gateway metadata.
        # Preserve idle_mode from the SQLite blob (same as classic copyover
        # resume): Docker/live "copyover" is this path, and clearing the flag
        # here used to snap AFK Echo-watchers back to present mid-reload.
        # Fresh password login still clears idle_mode below -- intentional.
        reattach = getattr(self, "_gateway_reattach_name", None)
        if reattach:
            from engine import account_chargen_draft as draft_mod

            chargen_char = draft_mod.resolve_copyover_chargen_body(
                self.game, reattach,
            )
            if chargen_char is not None:
                # Mid-create: keep the socket, re-enter chargen at the
                # saved step -- never promote to who/play (bug report 618).
                self._gateway_reattach_name = None
                chargen_char.session = self
                self.character = chargen_char
                self._set_creating()
                self.reset_gmcp()
                from engine import gmcp
                from engine import mssp
                from engine import telnet
                gmcp.offer_gmcp(self)
                mssp.offer_mssp(self)
                telnet.offer_naws(self)
                telnet.set_client_echo(self, True)
                from engine.copyover import MSG_AFTER
                from engine.account_login import resume_chargen_after_hold
                self.send(MSG_AFTER)
                await self._wait_veil_world_ready()
                result = await resume_chargen_after_hold(self)
                if result is None:
                    return
                await self.play()
                return
            # Prefer exact login body (never husk: / gmspirit:).
            finder = getattr(self.game, "find_login_character", None)
            if callable(finder):
                char = finder(reattach)
            else:
                char = self.game.find_character(reattach)
            # Hard gm fold: body may be vault-only across a game restart.
            # Unfinished homezone lessons are also vaulted on copyover boot,
            # but those players must log in again -- not skip the name prompt.
            if char is None:
                if not hooks.is_tutorial_incomplete_vault(
                    self.game, reattach
                ) and not hooks.is_chargen_draft_vault(
                    self.game, reattach
                ):
                    char = hooks.try_restore_folded_login(self.game, reattach)
            from engine.accounts import is_staff_login_cast
            if char is None:
                raw = self.game.find_character(reattach)
                if is_staff_login_cast(raw):
                    char = raw
            if char is not None and (
                not getattr(char, "is_npc", False)
                or is_staff_login_cast(char)
            ):
                self._gateway_reattach_name = None
                char.session = self
                # Do not stamp last_input_tick when already idle -- autoidle
                # skips idle bodies anyway; leaving the stamp alone avoids
                # resetting AFK context after a hot reload.
                # Module-level ``hooks`` only -- a local import here made
                # ``hooks`` local for all of _run_inner and crashed new-character
                # surname login (bug #311 UnboundLocalError).
                if not getattr(char, "idle_mode", False):
                    hooks.stamp_input_activity(char, self.game)
                self.character = char
                # Reattach skips the name prompt -- never on connecting_sessions.
                self._promote_to_sessions()
                self.reset_gmcp()
                from engine import gmcp
                from engine import mssp
                from engine import telnet
                gmcp.offer_gmcp(self)
                mssp.offer_mssp(self)
                telnet.offer_naws(self)
                # Gateway reattach skips password prompts -- restore local
                # echo in case the client still holds ECHO-off from login.
                telnet.set_client_echo(self, True)
                from engine.copyover import MSG_AFTER
                from engine import char_identity as identity_mod
                self.send(MSG_AFTER)
                # Skip reprinting the login MOTD board on gateway reattach.
                self._copyover_resume = True
                self._notify_gateway_bound(char.key)
                hooks.after_session_attach(char, self.game)
                notice = identity_mod.legacy_surname_login_notice(char)
                if notice:
                    from engine import display_prefs as dp_mod
                    self.send(dp_mod.format_tagged_text(char, notice))
                await self._wait_veil_world_ready()
                await self.play()
                return
            # Name gone or NPC — fall through to a fresh login prompt.
            self._gateway_reattach_name = None

        # ---- LOGIN STATE ----
        # Offer GMCP + MSSP before the welcome text so Mudlet / listing
        # crawlers can DO early (before any login line).
        from engine import gmcp
        from engine import mssp
        from engine import style
        from engine import telnet
        gmcp.offer_gmcp(self)
        mssp.offer_mssp(self)
        telnet.offer_naws(self)
        # Classic MUD connect card: gothic wrought splash + creator/engine
        # credits (paint() 16-color -- no Character prefs yet).
        game = getattr(self, "game", None)
        status_text = getattr(game, "login_status_text", None) if game else None
        if game is not None:
            discord_url = getattr(game, "login_discord_url", None)
        else:
            discord_url = (style.LOGIN_DISCORD_URL or "").strip() or None
        for line in style.format_login_banner(
            status_text=status_text,
            discord_url=discord_url,
        ):
            self.send(line)
        # Soft logout may stamp a linked account for character select.
        prefer_acct = getattr(self, "_soft_logout_account", None)
        self._soft_logout_account = None
        # Staff `gm users` can see this socket as flags=login until promote.
        self._register_connecting("login")

        from engine import account_login as account_login_mod
        result = await account_login_mod.run_account_login_flow(
            self, known_account=prefer_acct, skip_password=bool(prefer_acct),
        )
        if result is None:
            return
        if not result.is_new:
            await self._attach_returning_character(
                result.character, takeover=result.takeover,
            )
        # Stamp a login snapshot for after the first ``look`` -- scheduling
        # save_async here still let the task run before room text on large DBs
        # when force_full SQLite apply monopolizes the loop (lag save-coop).
        self._defer_login_save()
        # Clients often send Core.Supports.Set during login prompts. Sync
        # Char packages when a character is attached (bootstrap hook ran
        # earlier for new chars; reconnect path attaches above).
        if self.character is not None:
            from engine import gmcp
            gmcp.on_session_attach(self.character, self.game)
        # Gateway: remember who is on this held socket for the next game boot.
        if self.character is not None:
            self._notify_gateway_bound(self.character.key)
        acct = getattr(self, "staff_account", None) or "?"
        char_key = getattr(self.character, "key", "?")
        _log_connection(f"login ok account={acct} char={char_key}")
        await self.play()

    # --- the main command loop ----------------------------------------------
    async def play(self):
        """Show the room, then loop reading commands until disconnect.

        Split out of run() so a copyover resume (copyover.py's resume(),
        which builds a Session, sets .character directly on it, and calls
        this) can reattach a connection to its character and jump straight
        here -- skipping the name/password prompt above entirely, since a
        copyover already knows who was on this socket before the reload.
        """
        from engine import telnet
        # Belt-and-suspenders after any password prompt or gateway bounce.
        telnet.set_client_echo(self, True)
        # Late Core.Supports.Set during login/account-link is common; look
        # also pushes Room.Info -- sync identity/vitals once more first.
        from engine import gmcp
        gmcp.on_session_attach(self.character, self.game)
        # Player pace log: SESSION start (copyover resume included).
        _activity_logger_call(self.character, "note", "SESSION", "start")
        try:
            dispatch(self.character, "look", self.game)   # show them the room right away
        except Exception:
            # Same guard as the command loop below -- a look/weather bug must
            # not kill the session on login (live: off-plane macro None crash).
            import traceback
            traceback.print_exc()
            self.send("Something went wrong showing the room -- you are still in.")
        await self._flush_deferred_session_attach()
        await self._flush_deferred_login_save()

        # ---- PLAYING STATE ----
        # Loop forever reading commands until the session stops being 'alive'.
        while self.alive:
            # rechargen / makecar / Carvana own read_line while their
            # numbered menu is up -- yield so those tasks get the answers.
            if session_modal_prompt_running(self):
                await asyncio.sleep(0.05)
                continue
            line = await self.read_line()
            if line is None:
                break                 # client disconnected — leave the loop
            if self.report_capture is not None:
                # Multi-line bug/suggest capture is active: EVERY line (even
                # one that looks like a command) is buffered, not dispatched,
                # until the '.' terminator -- that's the whole point, see
                # __init__'s comment on report_capture. Blank lines are kept
                # (paste spacing); do not skip "" before this gate.
                self._handle_report_capture_line(line)
                continue
            if self.help_edit is not None:
                # HEDIT modal editor is active: every line is a buffer edit
                # (/i, /d, /r, ...) or an appended body line, never a normal
                # game command -- same gate shape as report_capture above.
                # Blank Enter appends an empty body line (help page spacing).
                self._handle_help_edit_line(line)
                continue
            if line == "":
                continue              # they just hit enter — wait for the next line
            # Half-cleared attach (GM staff-form handoff, login takeover,
            # failed disconnect save): session.character set but
            # character.session None — OOC still worked via game.sessions.
            from engine.session_attach import ensure_play_session_ready
            if not ensure_play_session_ready(self):
                if getattr(self, "character", None) is None:
                    self.send(
                        "Your connection was replaced from another login."
                    )
                break
            line, bang_err = expand_bang_repeat(line, self.last_command)
            if bang_err:
                self.send(bang_err)
                continue
            # Record BEFORE dispatch so a crash still lands in history;
            # redact setpass so plaintext never hits bug reports (H2).
            entry = [history_line_for_storage(line), None]
            self.history.append(entry)
            # Player pace log: what they typed (already redacted for setpass).
            _activity_logger_call(self.character, "cmd", entry[0])
            # Classic snoop: GMs watching this character also see what they type.
            from engine import snoop
            snoop.mirror_input(self.character, line)
            try:
                dispatch(self.character, line, self.game)
            except Exception:
                # A bug in ONE command shouldn't kill the player's whole session.
                # We print the error to the server console for debugging and tell
                # the player something went wrong. During development you might
                # prefer to remove this try/except so errors surface loudly.
                # Also stash the traceback on the history entry so a later
                # 'bug'/'suggest' report already carries the repro context.
                import traceback
                entry[1] = traceback.format_exc()
                traceback.print_exc()
                self.send("Something went wrong with that command.")
            if tracks_last_command(line):
                self.last_command = line
            # drain() waits if the outgoing buffer is backed up (slow client),
            # applying "backpressure" so we don't pile up unlimited data.
            await self.writer.drain()

        # Soft logout: park body as Echo, keep TCP, re-enter character select.
        if getattr(self, "_soft_logout", False):
            self._soft_logout = False
            self.disconnect(keep_connection=True, reason="logout_select")
            return
        self.disconnect(reason="client_eof")

    def _handle_report_capture_line(self, line):
        """One line while multi-line bug/suggest capture is active (see
        __init__'s report_capture comment and commands.cmd_bug/cmd_suggest,
        which starts capture when typed with no description). A lone '.'
        ends it and files the report; 'cancel' backs out without filing
        anything (so a player who didn't mean to start this isn't stuck);
        every other line is just buffered.
        """
        if line == ".":
            from engine import reports
            from engine import bug_filing
            from commands import _report_history
            capture = self.report_capture or {}
            description = "\n".join(capture.get("lines") or []).strip()
            kind = capture.get("kind")
            # Optional generic prefix (e.g. cmd_helpsubmit stamps the
            # proposed keyword ahead of the pasted body) -- not report-kind
            # specific, any future paste-capture caller can use it.
            prefix = capture.get("prefix")
            # Read optional fields before clearing capture -- a live crash
            # (bug report 209) hit .get on None when subject_key was read
            # after ``self.report_capture = None`` below.
            subject_key = capture.get("subject_key")
            on_finish = capture.get("on_finish")
            self.report_capture = None
            if callable(on_finish):
                on_finish(self.character, self.game, description)
                return
            if not description:
                self.send("Empty report -- nothing logged.")
                return
            if prefix:
                description = f"{prefix}\n{description}"
            subject_character = None
            if subject_key:
                finder = getattr(self.game, "find_character", None)
                if callable(finder):
                    subject_character = finder(subject_key)
            if kind == reports.BUG:
                noun = "bug report"
            elif kind == reports.HELP:
                noun = "help idea"
            else:
                noun = "suggestion"
            bug_filing.record_and_confirm(
                self.character, kind, description,
                _report_history(self.character), self.game.report_dir, noun,
                subject_character=subject_character if kind == reports.BUG else None,
            )
            return
        if line.strip().lower() == "cancel":
            capture = self.report_capture or {}
            on_cancel = capture.get("on_cancel")
            self.report_capture = None
            if callable(on_cancel):
                on_cancel(self.character, self.game)
                return
            self.send("Cancelled -- nothing logged.")
            return
        self.report_capture["lines"].append(line)

    def _handle_help_edit_line(self, line):
        """One line while the HEDIT modal editor is active (see __init__'s
        help_edit comment and engine.verbs.basic.cmd_hedit, which starts
        it). A line starting with '/' is an editor command; anything else
        is appended to the body buffer as-is -- same "plain text just
        appends" UX as report_capture, plus modal line-editing commands
        (docs/plans/helpfile_editing_system.md).
        """
        from engine import help_db, style

        state = self.help_edit
        stripped = line.strip()
        if not stripped.startswith("/"):
            state["body"].append(line)
            self.send(f"[{len(state['body'])}] {line}")
            return

        parts = stripped[1:].split(maxsplit=1)
        cmd = (parts[0].lower() if parts else "")
        rest = parts[1] if len(parts) > 1 else ""

        if cmd == "cancel":
            self.help_edit = None
            self.send(f"Cancelled editing '{state['keyword']}' -- nothing saved.")
            return

        if cmd == "list":
            lines = [f"Editing '{state['keyword']}'  "
                     f"(category={state['category'] or '-'}  "
                     f"aliases={', '.join(state['aliases']) or '-'}  "
                     f"gm_only={state['gm_only']}  is_ic={state['is_ic']})"]
            if state["syntax"]:
                lines.append("Syntax:")
                for i, s in enumerate(state["syntax"], start=1):
                    lines.append(f"  s{i}: {s}")
            lines.append("Body:")
            if not state["body"]:
                lines.append("  (empty)")
            for i, b in enumerate(state["body"], start=1):
                lines.append(f"  {i}: {b}")
            self.send("\r\n".join(lines))
            return

        if cmd == "i":
            # One space after the line number is the separator; everything
            # after that is body text as-is (leading indent is content).
            # str.split() would collapse those spaces and flatten the page.
            match = re.match(r"^(\d+) (.*)$", rest)
            if not match:
                self.send("Usage: /i <line> <text>")
                return
            pos = int(match.group(1))
            text = match.group(2)
            if pos < 1 or pos > len(state["body"]) + 1:
                self.send(f"Line must be between 1 and {len(state['body']) + 1}.")
                return
            state["body"].insert(pos - 1, text)
            self.send(f"Inserted at line {pos}.")
            return

        if cmd == "d":
            if not rest.strip().isdigit():
                self.send("Usage: /d <line>")
                return
            pos = int(rest.strip())
            if pos < 1 or pos > len(state["body"]):
                self.send(f"No line {pos} -- body has {len(state['body'])} lines.")
                return
            removed = state["body"].pop(pos - 1)
            self.send(f"Deleted line {pos}: {removed}")
            return

        if cmd == "clear":
            count = len(state["body"])
            state["body"] = []
            if count:
                self.send(f"Cleared {count} body line(s).")
            else:
                self.send("Body already empty.")
            return

        if cmd == "r":
            sub = rest.split(maxsplit=1)
            if len(sub) != 2:
                self.send("Usage: /r <pattern> <replacement>")
                return
            pattern, replacement = sub
            try:
                joined = re.sub(pattern, replacement, "\n".join(state["body"]))
            except re.error as exc:
                self.send(f"Bad regex: {exc}")
                return
            state["body"] = joined.split("\n")
            self.send("Replaced.")
            return

        if cmd == "syntax":
            if not rest:
                self.send("Usage: /syntax <text>")
                return
            state["syntax"].append(rest)
            self.send(f"Syntax line {len(state['syntax'])} added.")
            return

        if cmd == "category":
            state["category"] = rest.strip()
            self.send(f"Category set to '{state['category']}'.")
            return

        if cmd == "alias":
            alias = rest.strip().lower()
            if not alias:
                self.send("Usage: /alias <name>")
                return
            if alias not in state["aliases"]:
                state["aliases"].append(alias)
            self.send(f"Aliases: {', '.join(state['aliases'])}")
            return

        if cmd == "gm":
            state["gm_only"] = not state["gm_only"]
            self.send(f"gm_only is now {state['gm_only']}.")
            return

        if cmd == "ic":
            state["is_ic"] = not state["is_ic"]
            self.send(f"is_ic is now {state['is_ic']}.")
            return

        if cmd == "preview":
            from engine import display_prefs
            body_lines = list(state["body"])
            if state["syntax"]:
                body_lines = [f"Syntax: {s}" for s in state["syntax"]] + [""] + body_lines
            # Same width pref as live help -- staff preview must match what
            # players with config width N will see after /save.
            framed = style.format_tome(
                state["keyword"], body_lines,
                width=display_prefs.sheet_width(self.character),
                screenreader=bool(getattr(self.character, "screenreader", False)),
            )
            self.send("\r\n".join(framed))
            return

        if cmd == "save":
            if not state["body"]:
                self.send("Nothing to save -- body is empty. /cancel to abort.")
                return
            entry = help_db.save_entry(
                self.game.db,
                keyword=state["keyword"],
                category=state["category"],
                body_text="\n".join(state["body"]),
                syntax_block="\n".join(state["syntax"]),
                aliases=state["aliases"],
                gm_only=state["gm_only"],
                is_ic=state["is_ic"],
                author=getattr(self.character, "key", "?"),
            )
            self.help_edit = None
            self.send(
                f"Saved help page '{entry['primary_keyword']}' "
                f"({len(state['body'])} body lines). It now overrides any "
                "static page of the same name."
            )
            return

        self.send(
            "Unknown editor command. Try: /list /i /d /clear /r /syntax "
            "/category /alias /gm /ic /preview /save /cancel"
        )

    def _defer_login_save(self):
        """Queue a cooperative login snapshot after the first room look."""
        self._deferred_login_save = True

    async def _flush_deferred_login_save(self):
        """Run the deferred login snapshot without wedging menu→play."""
        if not getattr(self, "_deferred_login_save", False):
            return
        self._deferred_login_save = False
        import asyncio
        await asyncio.sleep(0)
        try:
            await self.writer.drain()
        except Exception:
            pass
        try:
            schedule = getattr(self.game, "schedule_save_async", None)
            if callable(schedule):
                schedule(reason="login")
            else:
                self.game.save()
        except Exception as exc:
            print(
                f"[connection] post-login save failed ({exc!r}) -- "
                "entering play anyway",
                flush=True,
            )
            try:
                self.send(
                    "(World save hiccup on login -- you are still in. "
                    "Staff have been notified via the server log.)"
                )
            except Exception:
                pass

    async def _flush_deferred_session_attach(self):
        """Run heavy login attach hooks after the first room look."""
        if getattr(self, "_deferred_attach_done", False):
            return
        if self.character is None:
            return
        self._deferred_attach_done = True
        import asyncio
        await asyncio.sleep(0)
        from engine import login_briefing as login_briefing_mod

        login_briefing_mod.begin(self, self.character)
        try:
            hooks.after_session_attach_deferred(self.character, self.game)
        except Exception as exc:
            print(
                f"[connection] deferred attach failed ({exc!r}) -- "
                "continuing play",
                flush=True,
            )
            import traceback
            traceback.print_exc()
        finally:
            login_briefing_mod.end(self)

    async def _attach_returning_character(self, char, *, takeover=False):
        """Wire a returning body after account menu pick."""
        from engine import char_identity as identity_mod
        from engine.command_support import (
            _presence_face,
            is_staff_stealth_presence,
        )
        from engine import gm_notify

        given_name = identity_mod.character_given_name(char) or char.key
        if apply_login_name_case(char, given_name, self.game):
            self.send(f"(Name casing fixed -- you are {char.key}.)")
        char.session = self
        char.offline_gains_this_stretch = 0
        char.idle_mode = False
        hooks.stamp_input_activity(char, self.game)
        self.character = char
        self._promote_to_sessions()
        face = _presence_face(char)
        if takeover:
            if (
                char.location is not None
                and not is_staff_stealth_presence(char)
            ):
                char.location.broadcast(
                    f"{face}'s attention snaps back into focus.",
                    exclude=char,
                )
            self.send(
                f"\r\nWelcome back, {face}! "
                "(Previous connection closed.)"
            )
        else:
            if (
                char.location is not None
                and not is_staff_stealth_presence(char)
            ):
                char.location.broadcast(
                    f"{face}'s echo stirs and comes back to life.",
                    exclude=char,
                )
            self.send(f"\r\nWelcome back, {face}!")
        notice = identity_mod.legacy_surname_login_notice(char)
        if notice:
            from engine import display_prefs as dp_mod
            self.send(dp_mod.format_tagged_text(char, notice))
        # Yield so welcome lines reach the client before attach hooks run.
        import asyncio
        await asyncio.sleep(0)
        try:
            await self.writer.drain()
        except Exception:
            pass
        hooks.after_session_attach(char, self.game)
        gm_notify.ping_gms(
            self.game,
            f"{face} has connected{{from}}.",
            exclude=char,
            peer_session=self,
        )

    def _take_over_session(self, character):
        """Kick the live Session on ``character`` so this login can attach.

        Used when the owner proves the password while another client still
        holds the seat (dropped TCP that never finished disconnect, second
        login from another window, etc.). Does **not** run full
        ``disconnect()`` Echo semantics -- the body stays in play and just
        changes who holds the wires. The old Session's ``play()`` loop will
        unwind with ``character`` already cleared, so it will not broadcast
        an Echo leave or double-save.
        """
        old = getattr(character, "session", None)
        if old is not None and old is not self:
            _log_connection(
                f"session takeover char={getattr(character, 'key', '?')}"
            )
            try:
                old.send(
                    "Your connection has been taken over from another login."
                )
            except Exception:
                pass
            # Stop the old play loop; clear the Character link before close so
            # a later old.disconnect() is a no-op for Echo broadcast.
            from engine import hooks
            hooks.on_session_disconnect(character, self.game, to_echo=False)
        from engine.session_attach import detach_stale_sessions

        detach_stale_sessions(character, self.game, self)
        if old is not None and old is not self:
            writer = getattr(old, "writer", None)
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass

    def disconnect(self, *, keep_connection=False, reason=None):
        # Tidy up when a player leaves. THE INVARIANT (systems doc section 4-E):
        # logout is NOT deletion. The character stays in the world as an Echo —
        # an invulnerable, session-less figure — so we detach the session but
        # deliberately do NOT remove the character from its room.
        #
        # keep_connection=True (soft ``logout``): Echo conversion + save, but
        # leave the TCP / gateway socket up and set ``_soft_relogin`` so
        # Session.run() re-enters the name/account character-select flow.
        if not keep_connection:
            self.alive = False
        # Capture name before we clear session / leave sessions list,
        # so the staff ping still has a readable label (mid-chargen included).
        # Peer IP is filled per recipient at ping time (head_gm only).
        disconnect_name = None
        leaving = self.character
        if leaving is not None:
            # Public face for staff ping -- never gmspirit:/husk: keys.
            from engine.command_support import _presence_face
            disconnect_name = _presence_face(leaving)
        if reason is None:
            reason = "logout_select" if keep_connection else "disconnect"
        char_key = (
            getattr(leaving, "key", None) or disconnect_name or "?"
        )
        _log_connection(f"disconnect char={char_key} reason={reason}")
        if leaving is not None:
            _activity_logger_call(leaving, "note", "SESSION", f"end reason={reason}")
        if self.character:
            # Drop any snoop THIS character was running (they're leaving);
            # keep snoopers aimed *at* them -- an Echo is still watchable.
            from engine import snoop
            snoop.stop(self.character, quiet=True)
            from engine import hooks
            hooks.on_session_disconnect(self.character, self.game)
            # Drop GM staff spirit on logout: body is already a Cadence Echo.
            # Permanent account spirits (feature G) are parked in place;
            # ephemeral leftovers are despawned. KEEP gm_away + gm_staff_form
            # so reconnect / copyover can restore `gm on`.
            if self.character is not None and (
                getattr(self.character, "gm_mode", False)
                or getattr(self.character, "gm_spirit", False)
            ):
                from engine import hooks
                body = hooks.resolve_gm_body(self.character, self.game)
                if body is None:
                    body_key = getattr(self.character, "gm_body_key", None)
                    if body_key:
                        for obj in getattr(self.game, "characters", ()) or ():
                            if getattr(obj, "key", None) == body_key:
                                body = obj
                                break
                if body is not None:
                    # Intent survives quit -- body stays true-invis Echo.
                    body.gm_away = True
                    body.gm_staff_form = True
                    body.gm_spirit_key = getattr(self.character, "key", None)
                    # Remember watch-room so reconnect restores there
                    # (not over wherever Cadence walked the Echo).
                    watch = getattr(self.character, "location", None)
                    if watch is not None and getattr(watch, "key", None):
                        body.gm_spirit_room_key = watch.key
                    # Staff ping should name the real login, not gmspirit:Key.
                    from engine.command_support import _presence_face
                    disconnect_name = _presence_face(body)
                    leaving = body
                spirit = self.character
                permanent = bool(
                    getattr(spirit, "gm_spirit_permanent", False)
                )
                if not permanent:
                    # Engine-pure account ownership check (no supers import).
                    try:
                        from engine.accounts import ensure_accounts_dict
                        low = (spirit.key or "").lower()
                        for acct in ensure_accounts_dict(self.game).values():
                            if acct.gm_rank not in ("gm", "head_gm"):
                                continue
                            want = (
                                acct.gm_spirit_key
                                or f"gmspirit:{acct.name}"
                            )
                            if want.lower() == low:
                                permanent = True
                                break
                    except Exception:
                        permanent = False
                if permanent:
                    # Fold staff spirit out of the live world; body stays Echo.
                    spirit.gm_mode = False
                    spirit.gm_spirit = True
                    spirit.gm_spirit_permanent = True
                    spirit.session = None
                    self.character = None
                    break_follows(spirit)
                    hooks.park_gm_spirit_on_disconnect(spirit, self.game)
                else:
                    # Legacy ephemeral leftover -- despawn.
                    spirit.gm_mode = False
                    spirit.gm_spirit = False
                    spirit.gm_mode_body = None
                    spirit.gm_body_key = None
                    spirit_room = getattr(spirit, "location", None)
                    if spirit_room is not None and spirit in getattr(
                        spirit_room, "contents", []
                    ):
                        spirit_room.remove(spirit)
                    spirit.session = None
                    self.character = None
                    break_follows(spirit)
            elif self.character is not None:
                echo_body = self.character
                echo_body.session = None    # the character is now an Echo
                break_follows(echo_body)
                from engine import hooks
                hooks.on_echo_begin(echo_body, self.game)
                if echo_body.location:
                    # session is already None, so the Echo itself can't receive this.
                    # Public face -- never raw storage keys in room traffic.
                    from engine.command_support import _presence_face
                    face = _presence_face(echo_body)
                    echo_body.location.broadcast(
                        f"{face} goes still, leaving only an echo."
                    )
                if keep_connection:
                    # Soft logout must clear so login can attach a new body.
                    self.character = None
        if self in self.game.sessions:
            self.game.sessions.remove(self)
        self._leave_connecting()
        # Staff ping after dropping from sessions so the leaver is not in
        # the recipient walk; exclude= still guards FakeSession edge cases.
        if disconnect_name:
            from engine import gm_notify
            verb = (
                "has logged out to character select{from}."
                if keep_connection
                else "has disconnected{from}."
            )
            gm_notify.ping_gms(
                self.game,
                f"{disconnect_name} {verb}",
                exclude=leaving,
                peer_session=self,
            )
        # Persist Echo position — but never let a save failure leave the
        # character.session pointer half-cleared (same class of bug as the
        # post-login save guard above: outbound combat with no command loop).
        try:
            schedule = getattr(self.game, "schedule_save_async", None)
            if callable(schedule):
                schedule(reason="disconnect")
            else:
                from engine import persistence

                persistence.save_world_before_process_exit(
                    self.game.db, self.game, reason="disconnect",
                )
        except Exception as exc:
            print(
                f"[connection] disconnect save failed ({exc!r}) -- "
                "session already detached",
                flush=True,
            )
        if keep_connection:
            # Soft logout: clear gateway bind, stay on TCP, re-enter login.
            self._notify_gateway_unbound()
            self.history.clear()
            self.last_command = None
            self.report_capture = None
            self.help_edit = None
            self.staff_account = None
            self.reset_gmcp()
            self._soft_relogin = True
            self.alive = True
            return
        # Gateway: drop the public TCP on intentional quit / client EOF path.
        # (Game-process restart cancels play() without calling disconnect.)
        self._kick_gateway_client()
        try:
            self.writer.close()
        except Exception:
            pass                      # already closing/closed — nothing to do

    def force_clear_zombie_attach(self):
        """Last-resort clear when disconnect() itself fails mid-crash cleanup.

        Used by ``gateway_client._abandon_crashed_session`` so a broken save
        cannot leave ``character.session`` pointing at a dead Session that
        still receives combat broadcasts.
        """
        char = self.character
        if char is not None and getattr(char, "session", None) is self:
            if (
                getattr(char, "gm_mode", False)
                or getattr(char, "gm_spirit", False)
            ):
                from engine import hooks
                body = hooks.resolve_gm_body(char, self.game)
                if body is not None:
                    body.gm_away = True
                    body.gm_staff_form = True
                    body.gm_spirit_key = getattr(char, "key", None)
                    watch = getattr(char, "location", None)
                    if watch is not None and getattr(watch, "key", None):
                        body.gm_spirit_room_key = watch.key
                char.session = None
                hooks.park_gm_spirit_on_disconnect(char, self.game)
            else:
                char.session = None
        self.character = None
        self.alive = False
        sessions = getattr(self.game, "sessions", None)
        if sessions is not None and self in sessions:
            try:
                sessions.remove(self)
            except ValueError:
                pass
        self._leave_connecting()
