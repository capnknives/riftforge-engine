"""
commands.py -- parsing raw input and dispatching it to handlers.

This is the server-side twin of the MUD-client triggers/aliases you already
know: raw text in -> verb + args -> the function that runs.

Every command handler has the same shape (same parameters, in the same order):

    def cmd_something(character, args, game):

    character : the Character who typed it
    args      : everything after the verb, as a single string
    game      : the Game object (for global things like 'who')

Because they all look the same, we can store them in a dict and call whichever
one matches the verb (see COMMANDS below).

This file used to hold every `cmd_*` handler directly (7000+ lines). It has
since been peeled into two verb packages, split along the engine/SUPERS
boundary (AGENTS.md's "Where things live"):

  - `engine/verbs/`  -- generic, game-agnostic MUD verbs (look, move, get,
    say, who, help, ...). Exports `ENGINE_COMMANDS`.
  - `supers/verbs/`  -- SUPERS game-content verbs (combat, training, Origin
    powers, the Cadence economy, GM tools, ...). Exports `SUPERS_COMMANDS`.

`command_support.py` (repo root, next to this file) holds the small handful
of helpers BOTH sides need (`_can_see_spirit`, `_display_name`, `DIRECTIONS`,
...) -- see its docstring. This module is now just `parse` + `dispatch` +
the merged `COMMANDS` table, plus re-exports so existing callers (notably
`smoke_test.py`, `engine/connection.py`, and `supers/cadence.py`/`pathfind.py`)
that do `from commands import X` keep working unchanged.
"""

from command_support import (
    DIRECTIONS,
    _can_see_spirit,
    _display_name,
    _pull_followers,
)
from engine.verbs import ENGINE_COMMANDS
from engine.verbs.basic import _report_history, cmd_move
from help_topics import HELP_CATEGORIES, HELP_TOPICS

# Soft-optional SUPERS verbs: lean engine boots with ENGINE_COMMANDS only
# (two-repo Phase 4b / docs/plans/two_repo_purity.md).
try:
    from supers.verbs import SUPERS_COMMANDS
except ImportError:
    SUPERS_COMMANDS = {}


def parse(raw):
    """Split a raw line like 'get rusted sword' into ('get', 'rusted sword')."""
    raw = raw.strip()              # remove leading/trailing whitespace and newline
    if not raw:                    # empty string is "falsy" -- nothing was typed
        return "", ""
    # split(maxsplit=1) splits on the FIRST space only, so 'rusted sword' stays
    # together as one argument instead of becoming ['rusted', 'sword'].
    parts = raw.split(maxsplit=1)
    verb = parts[0].lower()        # first word, lowercased so 'LOOK' == 'look'
    # If there was a second part, that's the args; otherwise args is empty.
    args = parts[1] if len(parts) > 1 else ""
    return verb, args              # hand back two values as a tuple


# The real dispatch table: every verb -> (handler, help_text), merged from
# both verb packages. Storing a one-line help_text alongside every handler --
# not just the function -- is a deliberate project rule (CLAUDE.md/AGENTS.md):
# a new command isn't finished until it has one here. 'commands' (cmd_commands)
# reads this SAME dict to build its listing; bare 'help' lists
# HELP_CATEGORIES / HELP_TOPICS instead. Dict-unpacking with `**` merges both
# tables into one; SUPERS_COMMANDS is listed second so it would win on a key
# clash, but ENGINE_COMMANDS and SUPERS_COMMANDS should never define the same
# verb in the first place. When SUPERS is absent, COMMANDS is engine-only.
COMMANDS = {**ENGINE_COMMANDS, **SUPERS_COMMANDS}

# Idlemode spectator verbs: keep watching Cadence; do NOT clear idle_mode.
# Rule: wake only when the typed verb means reclaiming presence / acting in
# the world. Spectator = read-only sheet/info, OOC/account/meta, GM inspect
# and session-watch tools. Always wake: DIRECTIONS, IC speech/emotes/socials,
# inventory/room mutation, combat/train/powers, Cadence lifestyle, relationship
# write shortcuts (friend/…), GM mutate (goto/set/spawn/…), dual-mode verbs
# that commonly travel or edit the world when used with args are still listed
# here only when bare use is primarily a status/list pane (relate, missions,
# gmmode status) -- no per-arg wake in this pass.
# Cadence drives idlemode bodies through npc_do + SilentSession; that path
# must NOT wake them (see dispatch below).
IDLE_SPECTATOR = frozenset({
    # Room / who / sheet basics
    "look", "l",
    "who", "whofull", "whohide",
    "score", "sc",
    "help", "commands", "changes",
    "wallet", "coins",
    "idlemode", "idle", "autoidle",
    # Sheet / vitals / examine
    "needs",
    "skills", "powers", "kit",
    "disciplines",
    "home",
    "npcs",
    "spells",
    "inventory", "inv", "i",
    "examine", "exa", "ex",
    "map",
    # Origin status panes (read-only fuel / kit summaries)
    "grace", "blood", "instinct", "devouring", "souls",
    "integrity", "favor", "mana", "mutations",
    "spirit", "ki", "hellcraft", "congregation", "findhusk",
    # Clock / prefs / tutorial meta
    "time", "date", "timeformat", "color",
    "config", "alias", "prompt",
    "hint", "tutorial",
    "socials",
    # Training sheet / regimen picker (suggestion #75 -- do not wake idle)
    "regimen",
    # OOC / account (outbound tell/ooc stay spectator; inbound already works)
    "ooc", "tell", "whisper",
    "bug", "suggest", "setpass", "quit",
    # Relationship / mission list panes (write shortcuts like friend wake)
    "relate", "relationship",
    "missions", "board",
    # GM inspect / session meta (mutate verbs like goto/set/spawn still wake)
    "stat", "stats",
    "gmlist",
    "whoallnpc", "npclist",
    "reports",
    "gmmode",
    "snoop", "unsnoop",
    "immersion", "gmcast",
})


