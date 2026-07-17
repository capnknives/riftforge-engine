"""
npc_act.py -- let Cadence / hostiles run the same verbs players type.

Immersion parity (AGENTS.md rule 9 / docs/SYSTEMS_DESIGN.md §4-E): the town
should feel like one shared world. Watchers must see the same room
broadcasts when an NPC or Echo does something a player would type, and the
same mechanical gates must apply. Offline Echoes run Cadence lifestyle AI
for that reason -- simulating life and roleplay, not a parallel silent sim.

Players reach the world through commands.dispatch (engine/connection.py).
NPCs and offline Echoes have no Session, so their AI used to call domain
helpers directly and skip the player-facing verbs -- which meant gates like
"must true-form before bite" could drift between players and hostiles.

npc_do() attaches a temporary SilentSession, runs one raw command line
through the same COMMANDS table, then restores the previous session
(usually None). Room.broadcast from handlers still fires, so townsfolk
see the same "starts working" / "bares fangs" lines they would for a
player. The silent session just absorbs the "You ..." feedback that
nobody would read.

Two-repo purity (Phase 2, docs/plans/two_repo_purity.md): this module lives
under `engine/`, so it must never import the root `commands.py` module
itself (that module pulls in `supers.verbs`). It instead runs whatever
`engine.hooks.set_dispatch(fn)` last registered -- `supers.bootstrap`'s
`register_all_hooks()` points that at `commands.dispatch` at game boot.
With no game installed the hook stays `None` and npc_do() is a no-op
(SilentSession stays empty) rather than raising.

No networking here -- SilentSession.send only appends to an in-memory
list. Keep Cadence planners thin: decide *what* to do, then npc_do the
verb (or call a shared domain helper that cmd_* also uses). Full telnet
dispatch for every AI step is not required -- pathfinding and need meters
may stay planner-side -- but when a verb exists, prefer this path over an
NPC-only shortcut that looks different in the room.
"""


class SilentSession:
    """Drop-in Session stand-in that records lines and never touches sockets.

    Mirrors the smoke test's FakeSession shape enough for cmd_* handlers:
    .send(message) and an empty .history so bug/suggest helpers stay quiet.

    Optional `character` back-link lets GM `snoop` see the "You ..." lines
    an NPC/Echo produces while npc_do temporarily attaches this session.
    """

    def __init__(self, character=None):
        self.lines = []
        # cmd_* / reports look for .history; keep an empty list (not deque)
        # so "no history" paths stay simple.
        self.history = []
        self.character = character
        self.gmcp_enabled = False
        self.gmcp_supports = {}

    def send(self, message):
        """Record output the way a real Session would emit it."""
        self.lines.append(message)
        # Fan out to GM snoopers (classic viewpoint mirror).
        if self.character is not None:
            from engine import snoop
            snoop.mirror_output(self.character, message)

    def send_gmcp(self, package, payload, force=False):
        """No-op -- NPCs/Echoes have no client to receive GMCP frames."""
        return


def npc_do(character, raw, game):
    """Run one player verb as an NPC / Echo via commands.dispatch.

    Returns the SilentSession's captured lines (mostly for tests). Always
    restores character.session afterward -- even if the handler raises --
    so a hostile never keeps a fake session that would make prey filters
    or Echo checks think they're online.

    Idlemode watchers: room broadcasts often use exclude=character, so the
    watching Session would miss "You start working" / buy / drink feedback.
    When the restored session is a real idlemode watcher, relay SilentSession
    lines with an [Echo] prefix so Cadence activity is visible to the player.
    """
    # Two-repo purity (Phase 2): this module lives under engine/, so it must
    # never import the root commands.py directly (that module pulls in
    # supers.verbs). server.py registers commands.dispatch onto this hook
    # at boot -- see engine/hooks.py's set_dispatch/get_dispatch.
    from engine import hooks
    from engine import snoop
    dispatch = hooks.get_dispatch()

    previous = getattr(character, "session", None)
    silent = SilentSession(character)
    character.session = silent
    # Kit / Echo activity transcript (duck-typed; no supers import here).
    logger = getattr(character, "activity_logger", None)
    if logger is not None:
        try:
            logger.do(raw)
        except Exception:
            pass
    try:
        # GMs snooping this actor see the verb they "typed" (same ] tag as
        # a live player's Session.play path).
        snoop.mirror_input(character, raw)
        if dispatch is not None:
            dispatch(character, raw, game)
    finally:
        character.session = previous
    if logger is not None and silent.lines:
        try:
            logger.you(silent.lines)
        except Exception:
            pass
    # Relay AI verb feedback to an idlemode spectator (not SilentSession).
    if (
        previous is not None
        and not isinstance(previous, SilentSession)
        and getattr(character, "idle_mode", False)
        and silent.lines
        and hasattr(previous, "send")
    ):
        for line in silent.lines:
            text = str(line).strip()
            if not text:
                continue
            previous.send(f"[Echo] {text}")
            # Trailing blank so Echo feedback does not glue onto the next
            # leave/arrive / chat / tip line.
            previous.send("")
    return silent.lines
