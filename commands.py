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
    _pull_followers,
)
from engine.verbs import ENGINE_COMMANDS
from engine.verbs.basic import _report_history, cmd_move
from engine import display_prefs
from engine.session_attach import heal_character_session
import game_select

# The active game's verbs (SUPERS, basegame, or {} for a lean engine boot).
# Lean engine smoke (two-repo Phase 4b / docs/plans/two_repo_purity.md) sets
# RIFTFORGE_GAME=none (or leaves supers/basegame both absent) and gets
# ENGINE_COMMANDS only.
GAME_COMMANDS = game_select.game_commands()


def __getattr__(name):
    """Lazy HELP_* proxies so smoke can still ``commands.HELP_TOPICS``.

    Topics are registered on ``engine.hooks`` by bootstrap / the module
    footer below — not imported eagerly into this namespace (Phase 7
    Stage G: ``help_topics`` may be a thin facade or empty on lean boots).
    """
    if name == "HELP_TOPICS":
        from engine import hooks as _h
        return _h.get_help_topics()
    if name == "HELP_CATEGORIES":
        from engine import hooks as _h
        return _h.get_help_categories()
    raise AttributeError(f"module 'commands' has no attribute {name!r}")


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
    "more", "stop",  # pager continuation for long help / staff dumps
    "wallet", "coins",
    "idlemode", "idle", "autoidle", "afk",
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
    "spirit", "ki", "hellcraft", "congregation", "findvessel", "findhusk",
    # Clock / prefs / tutorial meta
    "time", "date", "timeformat", "color",
    "config", "alias", "prompt",
    "hint", "tutorial",
    "socials",
    # Training sheet / regimen picker (suggestion #75 -- do not wake idle)
    "regimen",
    # OOC / account (outbound tell/ooc stay spectator; inbound already works)
    "ooc", "tell", "whisper", "reply", "r",
    "rpseek", "rpwhere",
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
    "attack", "kill", "k", "hit", "fight", "spar",
    "bite", "stake", "slay", "maul", "crush", "devour",
    "smite", "judgment", "rend", "gnaw", "howl", "hunt",
    "flee", "disengage",
})

# Sleep: block sensory / physical / outbound RP; sheet + vitals stay open.
# Derived from IDLE_SPECTATOR so idle watch and asleep recovery stay aligned.
ASLEEP_BLOCK = frozenset({
    "look", "l", "examine", "exa", "ex",
    "say", "'", "emote", "em", "smote", "tell", "whisper", "reply", "r",
    "rest", "sleep", "sit", "stand", "lay",
    "idlemode", "idle", "afk",
}) | IDLE_WAKE_MOVE | IDLE_WAKE_AGGRESSIVE

ASLEEP_SPECTATOR = (IDLE_SPECTATOR - ASLEEP_BLOCK) | frozenset({
    "wake", "logout", "hp", "fuel", "energy", "stamina", "where", "coins",
    "professions", "prof",
    "account", "combatnumbers", "scoremeters", "bigmap", "atlas", "brief",
    "dominion", "bounty", "cases", "quests", "journal", "mail",
    # Spirit Expert dream lane: sleep gate requires dreamenter while
    # the body is still closed to the waking world (magic_basics §4.4).
    "dreamenter", "dreamleave",
})

# cast dreamenter / cast dreamleave share the same asleep carve-out.
_ASLEEP_DREAM_CAST_RITES = frozenset({"dreamenter", "dreamleave"})


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


def _verb_disabled_for_actor(game, verb, actor):
    """True when staff disabled this verb for the actor.

    Catalog immersion cast (Dean, Chuck, …) always bypass the global
    ``dothepit`` disable so pit autopilot stays available on cast logins.
    """
    from engine import command_disable as _cmd_disable
    from engine import hooks

    if not _cmd_disable.is_disabled(game, verb):
        return False
    if game_select.game_name() == "supers" and hooks.verb_disable_bypass(
        game, verb, actor,
    ):
        return False
    return True


