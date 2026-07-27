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
import re
from engine import auth
from engine import hooks
from world import Character, break_follows
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
        self.alive = True         # flips to False on quit/disconnect; ends the loop
        # Gateway IPC: fixed session id + optional bridge for bound/kick CTRL.
        # None when speaking telnet directly (RIFTFORGE_GATEWAY=0).
        self.gateway_session_id = gateway_session_id
        self.gateway_bridge = None
        # Set by gateway_client on reattach: skip login and jump to play().
        self._gateway_reattach_name = None
        # Ring buffer of recent play-loop lines for bug/suggest reports.
        # Each entry is [raw_line, traceback_or_None] -- a mutable list so the
        # except block below can fill in a traceback after a failed dispatch.
        # collections.deque(maxlen=N) auto-drops the oldest entry when full.
        self.history = collections.deque(maxlen=RECENT_HISTORY_SIZE)
        # Multi-line bug/suggest capture (a live report: pasting a multi-
        # line message into 'suggest' split across several 'Unknown
        # command' lines instead of landing as one report -- a raw telnet
        # paste arrives as several separate lines on the wire, indistin-
        # guishable from several separate Enter presses, so line 1 alone
        # got treated as the whole report). None when not capturing;
        # otherwise {"kind": reports.BUG|SUGGEST, "lines": [...]} -- see
        # commands.cmd_bug/cmd_suggest (which starts it) and
        # play()/_finish_report_capture below (which ends it).
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
        # Per-session wire counters for GM `host` (bytes on the socket,
        # including telnet/GMCP framing). Stdlib-only ops pulse -- not
        # host-wide NIC stats.
        self.bytes_in = 0
        self.bytes_out = 0
        # Staff-only login phase for `gm users` (not public `who`):
        # "login" = name/password prompts; "creating" = mid-chargen;
        # None = fully in play (on game.sessions) or not yet registered.
        self.login_stage = None

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

    def _leave_connecting(self):
        """Drop from connecting_sessions (play promote or disconnect)."""
        bucket = getattr(self.game, "connecting_sessions", None)
        if bucket is not None and self in bucket:
            bucket.remove(self)

    def _promote_to_sessions(self):
        """Leave connecting bucket and join game.sessions for who/play."""
        self._leave_connecting()
        self.login_stage = None
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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(bridge.notify_bound(sid, bind))

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
            self._write(message)
            # Fan out to GM snoopers after the real client has the line.
            if self.character is not None:
                from engine import snoop
                snoop.mirror_output(self.character, message)

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
            line = telnet.text_to_command_line(line_bytes)
            self._pending_lines.append(line)

    # --- input -------------------------------------------------------------
    async def read_line(self):
        """Await one command line, processing interleaved telnet/GMCP.

        Uses reader.read() (not readline) so a client can send IAC SB GMCP
        without a trailing newline and still be heard -- readline would block
        forever waiting for \\n on a pure-GMCP frame.
        """
        while True:
            if self._pending_lines:
                return self._pending_lines.popleft()
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
                    return line
                return None
            self._ingest_bytes(data)

    # --- the session lifecycle --------------------------------------------
    async def run(self):
        # Always drop from connecting_sessions on exit (mid-login hangup,
        # chargen abort, or play() end). Promote-to-play already removes us;
        # this is the safety net for early ``return`` paths that skip
        # disconnect().
        try:
            await self._run_inner()
        finally:
            self._leave_connecting()

    async def _run_inner(self):
        # Gateway reattach: game restarted while this telnet client stayed
        # held -- skip name/password and resume play() like copyover.
        # Preserve idle_mode from the SQLite blob (same as classic copyover
        # resume): Docker/live "copyover" is this path, and clearing the flag
        # here used to snap AFK Echo-watchers back to present mid-reload.
        # Fresh password login still clears idle_mode below -- intentional.
        reattach = getattr(self, "_gateway_reattach_name", None)
        if reattach:
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
                ):
                    char = hooks.try_restore_folded_login(self.game, reattach)
            if char is not None and not getattr(char, "is_npc", False):
                self._gateway_reattach_name = None
                char.session = self
                # Do not stamp last_input_tick when already idle -- autoidle
                # skips idle bodies anyway; leaving the stamp alone avoids
                # resetting AFK context after a hot reload.
                if not getattr(char, "idle_mode", False):
                    char.last_input_tick = getattr(
                        self.game, "game_time_ticks", 0
                    ) or 0
                self.character = char
                # Reattach skips the name prompt -- never on connecting_sessions.
                self._promote_to_sessions()
                self.reset_gmcp()
                from engine import gmcp
                from engine import mssp
                gmcp.offer_gmcp(self)
                mssp.offer_mssp(self)
                from engine.copyover import MSG_AFTER
                from engine import char_identity as identity_mod
                self.send(MSG_AFTER)
                self._notify_gateway_bound(char.key)
                hooks.after_session_attach(char, self.game)
                notice = identity_mod.legacy_surname_login_notice(char)
                if notice:
                    self.send(notice)
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
        gmcp.offer_gmcp(self)
        mssp.offer_mssp(self)
        # Classic MUD connect card: gothic wrought splash + creator/engine
        # credits (paint() 16-color -- no Character prefs yet).
        for line in style.format_login_banner():
            self.send(line)
        self.send("By what name are you known?")
        self.send("(Or type 'account' to log into an account.)")
        # Staff `gm users` can see this socket as flags=login until promote.
        self._register_connecting("login")

        # Keep asking until we get a usable name + password. Ways around the
        # loop: a blank/invalid name, an NPC name, a wrong password, a live
        # session without a password to prove takeover, or success (break).
        # takeover is True when we kicked another live Session for this name.
        takeover = False
        # When True, password already verified via account login (feature C).
        account_login_ok = False
        while True:
            raw_name = await self.read_line()
            if raw_name is None:
                return                # disconnected before finishing login
            # Listing crawlers that skip telnet MSSP may type "mssp" or
            # "mssp-request" at the name prompt -- reply with the text
            # status block and hang up (never create a Character / GM ping).
            if mssp.is_text_probe(raw_name):
                mssp.reply_text_probe(self)
                self.close()
                return
            # Feature C: keyword ``account`` → account name/pass → pick char.
            from engine import account_login as account_login_mod
            if account_login_mod.is_account_login_keyword(raw_name):
                picked = await account_login_mod.login_via_account(self)
                if picked is None:
                    return  # disconnected mid-account login
                if picked is False:
                    continue  # back to name prompt (already re-prompted)
                # Account auth covers owned characters -- skip char password.
                existing = picked
                given_name = (
                    getattr(picked, "given_name", None)
                    or getattr(picked, "key", "")
                    or ""
                )
                from engine import char_identity as identity_mod
                surname = identity_mod.character_surname(picked)
                account_login_ok = True
                unique_short_ok = True
                # Live seat / gmspirit takeover (same as direct login).
                live_holder = (
                    existing if existing.session is not None else None
                )
                if live_holder is None:
                    sk = getattr(existing, "gm_spirit_key", None)
                    if not sk and getattr(existing, "gm_staff_form", False):
                        from engine.command_support import (
                            strip_ephemeral_storage_prefix,
                        )
                        sk = (
                            "gmspirit:"
                            + strip_ephemeral_storage_prefix(existing.key)
                        )
                    if sk:
                        spirit = self.game.find_character(sk)
                        if (
                            spirit is not None
                            and getattr(spirit, "session", None) is not None
                        ):
                            live_holder = spirit
                if live_holder is not None:
                    self._take_over_session(live_holder)
                    takeover = True
                break
            # Strip client window tags (P1/P4…) then require letters-only
            # (bug_reports.log #28). Digits used to pass isalnum() and baked
            # session tags into Character.key forever.
            name, name_err, name_stripped = normalize_login_name(raw_name)
            if name_err:
                self.send(name_err + " Try again:")
                continue
            if name_stripped:
                # Tell the player what will actually be stored / looked up.
                self.send(f"(Client prefix dropped -- logging in as {name}.)")

            # Staff banlist (engine/banlist.py) -- name and/or client IP.
            # Checked before password so a banned account never attaches.
            from engine import banlist as banlist_mod
            from engine import gm_notify as gm_notify_mod
            from engine import char_identity as identity_mod
            peer = gm_notify_mod.peer_host(self)
            if banlist_mod.is_banned(self.game, name=name, ip=peer):
                self.send(
                    "You are banned from this game. "
                    "Contact staff if you believe this is an error."
                )
                self.close()
                return

            # First name may belong to zero, one, or many player bodies.
            # Unique first name: next line may be password OR surname
            # (Dean→pass, or Dean→Winchester→pass). Shared names still get
            # an explicit surname prompt. Unused non-empty surname → new char.
            # Immersion cast first names are reserved for NEW bodies only.
            given_name = name
            surname = ""
            existing = identity_mod.find_unique_given_login(
                self.game, given_name
            )
            unique_short_ok = False
            if existing is not None:
                # ---- Unique given name: password-or-surname second line ----
                if getattr(existing, "is_npc", False):
                    self.send(
                        "That name belongs to a townsfolk, not a player. "
                        "Choose another:"
                    )
                    continue
                if not existing.password_hash:
                    self.send(
                        "That character has no password set. "
                        "Ask a head GM to reset it (gm setpass), then log in."
                    )
                    continue
                wait = _login_backoff_seconds(name, peer)
                if wait > 0:
                    self.send(
                        f"Too many failed logins -- wait {int(wait)}s "
                        f"and try again."
                    )
                    await asyncio.sleep(wait)
                # Prompt says Password so classic name→pass clients are happy;
                # typing the body's surname here still works (then Password).
                self.send("Password:")
                second = await self.read_line()
                if second is None:
                    return
                second = strip_client_session_tags(second or "")
                kind, cleaned = identity_mod.interpret_unique_given_second_line(
                    existing,
                    second,
                    password_hash=existing.password_hash,
                    verify_fn=auth.verify_password,
                )
                if kind == "ok":
                    unique_short_ok = True
                    surname = identity_mod.character_surname(existing)
                elif kind == "surname_match":
                    surname = cleaned or ""
                    # Fall through to the shared Password: prompt below.
                elif kind == "new_surname":
                    # Same first name, different family → new character.
                    surname = cleaned or ""
                    existing = None
                else:
                    _note_login_failure(name, peer)
                    self.send(
                        "Incorrect password. By what name are you known?"
                    )
                    continue
            else:
                # Zero or several bodies share this first name -- ask surname.
                self.send("What is your surname? (enter for none)")
                raw_sur = await self.read_line()
                if raw_sur is None:
                    return
                raw_sur = strip_client_session_tags(raw_sur or "").strip()
                if not raw_sur:
                    surname = ""
                else:
                    surname, sur_err = identity_mod.normalize_surname(raw_sur)
                    if sur_err:
                        self.send(sur_err + " Try again from the start:")
                        self.send("By what name are you known?")
                        continue

                existing = identity_mod.find_player_by_given_surname(
                    self.game, given_name, surname
                )
                # Exact storage-key login still works for mash keys when the
                # body's surname matches what they typed (including empty).
                if existing is None:
                    finder = getattr(self.game, "find_login_character", None)
                    if callable(finder):
                        key_hit = finder(given_name)
                        if (
                            key_hit is not None
                            and identity_mod.character_surname(key_hit).lower()
                            == surname.lower()
                        ):
                            existing = key_hit
            # Town NPCs / hostiles share the character roster but are never
            # player logins -- letter-only keys (Marta, Bobby, …) used to
            # attach passwordless as if they were Echoes.
            if existing is not None and getattr(existing, "is_npc", False):
                self.send(
                    "That name belongs to a townsfolk, not a player. "
                    "Choose another:"
                )
                continue
            if not existing:
                # find_login_character / find_player skip NPCs -- still refuse
                # letter-only town keys (Marta, Nix, …) so they never fall
                # through to "need a surname" / new-character chargen.
                npc_hit = None
                needle = given_name.lower()
                for obj in list(getattr(self.game, "characters", None) or []):
                    if not getattr(obj, "is_npc", False):
                        continue
                    if (getattr(obj, "key", None) or "").lower() == needle:
                        npc_hit = obj
                        break
                if npc_hit is not None:
                    body_sur = identity_mod.character_surname(npc_hit)
                    if body_sur.lower() == surname.lower():
                        self.send(
                            "That name belongs to a townsfolk, not a player. "
                            "Choose another:"
                        )
                        continue
            if not existing:
                # Hard gm fold: name may live only in character_vault.
                existing = hooks.try_restore_folded_login(
                    self.game, given_name
                )
                if existing is not None:
                    # Folded restore is key-based; require surname match
                    # when the body already has one stamped.
                    body_sur = identity_mod.character_surname(existing)
                    if body_sur.lower() != surname.lower():
                        existing = None
            if not existing:
                if hooks.is_reserved_login_name(given_name):
                    self.send(
                        "That name is reserved for the immersion cast. "
                        "Choose another:"
                    )
                    continue
                if not surname:
                    self.send(
                        "New characters need a surname (2-16 letters). "
                        "By what name are you known?"
                    )
                    continue
                # Fresh given+surname -- new-character path below.
                break

            # ---- Returning character: password, then optional takeover ----
            # Every returning body needs a password hash. Blank-hash Echoes
            # are not publicly reclaimable (pen-test H1) -- head GM resets
            # via gm setpass. A correct password kicks a live Session so
            # the owner can reclaim (linkdead / second login).
            if not existing.password_hash:
                self.send(
                    "That character has no password set. "
                    "Ask a head GM to reset it (gm setpass), then log in."
                )
                continue
            # Live seat may be on the body OR on a gmspirit: while `gm on`
            # (body.session is None in that case).
            live_holder = existing if existing.session is not None else None
            if live_holder is None:
                sk = getattr(existing, "gm_spirit_key", None)
                if not sk and getattr(existing, "gm_staff_form", False):
                    from engine.command_support import (
                        strip_ephemeral_storage_prefix,
                    )
                    sk = (
                        "gmspirit:"
                        + strip_ephemeral_storage_prefix(existing.key)
                    )
                if sk:
                    spirit = self.game.find_character(sk)
                    if (
                        spirit is not None
                        and getattr(spirit, "session", None) is not None
                    ):
                        live_holder = spirit
            live = live_holder is not None
            if not unique_short_ok:
                wait = _login_backoff_seconds(name, peer)
                if wait > 0:
                    self.send(
                        f"Too many failed logins -- wait {int(wait)}s "
                        f"and try again."
                    )
                    await asyncio.sleep(wait)
                self.send("Password:")
                password = await self.read_line()
                if password is None:
                    return
                # Same client may tag password lines; strip Pn only.
                password = strip_client_session_tags(password or "")
                if not auth.verify_password(password, existing.password_hash):
                    _note_login_failure(name, peer)
                    self.send(
                        "Incorrect password. By what name are you known?"
                    )
                    continue  # back to square one; don't leak WHICH part was wrong
            _clear_login_failures(name, peer)
            if live:
                self._take_over_session(live_holder)
                takeover = True
            break                     # verified reconnect / takeover

        if existing:
            # ---- RECONNECT / TAKEOVER: reattach the wires ----
            # Echo wake (section 4-E) when the body had no Session; takeover
            # when we just kicked a live Session -- character never left play.
            char = existing
            # Capitalize a forgotten lowercase storage key when the login
            # face matches the key letters (legacy single-name bodies).
            if apply_login_name_case(char, given_name, self.game):
                self.send(f"(Name casing fixed -- you are {char.key}.)")
            # Keep given_name casing aligned with what they typed.
            if (
                getattr(char, "given_name", None)
                and char.given_name.lower() == given_name.lower()
                and char.given_name != given_name
            ):
                char.given_name = given_name
            elif not getattr(char, "given_name", None):
                char.given_name = given_name
            char.session = self
            # Offline regimen: stretch (growth-only) resets on reconnect.
            # Pending Tier break from banked Echo growth is applied in
            # hooks.after_session_attach (game side) so engine/ never
            # imports supers (two-repo purity).
            char.offline_gains_this_stretch = 0
            # Never wake into idlemode -- a fresh password login / takeover
            # is always "present". Gateway reattach and classic copyover
            # resume keep the persisted flag (see run() reattach above and
            # engine/copyover.py resume()).
            char.idle_mode = False
            # Fresh login starts the auto-idle AFK clock now.
            char.last_input_tick = getattr(self.game, "game_time_ticks", 0) or 0
            self.character = char
            self._promote_to_sessions()
            # Public face for room / welcome / staff ping -- never
            # gmspirit:Key / husk:Mantle storage keys (roleplay names only).
            from engine.command_support import (
                _presence_face,
                is_staff_stealth_presence,
            )
            face = _presence_face(char)
            if takeover:
                # Still embodied -- no Echo stir broadcast.
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
                self.send(notice)
            # Mail notify + pending offline Tier break -- after Session is
            # wired (D64 / Echo softcap).
            hooks.after_session_attach(char, self.game)
            # Dark-green staff ping: returning player woke their Echo /
            # reclaimed a live seat. ``{from}`` is filled per recipient --
            # only head_gm sees the real client IP (junior staff omit it).
            from engine import gm_notify
            gm_notify.ping_gms(
                self.game,
                f"{face} has connected{{from}}.",
                exclude=char,
                peer_session=self,
            )
        else:
            # ---- NEW CHARACTER: password, then chargen, then place --------
            # Chargen (appearance + pronoun + Human Background) runs BEFORE
            # move_to / broadcast / save so a disconnect mid-flow leaves no
            # half-made Echo in the world (section 7 character creation).
            from engine import char_identity as identity_mod
            min_len = auth.MIN_PASSWORD_LEN
            self.send(f"Choose a password (at least {min_len} characters):")
            while True:
                password = await self.read_line()
                if password is None:
                    return
                # Strip Pn tags so a web client does not bake them into the hash.
                password = strip_client_session_tags(password or "")
                # New characters are mortals -- length only (no GM complexity).
                policy_err = auth.password_policy_error(password, for_gm=False)
                if policy_err is None:
                    break
                self.send(f"{policy_err} Try again:")

            storage_key = identity_mod.allocate_storage_key(
                self.game, given_name, surname
            )
            char = Character(storage_key)
            identity_mod.stamp_new_identity(
                char, self.game, given_name, surname
            )
            char.password_hash = auth.hash_password(password)
            char.session = self       # so chargen prompts can reach the client
            self.character = char
            # Staff `gm users` shows flags=creating while prompts continue;
            # still NOT on game.sessions (no who / room broadcasts yet).
            self._set_creating()
            # Staff ping as soon as the name+password stick -- before
            # chargen questions, so GMs see "is making a character…"
            # while the player is still answering prompts. IP clause is
            # head_gm-only (see gm_notify.format_from).
            from engine import gm_notify
            legal = identity_mod.legal_public_name(char, force_surname=True)
            gm_notify.ping_gms(
                self.game,
                f"{legal} has connected{{from}} "
                "and is making a character...",
                exclude=char,
                peer_session=self,
            )
            if not await hooks.run_chargen(self, char):
                # Client hung up mid-chargen -- do not place or persist.
                return
            # An Awakened Nature (Vampire/Angel/Demon/Leviathan/Elemental)
            # sets a one-shot chargen_start_room_key so the character
            # materializes in its homezone instead of the ordinary
            # start_room -- fall back to start_room if that key doesn't
            # resolve to a real room (bad/missing JSON should never strand
            # a new character with nowhere to stand).
            start_key = getattr(char, "chargen_start_room_key", None)
            if start_key and start_key in self.game.rooms:
                start_room = self.game.rooms[start_key]
            else:
                start_room = self.game.start_room
            char.chargen_start_room_key = None  # consumed -- one-shot only
            char.move_to(start_room)
            self._promote_to_sessions()  # register for 'who' and broadcasts
            start_room.broadcast(f"{legal} materializes.", exclude=char)
            self.send(
                f"\r\nWelcome, {legal}! Type 'help newbie' to get started "
                f"(or 'help' for the topic list)."
            )
            # Post-placement game content (path home stamp + tutorial, ...).
            # Must run AFTER move_to -- see set_after_new_character's
            # docstring on engine/hooks.py. Path home stamping lives in
            # the SUPERS hook (bootstrap), not a supers import here
            # (two-repo purity).
            hooks.after_new_character(char, self.game)
            # Same attach hook as reconnect (mail notify, …).
            hooks.after_session_attach(char, self.game)

        # Persist now -- but never let a save bug kill the session before
        # play(). Live hit: gear_bag rows + old CHECK (room|character only)
        # raised IntegrityError here; Mudlet stayed connected with no
        # command loop while the world kept ticking ("commands don't parse").
        try:
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
        # Feature B: unlinked playable body → create / link / skip offer.
        if self.character is not None:
            from engine import account_login as account_login_mod
            linked_ok = await account_login_mod.offer_account_link(
                self, self.character
            )
            if not linked_ok:
                return
        # Gateway: remember who is on this held socket for the next game boot.
        if self.character is not None:
            self._notify_gateway_bound(self.character.key)
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
        try:
            dispatch(self.character, "look", self.game)   # show them the room right away
        except Exception:
            # Same guard as the command loop below -- a look/weather bug must
            # not kill the session on login (live: off-plane macro None crash).
            import traceback
            traceback.print_exc()
            self.send("Something went wrong showing the room -- you are still in.")

        # ---- PLAYING STATE ----
        # Loop forever reading commands until the session stops being 'alive'.
        while self.alive:
            line = await self.read_line()
            if line is None:
                break                 # client disconnected — leave the loop
            if line == "":
                continue              # they just hit enter — wait for the next line
            if self.report_capture is not None:
                # Multi-line bug/suggest capture is active: EVERY line (even
                # one that looks like a command) is buffered, not dispatched,
                # until the '.' terminator -- that's the whole point, see
                # __init__'s comment on report_capture.
                self._handle_report_capture_line(line)
                continue
            if self.help_edit is not None:
                # HEDIT modal editor is active: every line is a buffer edit
                # (/i, /d, /r, ...) or an appended body line, never a normal
                # game command -- same gate shape as report_capture above.
                self._handle_help_edit_line(line)
                continue
            # Record BEFORE dispatch so a crash still lands in history;
            # redact setpass so plaintext never hits bug reports (H2).
            entry = [history_line_for_storage(line), None]
            self.history.append(entry)
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
            # drain() waits if the outgoing buffer is backed up (slow client),
            # applying "backpressure" so we don't pile up unlimited data.
            await self.writer.drain()

        self.disconnect()             # loop ended -> clean up

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
            description = "\n".join(self.report_capture["lines"]).strip()
            kind = self.report_capture["kind"]
            # Optional generic prefix (e.g. cmd_helpsubmit stamps the
            # proposed keyword ahead of the pasted body) -- not report-kind
            # specific, any future paste-capture caller can use it.
            prefix = self.report_capture.get("prefix")
            self.report_capture = None
            if not description:
                self.send("Empty report -- nothing logged.")
                return
            if prefix:
                description = f"{prefix}\n{description}"
            if kind == reports.BUG:
                noun = "bug report"
            elif kind == reports.HELP:
                noun = "help idea"
            else:
                noun = "suggestion"
            bug_filing.record_and_confirm(
                self.character, kind, description,
                _report_history(self.character), self.game.report_dir, noun,
            )
            return
        if line.strip().lower() == "cancel":
            self.report_capture = None
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
            sub = rest.split(maxsplit=1)
            if len(sub) != 2 or not sub[0].isdigit():
                self.send("Usage: /i <line> <text>")
                return
            pos = int(sub[0])
            if pos < 1 or pos > len(state["body"]) + 1:
                self.send(f"Line must be between 1 and {len(state['body']) + 1}.")
                return
            state["body"].insert(pos - 1, sub[1])
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
            "Unknown editor command. Try: /list /i /d /r /syntax /category "
            "/alias /gm /ic /preview /save /cancel"
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
        if old is None or old is self:
            return
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
        old.alive = False
        old.character = None
        character.session = None
        sessions = getattr(self.game, "sessions", None)
        if sessions is not None and old in sessions:
            sessions.remove(old)
        writer = getattr(old, "writer", None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

    def disconnect(self):
        # Tidy up when a player leaves. THE INVARIANT (systems doc section 4-E):
        # logout is NOT deletion. The character stays in the world as an Echo —
        # an invulnerable, session-less figure — so we detach the session but
        # deliberately do NOT remove the character from its room.
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
            if getattr(self.character, "gm_mode", False) or getattr(
                self.character, "gm_spirit", False
            ):
                body = getattr(self.character, "gm_mode_body", None)
                body_key = getattr(self.character, "gm_body_key", None)
                if body is None and body_key:
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
                    # Park: stay in room, sessionless, true-invis.
                    spirit.gm_mode = False
                    spirit.gm_spirit = True
                    spirit.gm_spirit_permanent = True
                    spirit.session = None
                    self.character = None
                    break_follows(spirit)
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
            else:
                self.character.session = None    # the character is now an Echo
                break_follows(self.character)
                if self.character.location:
                    # session is already None, so the Echo itself can't receive this.
                    # Public face -- never raw storage keys in room traffic.
                    from engine.command_support import _presence_face
                    face = _presence_face(self.character)
                    self.character.location.broadcast(
                        f"{face} goes still, leaving only an echo."
                    )
        if self in self.game.sessions:
            self.game.sessions.remove(self)
        self._leave_connecting()
        # Staff ping after dropping from sessions so the leaver is not in
        # the recipient walk; exclude= still guards FakeSession edge cases.
        if disconnect_name:
            from engine import gm_notify
            gm_notify.ping_gms(
                self.game,
                f"{disconnect_name} has disconnected{{from}}.",
                exclude=leaving,
                peer_session=self,
            )
        self.game.save()              # persist the Echo's final position/inventory
        # Gateway: drop the public TCP on intentional quit / client EOF path.
        # (Game-process restart cancels play() without calling disconnect.)
        self._kick_gateway_client()
        try:
            self.writer.close()
        except Exception:
            pass                      # already closing/closed — nothing to do
