"""verbs/__init__.py -- basegame's COMMANDS contribution.

Mirrors supers/verbs/__init__.py's role: exports BASEGAME_COMMANDS, merged
into the live COMMANDS dispatch table by commands.py via game_select.py.

look/move/say/get/drop/inventory/help/who all come from ENGINE_COMMANDS
already, and chargen covers path + stat selection -- `score` (below) is
basegame's first own verb. Later stages add path verbs here (cases, treat,
work, patrol, ...) as (handler, help_text) pairs, same rule as SUPERS'
COMMANDS (AGENTS.md rule 11).
"""

from basegame.verbs.character import cmd_score
from basegame.verbs.mail import cmd_mail

BASEGAME_COMMANDS = {
    "score": (cmd_score, "your path, stats, Tier, and HP"),
    "sc": (cmd_score, "alias for score"),
    "mail": (
        cmd_mail,
        "letters at the Post Office (see 'help mail')",
    ),
}
