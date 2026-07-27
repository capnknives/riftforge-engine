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
since been peeled into two verb packages, split along the engine/game
boundary (AGENTS.md's "Where things live"):

  - `engine/verbs/`  -- generic, game-agnostic MUD verbs (look, move, get,
    say, who, help, ...). Exports `ENGINE_COMMANDS`.
  - `supers/verbs/` or `basegame/verbs/` -- whichever game package
    `game_select` resolves as active exports its own COMMANDS dict
    (SUPERS_COMMANDS / BASEGAME_COMMANDS). The two are mutually exclusive
    at runtime -- see game_select.py's docstring.

`command_support.py` (repo root, next to this file) holds the small handful
of helpers BOTH sides need (`_can_see_spirit`, `_display_name`, `DIRECTIONS`,
...) -- see its docstring. This module is now just `parse` + `dispatch` +
the merged `COMMANDS` table, plus re-exports so existing callers (notably
`smoke_test.py`, `engine/connection.py`, and `supers/cadence.py`/`pathfind.py`)
that do `from commands import X` keep working unchanged.
"""

from command_support import (
    DIRECTIONS,
    resolve_walk_direction,
    _can_see_spirit,
    _display_name,
    _presence_face,
    _pull_followers,
    is_staff_stealth_presence,
)
from engine.verbs import ENGINE_COMMANDS
from engine.verbs.basic import _report_history, cmd_move
from help_topics import HELP_CATEGORIES, HELP_TOPICS
import game_select

# The active game's verbs (SUPERS, basegame, or {} for a lean engine boot).
# Lean engine smoke (two-repo Phase 4b / docs/plans/two_repo_purity.md) sets
# RIFTFORGE_GAME=none (or leaves supers/basegame both absent) and gets
# ENGINE_COMMANDS only.
GAME_COMMANDS = game_select.game_commands()


# Idlemode wake gate: only movement or aggressive verbs reclaim presence.
# Everything else (look, say, get, train, sheet panes, OOC, …) keeps watching
# Cadence. Cadence drives idlemode bodies through npc_do + SilentSession --
# that path must NOT wake them either (see dispatch below).
#
# IDLE_SPECTATOR remains the documented "safe pane" set for help / smoke;
# wake is no longer "anything not in IDLE_SPECTATOR".
IDLE_SPECTATOR = frozenset({
    # Room / who / sheet basics
    "look", "l",
    "who", "whofull", "whohide",
    "score", "sc",
    "help", "commands", "changes",
    "wallet", "coins",
    "idlemode", "idle", "autoidle",
    "seek",
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
    "grace", "blood", "instinct", "devouring", "carrion", "souls",
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
    "whoallnpc", "npclist", "mobs",
    "reports",
    "gmmode", "gm on", "gm off",
    "snoop", "unsnoop",
    "immersion", "gmcast",
})

# Cardinal / diagonal move words from command_support.DIRECTIONS.
IDLE_WAKE_MOVE = frozenset(DIRECTIONS) | frozenset({
    "walk", "enter", "exit", "leave", "drive",
})

# Combat / predation verbs that mean "I'm back in the fight".
IDLE_WAKE_AGGRESSIVE = frozenset({
    "attack", "kill", "hit", "fight", "spar",
    "bite", "stake", "slay", "maul", "crush", "devour",
    "smite", "judgment", "blade", "rend", "gnaw", "howl", "hunt",
    "flee", "disengage",
})


def _idlemode_should_wake(verb, character=None):
    """True when a typed verb should clear idle_mode (move or aggression)."""
    if not verb:
        return False
    if verb in IDLE_WAKE_MOVE or verb in IDLE_WAKE_AGGRESSIVE:
        return True
    # Street-address exits (12223) etc. are walks but not in DIRECTIONS.
    if character is not None:
        return resolve_walk_direction(
            verb, getattr(character, "location", None),
        ) is not None
    return False


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
# tables into one; GAME_COMMANDS is listed second so it would win on a key
# clash, but ENGINE_COMMANDS and the active game's COMMANDS should never
# define the same verb in the first place. When no game is active,
# COMMANDS is engine-only.
COMMANDS = {**ENGINE_COMMANDS, **GAME_COMMANDS}

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
        "ooc", "bug", "suggest",
    })
    if getattr(character, "asleep", False) and verb not in _ASLEEP_ALLOWED:
        if resolve_walk_direction(verb, getattr(character, "location", None)):
            character.session.send(
                "You're asleep -- type 'wake' before you can move."
            )
            return
        character.session.send(
            "You're asleep -- the outside world is closed. Type 'wake'."
        )
        return

    # GM freeze: staff paralyzed the player -- only help / quit / bug path.
    # Mirror asleep so a frozen player can still file a report or disconnect.
    _FROZEN_ALLOWED = frozenset({
        "help", "commands", "quit", "logout", "bug", "suggest",
        "score", "sc", "ooc",
    })
    if getattr(character, "frozen", False) and verb not in _FROZEN_ALLOWED:
        if resolve_walk_direction(verb, getattr(character, "location", None)):
            character.session.send(
                "You're frozen by staff -- you can't move."
            )
            return
        character.session.send(
            "You're frozen by staff. You can still use help, bug, "
            "suggest, or quit."
        )
        return

    # Lucifer's Cage hold: sit and wait. Exit via cageproject swap or a
    # Primordial (T4+) God Mantle walking out -- see supers.magic.
    _CAGE_ALLOWED = frozenset({
        "look", "l", "examine", "ex", "score", "sc", "help", "commands",
        "inventory", "i", "time", "who", "quit", "logout", "bug", "suggest",
        "say", "'", "gm", "ooc",
    })
    try:
        from supers import magic as _magic_cage
        _cage_held = _magic_cage.cage_holds_actor(character)
    except Exception:
        _cage_held = False
    if _cage_held and verb not in _CAGE_ALLOWED:
        if resolve_walk_direction(verb, getattr(character, "location", None)):
            character.session.send(
                "The Cage holds you. Sit. Wait. Only another "
                "cageproject -- or a Primordial God's will -- frees you."
            )
            return
        character.session.send(
            "The Cage smothers action. You can look, speak, check score, "
            "or wait. Freedom is a projection swap or a Primordial God."
        )
        return

    # GM mute: block global / room speech channels only (not movement).
    _MUTED_VERBS = frozenset({
        "say", "'", "emote", "em", "tell", "whisper", "ooc",
    })
    if getattr(character, "muted", False) and verb in _MUTED_VERBS:
        character.session.send(
            "You're muted by staff -- you can't use that channel."
        )
        return

    # Manual cardinals / aggression cancel a paced walk (say/emote keep it).
    # ``walk`` itself manages focus inside cmd_walk.
    if verb != "walk" and (
        verb in IDLE_WAKE_MOVE
        or verb in IDLE_WAKE_AGGRESSIVE
        or resolve_walk_direction(verb, getattr(character, "location", None))
        is not None
    ):
        try:
            from supers import walk as walk_mod
            if walk_mod.has_walk_focus(character):
                from engine.npc_act import SilentSession
                if not isinstance(
                    getattr(character, "session", None), SilentSession
                ):
                    walk_mod.clear_walk_focus(character)
        except ImportError:
            pass

    # Idlemode: only walking / aggressive verbs reclaim presence (then run).
    # Say, get, train, sheet panes, OOC, etc. keep watching Cadence.
    # Cadence drives idlemode bodies through npc_do + SilentSession -- that
    # must NOT wake them, or the first AI verb silently drops idle_mode and
    # the body freezes (wake text went to the silent sink, not the player).
    if getattr(character, "idle_mode", False):
        if _idlemode_should_wake(verb, character):
            # Local import: npc_act imports commands inside npc_do only.
            from engine.npc_act import SilentSession
            if isinstance(character.session, SilentSession):
                # Cadence AI verb -- keep watching; do not clear idle_mode.
                pass
            else:
                # Pack mission-defer: walk/fight must confirm via idle off.
                try:
                    from supers import pack as pack_mod
                    if getattr(character, "pack_mission_defer", False):
                        pack_mod.try_wake_from_pack_defer(
                            character, game, confirm=False,
                        )
                        return
                except ImportError:
                    pass
                character.idle_mode = False
                # Waking cancels a locked seek focus (same as idle off).
                try:
                    from supers import seek as seek_mod
                    if seek_mod.has_seek_focus(character):
                        seek_mod.clear_seek_focus(character)
                except ImportError:
                    pass
                character.session.send(
                    "You snap back -- your Echo stirs and you are present again."
                )
                if (
                    character.location
                    and not is_staff_stealth_presence(character)
                ):
                    face = _presence_face(character)
                    character.location.broadcast(
                        f"{face}'s echo stirs and comes back to life.",
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
        if (
            verb not in _REST_KEEP
            and resolve_walk_direction(
                verb, getattr(character, "location", None),
            ) is None
        ):
            try:
                from supers import lodging
                lodging.cancel_rest_if_any(character)
            except ImportError:
                from engine import hooks
                hooks.cancel_rest(character)

    # God omnipresence: when focused on twin, room verbs / movement run as
    # the twin body (session output still reaches the God).
    actor = character
    _twin_session_restore = None
    try:
        from supers import god_omnipresence as go
        if go.should_redirect_to_twin(character, verb):
            twin = go.resolve_twin(character, game)
            if twin is not None and getattr(twin, "location", None) is not None:
                actor = twin
                if twin.session is None:
                    twin.session = character.session
                    _twin_session_restore = twin
                # Plain tag so screenreaders know which body is active.
                if verb in ("look", "l") or resolve_walk_direction(
                    verb, getattr(character, "location", None),
                ):
                    pass  # look/move feedback comes from handlers
    except ImportError:
        pass

    # Soft-but-strict authored quest gate (Family Business foyer, …).
    # Same layer as asleep/frozen -- not a pre-parser black hole.
    _quest_gate = None
    try:
        from supers import quests as _quests_gate
        _quest_gate = _quests_gate
    except ImportError:
        pass

    # Movement is handled first because it passes a direction, not args.
    # Street-address exits (populate homes) resolve here too — not only
    # cardinals / Ash Court a1..c10 in DIRECTIONS.
    # Aboard: resolve named exits (garage, …) against the curb Room, not
    # the cabin (cabin has no Room.exits).
    walk_room = getattr(actor, "location", None)
    if getattr(actor, "in_vehicle", None):
        try:
            from supers import vehicles as _veh_walk
            _veh = _veh_walk.vehicle_by_id(
                game, getattr(actor, "in_vehicle", None),
            )
            _curb = _veh_walk.vehicle_curb_room(game, _veh) if _veh else None
            if _curb is not None:
                walk_room = _curb
        except ImportError:
            pass
    walk_dir = resolve_walk_direction(verb, walk_room)
    if walk_dir is not None:
        if _quest_gate is not None:
            allowed, nudge = _quest_gate.pre_dispatch_allowed(
                actor, verb, is_move=True,
            )
            if not allowed:
                character.session.send(nudge)
                if _twin_session_restore is not None:
                    _twin_session_restore.session = None
                display_prefs.send_prompt(character, game)
                return
        cmd_move(actor, walk_dir, game)
        if _twin_session_restore is not None:
            _twin_session_restore.session = None
        display_prefs.send_prompt(character, game)
        return

    if _quest_gate is not None:
        allowed, nudge = _quest_gate.pre_dispatch_allowed(
            actor, verb, is_move=False,
        )
        if not allowed:
            character.session.send(nudge)
            if _twin_session_restore is not None:
                _twin_session_restore.session = None
            display_prefs.send_prompt(character, game)
            return

    # Look the verb up in the table. .get() returns None if it isn't a command.
    entry = COMMANDS.get(verb)
    if entry:
        from engine import command_disable as _cmd_disable
        if _cmd_disable.is_disabled(game, verb):
            character.session.send(
                f"'{verb}' is temporarily disabled by staff."
            )
            if _twin_session_restore is not None:
                _twin_session_restore.session = None
            display_prefs.send_prompt(character, game)
            return
        handler, _help_text = entry
        handler(actor, args, game)  # call whichever function we found
        # Authored quests: simple verb steps (look / gear / track / …).
        # Dedicated events (takehunt, rent, …) fire from success paths.
        if _quest_gate is not None:
            try:
                _quest_gate.notify(
                    actor, "verb", verb=verb, game=game,
                )
            except Exception:
                # A broken quest hook must not swallow the whole command,
                # but it also must not vanish silently -- surface it to the
                # server console so a regression in quest wiring is visible.
                import traceback
                print(
                    f"[commands] quest notify failed for verb {verb!r}:",
                    flush=True,
                )
                traceback.print_exc()
    else:
        character.session.send(f"Unknown command: '{verb}'. Try 'help'.")

    if _twin_session_restore is not None:
        _twin_session_restore.session = None

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
