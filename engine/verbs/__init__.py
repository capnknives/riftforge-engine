"""engine/verbs -- generic, game-agnostic MUD command handlers.

This package is the engine side of the commands.py split (see that module's
docstring, and command_support.py's, for the full rationale). Everything
here is a plain MUD-engine verb -- movement, looking, inventory, talking,
who's online, the clock, help/commands listings, and the bug/suggestion
report pipeline -- that would make sense in ANY game built on this engine,
not just SUPERS.

`ENGINE_COMMANDS` is a verb -> (handler, help_text) dict, exactly like the
old commands.py `COMMANDS` table but scoped to just these engine-generic
verbs. `commands.py` merges this with `supers.verbs.SUPERS_COMMANDS` to
build the real dispatch table.

Hard rule (AGENTS.md / two-repo purity Phase 2): this package must NOT
import the SUPERS game package at all -- not at module level, and not with
a LAZY, function-local import either. An earlier pass allowed a handler
body to reach into SUPERS with a function-local import for a bit of game
flavor; Phase 2 forbids that outright (see basic.py's module docstring) --
those flavor sites now go through `engine.hooks` instead, which SUPERS
wires up at boot without this package ever needing to know SUPERS exists.
"""
from .basic import (
    cmd_alias,
    cmd_bug,
    cmd_changes,
    cmd_color,
    cmd_commands,
    cmd_config,
    cmd_date,
    cmd_drop,
    cmd_emote,
    cmd_enter,
    cmd_examine,
    cmd_exit_zone,
    cmd_follow,
    cmd_get,
    cmd_go_in,
    cmd_go_out,
    cmd_help,
    cmd_idlemode,
    cmd_inventory,
    cmd_look,
    cmd_map,
    cmd_ooc,
    cmd_open,
    cmd_prompt,
    cmd_quit,
    cmd_reports,
    cmd_resolve,
    cmd_say,
    cmd_search,
    cmd_setpass,
    cmd_suggest,
    cmd_tell,
    cmd_time,
    cmd_timeformat,
    cmd_unfollow,
    cmd_who,
)

# Verb -> (handler, help_text). See commands.py's COMMANDS docstring for why
# the help_text lives right next to the handler instead of somewhere else.
ENGINE_COMMANDS = {
    "look":      (cmd_look,      "look at the room, or look <name> / look me for a description"),
    "l":         (cmd_look,      "look at the room, or look <name> / look me for a description"),
    "search":    (cmd_search,    "feel for secret exits in this room (see 'help search')"),
    "map":       (cmd_map,       "local ASCII overland map (only outdoors; see 'help map')"),
    "examine":   (cmd_examine,   "look closely at an item or person (try examine sword or examine me)"),
    "exa":       (cmd_examine,   "look closely at an item or person (try examine sword or examine me)"),
    "ex":        (cmd_examine,   "look closely at an item or person (try examine sword or examine me)"),
    "enter":     (cmd_enter,     "enter <zone> from an overland gateway (see 'help enter')"),
    "exit":      (cmd_exit_zone, "leave a pocket zone back to the overland grid (see 'help enter')"),
    "in":        (cmd_go_in,     "go in through a nested indoor exit"),
    "out":       (cmd_go_out,    "go out through a nested indoor exit"),
    "leave":     (cmd_go_out,    "go out through a nested indoor exit"),
    "get":       (cmd_get,       "pick up an item in the room (get <name>)"),
    "take":      (cmd_get,       "pick up an item in the room (get <name>)"),
    "drop":      (cmd_drop,      "drop a carried item here (drop <name>)"),
    "open":      (cmd_open,      "force open a locked container (e.g. a dungeon strongbox)"),
    "inventory": (cmd_inventory, "list what you are carrying"),
    "inv":       (cmd_inventory, "list what you are carrying"),
    "i":         (cmd_inventory, "list what you are carrying"),
    "say":       (cmd_say,       "speak aloud to everyone in the room (say <words>)"),
    "'":         (cmd_say,       "speak aloud to everyone in the room (say <words>)"),
    "emote":     (cmd_emote,     "free-form third-person action (try emote grins.)"),
    "em":        (cmd_emote,     "free-form third-person action (try emote grins.)"),
    "tell":      (cmd_tell,      "private message someone anywhere (try tell Erin hi)"),
    "whisper":   (cmd_tell,      "private message someone anywhere (try tell Erin hi)"),
    "ooc":       (cmd_ooc,       "global out-of-character chat; bare ooc shows the last 20 lines"),
    "follow":    (cmd_follow,    "follow <name> when they move; bare follow stops (see 'help follow')"),
    "unfollow":  (cmd_unfollow,  "stop following whoever you are following"),
    "idlemode":  (cmd_idlemode,  "act as an Echo while still logged in (see 'help idlemode')"),
    "idle":      (cmd_idlemode,  "short for idlemode -- bare toggles (see 'help idlemode')"),
    "setpass":   (cmd_setpass,   "set or change your character password"),
    "who":       (cmd_who,       "list players online"),
    "time":      (cmd_time,      "show the in-game clock, day period, and season"),
    "timeformat": (cmd_timeformat, "clock display: timeformat 12|24 (see 'help calendar')"),
    "color":     (cmd_color,     "ANSI color on|off; bare toggles (see 'help formatting')"),
    "config":    (cmd_config,    "display prefs: width, screenreader, map, color, combatgag"),
    "alias":     (cmd_alias,     "list, set, or clear your command shortcuts"),
    "prompt":    (cmd_prompt,    "show or set your vitals prompt after each command"),
    "date":      (cmd_date,      "full calendar date, season, and moon (see 'help calendar')"),
    "bug":       (cmd_bug,       "file a bug report for staff (see 'help bug')"),
    "suggest":   (cmd_suggest,   "file a suggestion for staff (see 'help suggest')"),
    "reports":   (cmd_reports,   "GM: list open bug/idea reports [n] [all]"),
    "resolve":   (cmd_resolve,   "GM: mark a bug/suggestion open/resolved/rejected"),
    "changes":   (cmd_changes,   "recent changelog entries (see 'help changes')"),
    "commands":  (cmd_commands,  "list every verb with a one-line tip"),
    "help":      (cmd_help,      "topic index, or help <topic|command> for one page"),
    "quit":      (cmd_quit,      "disconnect; your body stays as an Echo (see 'help echo')"),
}
