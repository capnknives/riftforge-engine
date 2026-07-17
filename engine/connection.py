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

    def _notify_gateway_bound(self, name: str):
        """Tell the gateway this sid is logged in (for reattach after restart)."""
        bridge = self.gateway_bridge
        sid = self.gateway_session_id
        if bridge is None or not sid or not name:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(bridge.notify_bound(sid, name))

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
        # Gateway reattach: game restarted while this telnet client stayed
        # held -- skip name/password and resume play() like copyover.
        reattach = getattr(self, "_gateway_reattach_name", None)
        if reattach:
            char = self.game.find_character(reattach)
            if char is not None and not getattr(char, "is_npc", False):
                self._gateway_reattach_name = None
                char.session = self
                char.idle_mode = False
                char.last_input_tick = getattr(
                    self.game, "game_time_ticks", 0
                ) or 0
                self.character = char
                if self not in self.game.sessions:
                    self.game.sessions.append(self)
                self.reset_gmcp()
                from engine import gmcp
                from engine import mssp
                gmcp.offer_gmcp(self)
                mssp.offer_mssp(self)
                self.send(
                    "*** The world reforms around you. You're still here. ***"
                )
                self._notify_gateway_bound(char.key)
                hooks.after_session_attach(char, self.game)
                await self.play()
                return
            # Name gone or NPC — fall through to a fresh login prompt.
            self._gateway_reattach_name = None

        # ---- LOGIN STATE ----
        # Offer GMCP + MSSP before the welcome text so Mudlet / listing
        # crawlers can DO early (before any login line).
        from engine import gmcp
        from engine import mssp
        gmcp.offer_gmcp(self)
        mssp.offer_mssp(self)
        self.send("Welcome to Mortals and Monsters (pre-alpha).")
        self.send("By what name are you known?")

        # Keep asking until we get a usable name + password. Ways around the
        # loop: a blank/invalid name, an NPC name, a wrong password, a live
        # session without a password to prove takeover, or success (break).
        # takeover is True when we kicked another live Session for this name.
        takeover = False
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
            existing = self.game.find_character(name)
            # Town NPCs / hostiles share the character roster but are never
            # player logins -- letter-only keys (Marta, Bobby, …) used to
            # attach passwordless as if they were Echoes.
            if existing is not None and getattr(existing, "is_npc", False):
                self.send(
                    "That name belongs to a townsfolk, not a player. "
                    "Choose another:"
                )
                continue
            # Immersion cast keys (Buffy, Constantine, …) are reserved via
            # a game hook -- engine stays SUPERS-free (two-repo purity).
            # Use the module-level `hooks` import (do NOT re-import locally
            # here -- that would make `hooks` a function-local name and
            # break reconnect's after_session_attach below).
            if not existing:
                if hooks.is_reserved_login_name(name):
                    self.send(
                        "That name is reserved for the immersion cast. "
                        "Choose another:"
                    )
                    continue
                break                 # fresh name -- new-character path below

            # ---- Returning character: password, then optional takeover ----
            # Password is required whenever a hash exists. When another
            # Session still holds the body (linkdead / forgotten client /
            # second login), a correct password kicks that session so the
            # owner can reclaim the character. Passwordless bodies still
            # reconnect as Echoes, but cannot take over a live session
            # (no way to prove ownership).
            live = existing.session is not None
            if existing.password_hash:
                self.send("Password:")
                password = await self.read_line()
                if password is None:
                    return
                # Same client may tag password lines; strip Pn only.
                password = strip_client_session_tags(password or "")
                if not auth.verify_password(password, existing.password_hash):
                    self.send("Incorrect password. By what name are you known?")
                    continue          # back to square one; don't leak WHICH part was wrong
                if live:
                    self._take_over_session(existing)
                    takeover = True
            elif live:
                # No password on file -- refuse rather than steal the seat.
                self.send("That name is already in play. Choose another:")
                continue
            break                     # verified reconnect / takeover / passwordless Echo

        if existing:
            # ---- RECONNECT / TAKEOVER: reattach the wires ----
            # Echo wake (section 4-E) when the body had no Session; takeover
            # when we just kicked a live Session -- character never left play.
            char = existing
            # Capitalize a forgotten lowercase key (velan -> Velan) so who
            # / look match every other name. Case-insensitive find already
            # matched; rewrite the stored key when only casing differs.
            if apply_login_name_case(char, name, self.game):
                self.send(f"(Name casing fixed -- you are {char.key}.)")
            char.session = self
            # Offline regimen: stretch (growth-only) resets on reconnect.
            # Pending Tier break from banked Echo growth is applied in
            # hooks.after_session_attach (game side) so engine/ never
            # imports supers (two-repo purity).
            char.offline_gains_this_stretch = 0
            # Never wake into idlemode -- a fresh login is always "present".
            # (Mid-idle copyover can persist the flag; reconnect clears it.)
            char.idle_mode = False
            # Fresh login starts the auto-idle AFK clock now.
            char.last_input_tick = getattr(self.game, "game_time_ticks", 0) or 0
            self.character = char
            self.game.sessions.append(self)
            if takeover:
                # Still embodied -- no Echo stir broadcast.
                if char.location is not None:
                    char.location.broadcast(
                        f"{char.key}'s attention snaps back into focus.",
                        exclude=char,
                    )
                self.send(
                    f"\r\nWelcome back, {char.key}! "
                    "(Previous connection closed.)"
                )
            else:
                char.location.broadcast(
                    f"{char.key}'s echo stirs and comes back to life.",
                    exclude=char,
                )
                self.send(f"\r\nWelcome back, {char.key}!")
            if not char.password_hash:
                self.send(
                    "(This character has no password set -- "
                    "'setpass <new password>' to add one.)"
                )
            # Mail notify + pending offline Tier break -- after Session is
            # wired (D64 / Echo softcap).
            hooks.after_session_attach(char, self.game)
            # Dark-green staff ping: returning player woke their Echo /
            # reclaimed a live seat.
            from engine import gm_notify
            gm_notify.ping_gms(
                self.game,
                f"{char.key} has connected{gm_notify.format_from(self)}.",
                exclude=char,
            )
        else:
            # ---- NEW CHARACTER: password, then chargen, then place --------
            # Chargen (appearance + pronoun + Human Background) runs BEFORE
            # move_to / broadcast / save so a disconnect mid-flow leaves no
            # half-made Echo in the world (section 7 character creation).
            self.send("Choose a password (at least 4 characters):")
            while True:
                password = await self.read_line()
                if password is None:
                    return
                # Strip Pn tags so a web client does not bake them into the hash.
                password = strip_client_session_tags(password or "")
                if len(password) >= 4:
                    break
                self.send("Too short -- at least 4 characters. Try again:")

            char = Character(name)
            char.password_hash = auth.hash_password(password)
            char.session = self       # so chargen prompts can reach the client
            self.character = char
            # Staff ping as soon as the name+password stick -- before
            # chargen questions, so GMs see "is making a character…"
            # while the player is still answering prompts.
            from engine import gm_notify
            gm_notify.ping_gms(
                self.game,
                f"{name} has connected{gm_notify.format_from(self)} "
                "and is making a character...",
                exclude=char,
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
            self.game.sessions.append(self)   # register for 'who' and broadcasts
            start_room.broadcast(f"{name} materializes.", exclude=char)
            self.send(f"\r\nWelcome, {name}! Type 'help' for commands.")
            # Post-placement game content (path home stamp + tutorial, ...).
            # Must run AFTER move_to -- see set_after_new_character's
            # docstring on engine/hooks.py. Path home stamping lives in
            # the SUPERS hook (bootstrap), not a supers import here
            # (two-repo purity).
            hooks.after_new_character(char, self.game)
            # Same attach hook as reconnect (mail notify, …).
            hooks.after_session_attach(char, self.game)

        self.game.save()              # persist the new/reconnected character now
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
        dispatch(self.character, "look", self.game)   # show them the room right away

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
            # Record the raw line BEFORE dispatch so a crash still lands in
            # history; traceback stays None until the except block fills it.
            entry = [line, None]
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
            self.report_capture = None
            if not description:
                self.send("Empty report -- nothing logged.")
                return
            noun = "bug report" if kind == reports.BUG else "suggestion"
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
        # Capture name + peer before we clear session / leave sessions list,
        # so the staff ping still has a readable label (mid-chargen included).
        disconnect_name = None
        disconnect_from = ""
        leaving = self.character
        if leaving is not None:
            disconnect_name = leaving.key
            from engine import gm_notify
            disconnect_from = gm_notify.format_from(self)
        if self.character:
            # Drop any snoop THIS character was running (they're leaving);
            # keep snoopers aimed *at* them -- an Echo is still watchable.
            from engine import snoop
            snoop.stop(self.character, quiet=True)
            # Exit GM form first so the resting-form prop is cleaned up and
            # the Echo rematerializes at the body they left (not mid-map).
            # Inlined (no supers import -- engine stays game-agnostic).
            if getattr(self.character, "gm_mode", False):
                body = getattr(self.character, "gm_mode_body", None)
                body_room = getattr(self.character, "gm_mode_body_room", None)
                if (
                    body_room is not None
                    and body is not None
                    and body in getattr(body_room, "contents", [])
                ):
                    body_room.remove(body)
                # Sweep orphaned resting props tagged for this character.
                owner = self.character.key
                for room in self.game.rooms.values():
                    for obj in list(room.contents):
                        if (
                            getattr(obj, "gm_resting_form", False)
                            and getattr(obj, "gm_resting_owner", None) == owner
                        ):
                            if body_room is None:
                                body_room = room
                            room.remove(obj)
                if body_room is not None and self.character.location is not body_room:
                    self.character.move_to(body_room)
                self.character.gm_mode = False
                self.character.gm_mode_body = None
                self.character.gm_mode_body_room = None
            self.character.session = None    # the character is now an Echo
            break_follows(self.character)
            if self.character.location:
                # session is already None, so the Echo itself can't receive this.
                self.character.location.broadcast(
                    f"{self.character.key} goes still, leaving only an echo."
                )
        if self in self.game.sessions:
            self.game.sessions.remove(self)
        # Staff ping after dropping from sessions so the leaver is not in
        # the recipient walk; exclude= still guards FakeSession edge cases.
        if disconnect_name:
            from engine import gm_notify
            gm_notify.ping_gms(
                self.game,
                f"{disconnect_name} has disconnected{disconnect_from}.",
                exclude=leaving,
            )
        self.game.save()              # persist the Echo's final position/inventory
        # Gateway: drop the public TCP on intentional quit / client EOF path.
        # (Game-process restart cancels play() without calling disconnect.)
        self._kick_gateway_client()
        try:
            self.writer.close()
        except Exception:
            pass                      # already closing/closed — nothing to do