def dispatch(character, raw, game):
    """Route one line of input to the right handler."""
    # D65: expand player aliases before parse (never shadows built-ins).
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    raw = display_prefs.expand_aliases(character, raw)

    verb, args = parse(raw)            # unpack the (verb, args) tuple into two vars
    if not verb:                       # blank line -- do nothing
        return

    # Stamp player activity for auto-idle (skip Cadence SilentSession).
    from engine.npc_act import SilentSession
    if (
        getattr(character, "session", None) is not None
        and not isinstance(character.session, SilentSession)
    ):
        try:
            from supers.verbs import engine_flavor as _idle_flavor
            _idle_flavor.stamp_input_activity(character, game)
        except ImportError:
            # Lean engine: stamp the AFK clock without SUPERS helpers.
            character.last_input_tick = getattr(
                game, "game_time_ticks", 0
            ) or 0

    # Sleep closes the outside world: only wake / help / quit / logout work.
    # Resting (awake) still hears everything; combat/move cancel rest below.
    _ASLEEP_ALLOWED = frozenset({
        "wake", "help", "commands", "quit", "logout", "score", "sc",
    })
    if getattr(character, "asleep", False) and verb not in _ASLEEP_ALLOWED:
        if verb in DIRECTIONS:
            character.session.send(
                "You're asleep -- type 'wake' before you can move."
            )
            return
        character.session.send(
            "You're asleep -- the outside world is closed. Type 'wake'."
        )
        return

    # Idlemode: spectator verbs keep watching; anything else auto-wakes
    # then runs (so typing 'north' takes control back without a second step).
    # Cadence drives idlemode bodies through npc_do + SilentSession -- that
    # must NOT wake them, or the first AI verb silently drops idle_mode and
    # the body freezes (wake text went to the silent sink, not the player).
    if getattr(character, "idle_mode", False):
        if verb in DIRECTIONS or verb not in IDLE_SPECTATOR:
            # Local import: npc_act imports commands inside npc_do only.
            from engine.npc_act import SilentSession
            if isinstance(character.session, SilentSession):
                # Cadence AI verb -- keep watching; do not clear idle_mode.
                pass
            else:
                character.idle_mode = False
                character.session.send(
                    "You snap back -- your Echo stirs and you are present again."
                )
                if character.location:
                    character.location.broadcast(
                        f"{character.key}'s echo stirs and comes back to life.",
                        exclude=character,
                    )

    # Awake rest cancels on most active verbs (not look/help/score/wake).
    if getattr(character, "resting", False) and not getattr(
        character, "asleep", False
    ):
        _REST_KEEP = frozenset({
            "rest", "wake", "look", "l", "help", "commands", "score", "sc",
            "inventory", "inv", "i", "who", "time", "home",
        })
        if verb not in _REST_KEEP and verb not in DIRECTIONS:
            try:
                from supers import lodging
                lodging.cancel_rest_if_any(character)
            except ImportError:
                from engine import hooks
                hooks.cancel_rest(character)

    # Movement is handled first because it passes a direction, not args.
    if verb in DIRECTIONS:
        cmd_move(character, DIRECTIONS[verb], game)
        display_prefs.send_prompt(character, game)
        return

    # Look the verb up in the table. .get() returns None if it isn't a command.
    entry = COMMANDS.get(verb)
    if entry:
        handler, _help_text = entry
        handler(character, args, game)  # call whichever function we found
    else:
        character.session.send(f"Unknown command: '{verb}'. Try 'help'.")

    # D65: reprint custom prompt after every command (empty template = skip).
    display_prefs.send_prompt(character, game)

    # EXTENSION POINT: next up is 'get <item> from <body>' -- the same _find_item
    # helper, but searching a container's contents. That's the plumbing the
    # body-as-container death mechanic (systems doc section 6) will need.


# Soft-optional boot: register dispatch + help even when supers.bootstrap
# did not run (lean engine / tools/engine_smoke.py). With SUPERS present,
# bootstrap re-registers the same callables — harmless idempotent overwrite.
from engine import hooks as _engine_hooks

_engine_hooks.set_dispatch(dispatch)
_engine_hooks.set_help(HELP_TOPICS, HELP_CATEGORIES)