def dispatch(character, raw, game, *, force_actor=None):
    """Route one line of input to the right handler.

    ``force_actor`` runs the verb as another body (God ``twin`` / ``omni``
    one-shots) without changing sustained act focus on the login character.
    """
    from engine import log_context
    from engine.session_attach import heal_character_session

    if character is None:
        return

    # Heal half-cleared Session <-> Character links before any verb runs.
    heal_character_session(character, game)
    if game is not None:
        ticks = int(getattr(game, "game_time_ticks", 0) or 0)
        if ticks > 0:
            character._last_game_time_ticks = ticks
            if force_actor is not None and force_actor is not character:
                force_actor._last_game_time_ticks = ticks

    session = getattr(character, "session", None)
    viewport_turn_id = getattr(session, "_viewport_turn_id", None)

    # Colon emote shorthand (:grins) before alias expansion.
    stripped = (raw or "").strip()
    if stripped.startswith(":"):
        colon_body = stripped[1:].lstrip()
        if not colon_body:
            if session is not None:
                session.send("Smote what?")
            from engine.viewport import finish_turn
            finish_turn(session, turn_id=viewport_turn_id)
            return
        display_prefs.ensure_display_defaults(character)
        actor = force_actor or character
        log_context.set_command_context(
            game=game,
            session=getattr(character, "session", None),
            character=character,
        )
        try:
            return _dispatch_body(
                character, raw, game, "smote", colon_body,
                force_actor=force_actor,
            )
        finally:
            log_context.clear_command_context()
            from engine.persistence import (
                command_marks_character_dirty,
                mark_character_dirty,
            )
            if command_marks_character_dirty("smote", colon_body):
                mark_character_dirty(game, actor)
            from engine.viewport import finish_turn
            finish_turn(getattr(character, "session", None), turn_id=viewport_turn_id)

    # D65: expand player aliases before parse (never shadows built-ins).
    display_prefs.ensure_display_defaults(character)
    raw = display_prefs.expand_aliases(character, raw)

    verb, args = parse(raw)            # unpack the (verb, args) tuple into two vars
    if not verb:                       # blank line -- do nothing
        from engine.viewport import finish_turn
        finish_turn(getattr(character, "session", None), turn_id=viewport_turn_id)
        return

    log_context.set_command_context(
        game=game,
        session=getattr(character, "session", None),
        character=character,
    )
    actor = force_actor or character
    import time as _time
    from engine import lag_watch
    if game is not None:
        game._move_phase_ms = {}
    _cmd_t0 = _time.perf_counter()
    try:
        return _dispatch_body(character, raw, game, verb, args, force_actor=force_actor)
    finally:
        lag_watch.note_command_stall(
            game,
            character,
            verb,
            (_time.perf_counter() - _cmd_t0) * 1000.0,
            raw_preview=raw or "",
            extra=lag_watch.format_move_phases(game),
        )
        log_context.clear_command_context()
        from engine.persistence import (
            command_marks_character_dirty,
            mark_character_dirty,
        )
        if command_marks_character_dirty(verb, args):
            mark_character_dirty(game, actor)
        from engine.viewport import finish_turn
        finish_turn(getattr(character, "session", None), turn_id=viewport_turn_id)


