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
from basegame.verbs.weather import cmd_forecast, cmd_weather
from basegame.verbs.work import cmd_work
from basegame.verbs.fly import cmd_descend, cmd_fly
from basegame.verbs.storm_watch import (
    cmd_abandonchase,
    cmd_chaseboard,
    cmd_probe,
    cmd_radar,
    cmd_reportchase,
    cmd_research,
    cmd_takechase,
    cmd_track_chase,
)

BASEGAME_COMMANDS = {
    "score": (cmd_score, "your path, stats, Tier, and HP"),
    "sc": (cmd_score, "alias for score"),
    "mail": (
        cmd_mail,
        "letters at the Post Office (see 'help mail')",
    ),
    "weather": (
        cmd_weather,
        "regional CONUS sky + tornado warnings (see 'help weather')",
    ),
    "forecast": (
        cmd_forecast,
        "short regional forecast (see 'help weather')",
    ),
    "work": (
        cmd_work,
        "clock in at a room job site (see 'help tornado-hunter')",
    ),
    "research": (
        cmd_research,
        "on-duty Tornado Hunter: log desk research (see 'help tornado-hunter')",
    ),
    "radar": (
        cmd_radar,
        "on-duty Tornado Hunter: list watches/funnels (see 'help tornado-hunter')",
    ),
    "chaseboard": (
        cmd_chaseboard,
        "Storm Watch chase board (see 'help tornado-hunter')",
    ),
    "takechase": (
        cmd_takechase,
        "accept a storm chase at Storm Watch (see 'help tornado-hunter')",
    ),
    "track": (
        cmd_track_chase,
        "soft lead toward chase target (use 'track chase')",
    ),
    "probe": (
        cmd_probe,
        "collect chase data near the target cell (see 'help tornado-hunter')",
    ),
    "reportchase": (
        cmd_reportchase,
        "turn in chase data at Storm Watch (see 'help tornado-hunter')",
    ),
    "abandonchase": (
        cmd_abandonchase,
        "drop your open storm chase",
    ),
    "fly": (
        cmd_fly,
        "Stellar flight: macro / globe / orbit (see 'help stellar')",
    ),
    "descend": (
        cmd_descend,
        "step down one Stellar flight tier",
    ),
}
