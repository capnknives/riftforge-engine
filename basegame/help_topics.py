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

Every resident is an ordinary human. Your path is the work you do:

{_paths_lines}

Pick your path at character creation; this reference build does not
support changing it later. See help basegame for what else is here.
""",
    "basegame": """Notbigville, Kansas — RiftForge public demo

This is the engine's demo game: regional weather, America overland travel,
Storm Watch storm chases, and optional Stellar flight. Type weather,
exit from Main Street to walk the atlas, help tornado-hunter for the desk
loop, help stellar for flight tiers.
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
    "stellar": """Notbigville -- Stellar flight demo

Choose Stellar at chargen (yellow-sun path). Then:
  1. fly from an outdoor room (Observatory knoll works)
  2. fly again to reach the brass globe layer; n/s/e/w to bank
  3. fly again for low orbit; descend steps back down

See also: help travel
""",
}

HELP_CATEGORIES = [
    ("Basegame", ["basegame", "paths", "score", "mail", "weather", "travel", "tornado-hunter", "stellar"]),
]