def _dispatch_body(character, raw, game, verb, args, *, force_actor=None):
    import game_select
    _active_game = game_select.game_name()

    # Bloodrite chapel tithe (two-word player phrase).
    if verb == "consume" and (args or "").strip().lower() == "tithe":
        verb = "consumetithe"
        args = ""

    # Burkitsville Vanir anchor (two-word player phrase).
    if verb == "burn" and (args or "").strip().lower() == "tree":
        verb = "burntree"
        args = ""

    # Stamp player activity for auto-idle (skip Cadence SilentSession).
    from engine.npc_act import SilentSession
    from engine import hooks
    if (
        getattr(character, "session", None) is not None
        and not isinstance(character.session, SilentSession)
    ):
        if _active_game == "supers":
            hooks.dispatch_on_input(character, verb, game)
        else:
            # Lean engine / basegame: never import supers here -- that
            # package's __init__ re-registers hooks and clobbers the
            # active game's appearance / persona catalogs.
            import time
            character.last_input_monotonic = time.monotonic()

    # Sleep closes the room (no look / move / speech) but sheet panes stay
    # open -- hp, fuel, needs, score, inventory, who, config, …
    # Resting (awake) still hears everything; combat/move cancel rest below.
    from engine.command_support import (
        ASLEEP_WORLD_CLOSED_MSG,
        asleep_blocks_world,
        send_if_online,
    )
    _asleep_ok = verb in ASLEEP_SPECTATOR
    if not _asleep_ok and verb == "cast":
        # Bare ``cast`` has no rite token -- let cmd_cast print usage instead
        # of IndexError on split()[0] (bug report 544).
        cast_args = (args or "").strip()
        if cast_args:
            rite = cast_args.split(None, 1)[0].lower()
            _asleep_ok = rite in _ASLEEP_DREAM_CAST_RITES
    if asleep_blocks_world(character) and not _asleep_ok:
        from engine.report_context import note_verb_gate
        if resolve_walk_direction(verb, getattr(character, "location", None)):
            note_verb_gate(character, verb, "dispatch:asleep_move")
            send_if_online(
                character,
                "You're asleep -- type 'wake' before you can move.",
            )
            return
        note_verb_gate(character, verb, "dispatch:asleep")
        send_if_online(character, ASLEEP_WORLD_CLOSED_MSG)
        return

    # GM freeze: staff paralyzed the body -- movement, room speech, and
    # verbs stay closed. OOC and private tells stay open so they can still
    # talk to staff / other players. gm mute is what closes those channels.
    _FROZEN_ALLOWED = frozenset({
        "help", "commands", "more", "stop", "quit", "logout", "bug", "suggest",
        "score", "sc", "ooc", "replay",
        "rpseek", "rpwhere",
        "tell", "whisper", "reply", "r",
    })
    if getattr(character, "frozen", False) and verb not in _FROZEN_ALLOWED:
        from engine.report_context import note_verb_gate
        if resolve_walk_direction(verb, getattr(character, "location", None)):
            note_verb_gate(character, verb, "dispatch:frozen_move")
            send_if_online(
                character,
                "You're frozen by staff -- you can't move.",
            )
            return
        note_verb_gate(character, verb, "dispatch:frozen")
        send_if_online(
            character,
            "You're frozen by staff. You can still use help, bug, "
            "suggest, quit, ooc, replay, rpseek, rpwhere, and tell.",
        )
        return

    # Game-owned dispatch gates (cage / vessel passenger / combat KO).
    blocked, gate_msg = hooks.command_dispatch_gate(
        character, verb, args, game,
    )
    if blocked:
        send_if_online(character, gate_msg)
        return

    # GM mute: block global / room speech channels only (not movement).
    _MUTED_VERBS = frozenset({
        "say", "'", "emote", "em", "smote", "tell", "whisper", "reply", "r", "ooc",
    })
    if getattr(character, "muted", False) and verb in _MUTED_VERBS:
        from engine.report_context import note_verb_gate
        note_verb_gate(character, verb, "dispatch:gm_muted")
        send_if_online(
            character,
            "You're muted by staff -- you can't use that channel.",
        )
        return
    try:
        from supers import archangel_powers as arch_powers_mod
        if arch_powers_mod.is_biokinesis_muted(character, game) and verb in _MUTED_VERBS:
            from engine.report_context import note_verb_gate
            note_verb_gate(character, verb, "dispatch:biokinesis_muted")
            send_if_online(
                character,
                "Your voice is gone -- you can't use that channel. [MUTED]",
            )
            return
    except ImportError:
        pass

    # Manual cardinals / aggression cancel a paced walk (say/emote keep it).
    # ``walk`` itself manages focus inside cmd_walk. SUPERS registers the
    # interrupt via hooks.dispatch_on_input during idle stamp above.

    # Idlemode: only walking / aggressive verbs reclaim presence (then run).
    if hooks.try_idlemode_wake_dispatch(character, verb, game):
        return

    actor = hooks.resolve_dispatch_actor(
        character, verb, game, force_actor=force_actor,
    )

    def _loan_session_to_actor(fn):
        """Run *fn* with the login Session on *actor* when steering a host."""
        loaned = False
        prev_session = None
        login_session = heal_character_session(character, game)
        if actor is not character and login_session is not None:
            prev_session = getattr(actor, "session", None)
            actor.session = login_session
            loaned = True
        try:
            fn()
        finally:
            if loaned:
                actor.session = prev_session

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
                verb, getattr(actor, "location", None),
            ) is None
        ):
            hooks.cancel_rest(character)

    # Conversation Tree numbered-menu capture. ALWAYS_ALLOWED verbs
    # (look/help/quit/...) still fall through to normal dispatch so a
    # player is never soft-locked inside a reply menu.
    if getattr(actor, "_active_dialogue", None):
        from engine.systems import dialogue as _dialogue_mod
        if _dialogue_mod.try_handle_menu_input(actor, verb, args, game):
            display_prefs.send_prompt(character, game)
            return

    # Soft-but-strict authored quest gate (Family Business foyer, …).
    # Same layer as asleep/frozen -- not a pre-parser black hole.
    _quest_gate = None
    try:
        from engine.systems import quests as _quest_gate
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
            from engine.systems import vehicles as _veh_walk
            _veh = _veh_walk.vehicle_by_id(
                game, getattr(actor, "in_vehicle", None),
            )
            park_key = _veh.get("parked_room") if _veh else None
            if park_key:
                _curb = (getattr(game, "rooms", None) or {}).get(park_key)
                if _curb is not None:
                    walk_room = _curb
        except ImportError:
            pass
    walk_dir = resolve_walk_direction(verb, walk_room)
    if walk_dir is not None:
        allowed = True
        nudge = None
        if _quest_gate is not None:
            allowed, nudge = _quest_gate.pre_dispatch_allowed(
                actor, verb, is_move=True,
            )
        if not allowed:
            from engine.report_context import note_verb_gate
            note_verb_gate(character, verb, "dispatch:quest_gate_move", detail=nudge)
            character.session.send(nudge)
            display_prefs.send_prompt(character, game)
            return
        def _do_move():
            cmd_move(actor, walk_dir, game)
        _loan_session_to_actor(_do_move)
        display_prefs.send_prompt(character, game)
        return

    if _quest_gate is not None:
        allowed, nudge = _quest_gate.pre_dispatch_allowed(
            actor, verb, is_move=False,
        )
        if not allowed:
            from engine.report_context import note_verb_gate
            note_verb_gate(character, verb, "dispatch:quest_gate", detail=nudge)
            character.session.send(nudge)
            display_prefs.send_prompt(character, game)
            return

    # Look the verb up in cmdsets first, then the flat table.
    from engine import cmdset as cmdset_mod

    room = getattr(actor, "location", None)
    entry = cmdset_mod.lookup_command(actor, room, verb)
    if entry is None:
        entry = COMMANDS.get(verb)
    if entry:
        from engine import command_disable as _cmd_disable
        if _verb_disabled_for_actor(game, verb, actor):
            from engine.report_context import note_verb_gate
            note_verb_gate(character, verb, "dispatch:verb_disabled")
            character.session.send(
                f"'{verb}' is temporarily disabled by staff."
            )
            display_prefs.send_prompt(character, game)
            return
        handler, _help_text = entry
        from engine.viewport import note_dispatch
        note_dispatch(getattr(character, "session", None), verb, handler)
        _twin_owner = hooks.pre_command_handler(actor, verb, game)
        def _run_handler():
            from engine import metrics as metrics_mod
            metrics_mod.bump(game, "commands_total")
            metrics_mod.bump(game, f"command.{verb}")
            handler(actor, args, game)  # call whichever function we found
            if _active_game == "supers":
                from supers import life_log as life_log_mod
                from engine.npc_act import SilentSession
                if (
                    verb in life_log_mod.PLAYER_CMD_LOG_VERBS
                    and getattr(character, "session", None) is not None
                    and not isinstance(character.session, SilentSession)
                    and not verb.startswith("gm")
                ):
                    preview = (args or "")[:80]
                    cmd_payload = {"verb": verb, "args_preview": preview}
                    loc = getattr(actor, "location", None)
                    if loc is not None:
                        try:
                            title = (
                                loc.look_title()
                                if callable(getattr(loc, "look_title", None))
                                else getattr(loc, "name", None)
                            )
                        except (TypeError, ValueError, AttributeError):
                            title = getattr(loc, "name", None)
                        if title:
                            cmd_payload["room_title"] = str(title).strip()
                    life_log_mod.record(
                        game,
                        actor,
                        "player_cmd",
                        cmd_payload,
                    )
        _loan_session_to_actor(_run_handler)
        hooks.post_command_handler(_twin_owner, actor, game)
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
        from engine.help_db import closest_match

        from engine import channels
        dyn = channels.lookup_verb(verb)
        if dyn is not None and not dyn.builtin:
            def _run_channel():
                channels.cmd_dynamic_channel(actor, args, game, verb=verb)
            _loan_session_to_actor(_run_channel)
            display_prefs.send_prompt(character, game)
            return

        hint = closest_match(verb, COMMANDS.keys(), max_distance=3)
        if hint:
            character.session.send(
                f"Unknown command: '{verb}'. Did you mean '{hint}'? Try 'help'."
            )
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
import help_topics as _help_topics_mod

_engine_hooks.set_dispatch(dispatch)
_engine_hooks.set_help(
    _help_topics_mod.HELP_TOPICS, _help_topics_mod.HELP_CATEGORIES
)
