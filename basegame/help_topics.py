"""help_topics.py -- basegame's HELP_TOPICS / HELP_CATEGORIES.

Registered via engine.hooks.set_help in
basegame/bootstrap.py.register_all_hooks. Mirrors root help_topics.py's
shape (topic id -> page string; categories -> ordered (heading, [topic
ids]) list) but scoped to just what basegame ships -- AGENTS.md rule 11
("ship help with the feature") applies to basegame verbs the same as
SUPERS ones.
"""

from basegame.chargen import PATHS, PATH_ORDER

_paths_lines = "\n".join(f"  {path_id} -- {PATHS[path_id]}" for path_id in PATH_ORDER)

HELP_TOPICS = {
    "paths": f"""RiftForge reference town -- paths

Every resident starts Mundane by default. Your path is the work you do:

{_paths_lines}

Pick your path at character creation; this reference build does not
support changing it later. See help origins for Mundane vs Alien, and
help basegame for what else is here.
""",
    "basegame": """Notbigville, Kansas — RiftForge public demo

This is the engine's demo game: regional weather, America overland travel,
Storm Watch storm chases, and optional Alien Bloodlines (Stellar flight /
Umbral shroud). Type weather, exit from Main Street to walk the atlas,
help tornado-hunter for the desk loop, help origins for Bloodlines,
help stellar for flight tiers.
""",
    "score": """RiftForge reference town -- score

Shows your Path, your six primary stats (the same POW/VIT/FOC/FIN/RES/PRE
spine every game on the engine shares), your Tier, and your HP. `sc` is a
shorthand alias.
""",
    "mail": """RiftForge reference town -- mail

How you play:
  1. Walk to the Post Office (east from General Store).
  2. Type bare mail to list your inbox.
  3. mail send <name> <text> to leave a letter for someone in the world.
  4. mail read <n> / mail discard <n|all> to manage letters.

Letters queue on the recipient even when they are offline.
""",
    "weather": """Notbigville -- regional weather

How you play:
  1. weather          full regional snapshot + tornado warnings
  2. forecast         short outlook for your area
  3. exit from Main Street to walk the America overland atlas
  4. enter notbigville from the Kansas cell when driving the plains

Rain, storm, and nearby tornadoes make outdoor look harder.
Seek the Community Shelter cellar when warnings sound.

See also: help tornado-hunter | help travel
""",
    "travel": """Notbigville -- America overland travel

How you play:
  1. exit from Main Street (NB00001) to step onto the 78x18 atlas
  2. n/s/e/w to cross macro tiles; micro wilderness is 10x10 per tile
  3. enter notbigville (or enter <alias>) at a pocket cell to return to town

Vehicles are not in this demo build -- on-foot only.
""",
    "tornado-hunter": """Notbigville -- Storm Watch desk + chase board

How you play:
  1. work as tornado_hunter at Storm Watch Office (east of Main Street)
  2. research / radar while on duty for dollars + sky intel
  3. chaseboard / takechase / track chase / probe / reportchase

See also: help weather | help travel
""",
    "reporter": """Notbigville -- Gazette reporter path + desk gig

How you play (field):
  1. photograph          shoot fights, crowds, or sky drama
  2. photos              list held shots
  3. sellphoto           cash a print at the News Office (north of Post Office)

How you play (stories):
  1. storyboard / takestory at the News Office
  2. interview <name>  quote locals while your brief is open
  3. reportstory       file finished copy for dollars
  4. abandonstory      drop a brief

Desk gig: work as news_reporter, then copydesk for small dollars.

See also: help work | help paths
""",
    "stellar": """Notbigville -- Stellar flight demo

Pick Alien → Stellar at chargen (yellow-sun Bloodline). Then:
  1. fly from an outdoor room (Observatory knoll works)
  2. fly again to reach the brass globe layer; n/s/e/w to bank
  3. fly again for low orbit; descend steps back down

See also: help origins | help travel
""",
    "origins": """Notbigville -- origins (Mundane / Alien)

How you play:
  1. At chargen, pick Mundane (default -- your path is the work you do)
     or Alien (extraterrestrial Bloodline).
  2. Alien then picks Stellar or Umbral:
       - Stellar: yellow-sun flight -- see help stellar (fly / descend).
       - Umbral: night shroud -- type shroud at dusk or night to fade
         from ordinary look / presence; unshroud to step back into sight.
         Shroud drains Umbral Charge each heartbeat and collapses at 0.
  3. Daylight blocks shroud -- wait for dusk or night on the calendar.

See also: help stellar | help paths
""",
    "shop": """Notbigville -- General Store shopping

How you play:
  1. Walk to the General Store (east from Storm Watch, or west from Post Office).
  2. list              see wares and prices
  3. buy <item>        purchase with cash on hand
  4. sell <item>       sell something you carry (half the shelf price)

You need dollars in your wallet to buy. See help score for your cash line.
""",
    "clinic": """Notbigville -- clinic / KO recovery

How you play:
  1. At 0 HP you are knocked down (downed) instead of deleted.
  2. After a short delay you are carried to the Clinic ward (NB00005).
  3. Medics: walk to the ward and treat <name> to admit someone faster.
  4. treat with no name signs yourself out when you are stable.

See also: help paths (medic path)
""",
    "justice": """Notbigville -- wanted / fines / jail

How you play:
  1. steal <name> pickpockets someone in the room -- and marks you wanted.
  2. Rangers: walk the wanted person to the holding cell (south from
     Highway Shoulder), then arrest <name> to jail them.
  3. payfine clears outstanding fines from anywhere.
  4. While jailed, exits from the cell are blocked until time served.

See also: help paths (ranger path)
""",
    "breach": """Notbigville -- slam / throw wall breach

How you play:
  1. Walk to the Saloon (east from the Post Office).
  2. slam [wall]       chip the saloon wall (8 HP); wreck it to burst into the alley.
  3. throw <name> [wall]   hurl someone into the same surface.

Layout-stamped rooms use the engine breach kit with persisted wall HP.

See also: help paths
""",
    "active-combat": """Notbigville -- active (twitch) combat

Combat backends (game-wide default):
  loadcombat list              show swing vs active_combat + current default
  loadcombat swing             round-based swing combat (mundane / martial_arts)
  loadcombat active_combat     timestamp-buffered twitch combat

Arena rooms (e.g. Grain Elevator Shed) force active combat even when the
game default is swing.

How you play (active combat):
  1. jab / punch / kick / sweep / uppercut / headbutt <name>
     Queues a strike. Balance recovers between swings (FIN helps).
  2. aim <zone>        spend Equilibrium to aim the next hit (head, torso, ...)
  3. dodge / block / parry [name]
     Manual defense during the telegraph window -- better than auto.
     Parry is manual-only (high risk / high reward).
  4. autodefense dodge|block on|off
     Turn off a type to train the other (auto never picks parry).
  5. --                clear your pending strike/aim queue

You type instantly; the heartbeat resolves queued actions by timestamp.
Auto-dodge/block still fire if you never type a defense.

See also: help score
""",
}

HELP_CATEGORIES = [
    ("Basegame", ["basegame", "paths", "origins", "score", "mail", "shop", "clinic", "justice", "breach", "active-combat", "weather", "travel", "tornado-hunter", "reporter", "stellar"]),
]
