"""verbs/__init__.py -- basegame's COMMANDS contribution.

Mirrors supers/verbs/__init__.py's role: exports BASEGAME_COMMANDS, merged
into the live COMMANDS dispatch table by commands.py via game_select.py.

look/move/say/get/drop/inventory/help/who all come from ENGINE_COMMANDS
already, and chargen covers path + stat selection -- `score` (below) is
basegame's first own verb. Later stages add path verbs here (cases, treat,
work, patrol, ...) as (handler, help_text) pairs, same rule as SUPERS'
COMMANDS (AGENTS.md rule 11).
"""

from basegame.verbs.drive import cmd_board, cmd_drive, cmd_unboard
from basegame.verbs.character import cmd_score
from basegame.verbs.mail import cmd_mail
from basegame.verbs.weather import cmd_forecast, cmd_weather
from basegame.verbs.work import cmd_work
from basegame.verbs.fly import cmd_descend, cmd_fly
from basegame.verbs.slam import cmd_slam, cmd_throw
from basegame.verbs.justice import cmd_arrest, cmd_payfine, cmd_steal
from basegame.verbs.treat import cmd_treat
from basegame.verbs.shop import cmd_buy, cmd_list, cmd_sell
from basegame.verbs.umbral import cmd_shroud, cmd_unshroud
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
from basegame.verbs.quest import cmd_quest, cmd_quest_accept
from basegame.verbs.press_beat import (
    cmd_abandonstory,
    cmd_copydesk,
    cmd_interview,
    cmd_photograph,
    cmd_photos,
    cmd_reportstory,
    cmd_sellphoto,
    cmd_storyboard,
    cmd_takestory,
)
from basegame.verbs.lodging import cmd_rent_bed, cmd_sleep, cmd_wake
from basegame.verbs.walk import cmd_jog, cmd_run, cmd_walk
from basegame.verbs.loadcombat import cmd_loadcombat
from engine.systems.active_combat_verbs import (
    cmd_aim,
    cmd_autodefense,
    cmd_block,
    cmd_clear_combat_queue,
    cmd_dodge,
    cmd_headbutt,
    cmd_jab,
    cmd_kick,
    cmd_legkick,
    cmd_parry,
    cmd_punch,
    cmd_sweep,
    cmd_uppercut,
)

BASEGAME_COMMANDS = {
    "score": (cmd_score, "your path, stats, Tier, and HP"),
    "sc": (cmd_score, "alias for score"),
    "board": (
        cmd_board,
        "climb into a parked vehicle (see 'help vehicles')",
    ),
    "drive": (
        cmd_drive,
        "steer a boarded vehicle one street exit (driver only)",
    ),
    "unboard": (
        cmd_unboard,
        "climb out of the vehicle you are aboard",
    ),
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
    "photograph": (
        cmd_photograph,
        "Reporter: photograph an exciting scene (see 'help reporter')",
    ),
    "photos": (
        cmd_photos,
        "list held news photos (see 'help reporter')",
    ),
    "sellphoto": (
        cmd_sellphoto,
        "sell a photo at the News Office (see 'help reporter')",
    ),
    "copydesk": (
        cmd_copydesk,
        "on-duty Reporter: desk copy pay (see 'help reporter')",
    ),
    "storyboard": (
        cmd_storyboard,
        "Gazette storyboard at the News Office (see 'help reporter')",
    ),
    "takestory": (
        cmd_takestory,
        "claim a story brief at the News Office (see 'help reporter')",
    ),
    "interview": (
        cmd_interview,
        "interview someone for your open story (see 'help reporter')",
    ),
    "reportstory": (
        cmd_reportstory,
        "file a finished story at the News Office (see 'help reporter')",
    ),
    "abandonstory": (
        cmd_abandonstory,
        "drop your open Gazette story",
    ),
    "fly": (
        cmd_fly,
        "Stellar flight: macro / globe / orbit (see 'help stellar')",
    ),
    "descend": (
        cmd_descend,
        "step down one Stellar flight tier",
    ),
    "list": (
        cmd_list,
        "wares for sale at a vendor counter (see 'help shop')",
    ),
    "buy": (
        cmd_buy,
        "buy from a vendor counter (see 'help shop')",
    ),
    "sell": (
        cmd_sell,
        "sell to a vendor counter (see 'help shop')",
    ),
    "treat": (
        cmd_treat,
        "medic ward care: admit or discharge (see 'help clinic')",
    ),
    "arrest": (
        cmd_arrest,
        "ranger: jail a wanted character at the holding cell (see 'help justice')",
    ),
    "payfine": (
        cmd_payfine,
        "pay outstanding fines (see 'help justice')",
    ),
    "steal": (
        cmd_steal,
        "pickpocket dollars from someone -- marks you wanted (see 'help justice')",
    ),
    "slam": (
        cmd_slam,
        "slam a breachable wall or floor (see 'help breach')",
    ),
    "throw": (
        cmd_throw,
        "throw someone into a breachable surface (see 'help breach')",
    ),
    "shroud": (
        cmd_shroud,
        "Umbral: fade into night (see 'help origins')",
    ),
    "unshroud": (
        cmd_unshroud,
        "Umbral: drop your night shroud (see 'help origins')",
    ),
    "quest": (
        cmd_quest,
        "authored quest log and offers (see 'help quests')",
    ),
    "questaccept": (
        cmd_quest_accept,
        "accept an authored quest by id",
    ),
    "rent": (
        cmd_rent_bed,
        "rent a bunk at the Prairie Inn (see 'help lodging')",
    ),
    "sleep": (
        cmd_sleep,
        "sleep on a bed in lodging (see 'help lodging')",
    ),
    "wake": (
        cmd_wake,
        "wake from sleep",
    ),
    "walk": (
        cmd_walk,
        "paced path to a named place in this zone",
    ),
    "jog": (
        cmd_jog,
        "paced jog to a named place in this zone",
    ),
    "run": (
        cmd_run,
        "paced run to a named place in this zone",
    ),
    # Active (twitch) combat -- see 'help active-combat'. Only resolves in
    # Fight.combat_mode == "active" rooms/NPCs (room.active_combat flag).
    "jab": (cmd_jab, "active combat: quick jab (see 'help active-combat')"),
    "punch": (cmd_punch, "active combat: punch (see 'help active-combat')"),
    "sweep": (cmd_sweep, "active combat: ground sweep (see 'help active-combat')"),
    "uppercut": (
        cmd_uppercut,
        "active combat: heavy uppercut (see 'help active-combat')",
    ),
    "kick": (cmd_kick, "active combat: leg kick (see 'help active-combat')"),
    "legkick": (cmd_legkick, "alias for kick (see 'help active-combat')"),
    "headbutt": (
        cmd_headbutt,
        "active combat: headbutt (see 'help active-combat')",
    ),
    "aim": (
        cmd_aim,
        "active combat: aim <zone>|clear (see 'help active-combat')",
    ),
    "dodge": (
        cmd_dodge,
        "active combat: manual dodge (see 'help active-combat')",
    ),
    "block": (
        cmd_block,
        "active combat: manual block (see 'help active-combat')",
    ),
    "parry": (
        cmd_parry,
        "active combat: manual-only parry (see 'help active-combat')",
    ),
    "--": (
        cmd_clear_combat_queue,
        "clear your pending active-combat queue (see 'help active-combat')",
    ),
    "autodefense": (
        cmd_autodefense,
        "toggle auto-dodge/block on|off (see 'help active-combat')",
    ),
    "loadcombat": (
        cmd_loadcombat,
        "load swing or active_combat backend (see 'help active-combat')",
    ),
}
