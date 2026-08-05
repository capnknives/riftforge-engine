"""verbs/__init__.py -- classic COMMANDS contribution."""

from classic.verbs.character import cmd_score, cmd_sheet
from classic.verbs.combat import cmd_attack, cmd_cast
from classic.verbs.skills import cmd_skill

CLASSIC_COMMANDS = {
    "score": (cmd_score, "class, abilities, saves, AC, and HP"),
    "sc": (cmd_score, "alias for score"),
    "sheet": (cmd_sheet, "full sheet with L1/L10/L20 progression rows"),
    "attack": (cmd_attack, "instant melee attack (see help combat)"),
    "cast": (cmd_cast, "cast a class spell (see help combat)"),
    "skill": (cmd_skill, "d20 skill check vs DC (see help skills)"),
}
