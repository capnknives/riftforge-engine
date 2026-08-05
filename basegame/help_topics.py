"""help_topics.py -- basegame's HELP_TOPICS / HELP_CATEGORIES.

Registered via engine.hooks.set_help in
basegame/bootstrap.py.register_all_hooks. Mirrors root help_topics.py's
shape (topic id -> page string; categories -> ordered (heading, [topic
ids]) list) but scoped to just what basegame ships -- AGENTS.md rule 11
("ship help with the feature") applies to basegame verbs the same as
SUPERS ones.

Engine-generic verbs (``bug``, ``hedit``, …) ship topic pages from
``help_engine_topics.py`` so ``RIFTFORGE_GAME=basegame`` does not depend
on SUPERS for ``help <topic>``. Detail: ``docs/ENGINE_CONSUMER.md``.
"""

from basegame.chargen import PATHS, PATH_ORDER
from basegame.help_engine_topics import (
    HELP_ENGINE_CATEGORIES,
    HELP_ENGINE_TOPICS,
)

_paths_lines = "\n".join(f"  {path_id} -- {PATHS[path_id]}" for path_id in PATH_ORDER)

_HELP_TOPICS_BASEGAME = {
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

How you play:
  1. score              compact sheet (path, stats, HP, urgent needs/injuries)
  2. score vitals       lifeforce / HP focus
  3. score combat       Balance, Equilibrium, aim zone, per-limb injuries
  4. score needs        hunger and thirst meters
  5. score full         verbose whole sheet

Regional injuries from active combat persist across reboots. Disabled limbs
need clinic care. See help active-combat for strikes, aim, and grapple.
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
  1. hover              lift off inside your current room (works indoors)
  2. fly from an outdoor room (Observatory knoll works) -- climbs map layers
  3. fly again to reach the brass globe layer; n/s/e/w to bank
  4. fly again for low orbit; descend steps back down

hover = airborne in the room you are in (active combat sweeps miss you).
fly = Stellar map-layer ascent (macro → globe → orbit). Descend lands or
steps down one layer.

See also: help origins | help travel | help active-combat
""",
    "origins": """Notbigville -- origins (Mundane / Alien)

How you play:
  1. At chargen, pick Mundane (default -- your path is the work you do)
     or Alien (extraterrestrial Bloodline).
  2. Alien then picks Stellar or Umbral:
       - Stellar: yellow-sun flight -- see help stellar (hover / fly / descend).
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
  1. skills              list every strike, grapple move, and defense verb
  2. jab / punch / kick / uppercut / sweep / headbutt / grab <name>
     Your first strike before a fight starts launches immediately.
     Later strikes queue (Balance recovers between swings -- FIN helps).
  3. grab <name>         seize them; then throw <dir> or slam [surface]
  4. throw <direction>   hurl held foe (wall = extra damage; open exit = toss)
  5. slam [surface]      slam held foe into a hard surface
  6. When your fight target flees, is thrown, or is slammed into another
     room, you auto-chase them (bare follow stops trailing).
  Firearms (demo sidearm):
  7. reload              fill the magazine
  8. load                chamber one round
  9. aim <name> [zone]   sight on target (head, torso, ...); aim clear to drop
 10. fire                discharge at your sight line (after load + aim)
  Defense:
 11. dodge / block / parry [name]
     Manual defense during the telegraph window -- better than auto.
     Parry is manual-only (high risk / high reward).
 12. autodefense dodge|block on|off
     Turn off a type to train the other (auto never picks parry).
 13. --                clear your pending action queue
 14. hover / descend   lift off inside the room (sweeps miss) or land

You type instantly; after the opener, queued actions resolve by timestamp.
Auto-dodge/block still fire if you never type a defense.

See also: help breach, help score
""",
    "dig": """Notbigville -- runtime room carving (map_store demo)

How you play:
  1. Stand in a layout-stamped room (Saloon works).
  2. dig <direction> <ROOM NAME…>   carve and link a new room live.
  3. walk the new exit to confirm the live graph updated.

This writes the owning zone JSON on disk (builder demo for engine OLC).
""",
    "phone": """Notbigville -- payphone + dial demo

How you play:
  1. Walk to the Post Office (east from General Store).
  2. Keep a dollar in your wallet for the coin payphone.
  3. dial operator        ring the desk clerk (alias for their handset).
  4. answer / hangup      when someone rings your phone.

The Post Office description mentions the payphone; Operator NPC seeds at boot.
""",
    "appearance": """Notbigville -- appearance slots demo

How you play:
  1. appearance                 list your slots
  2. appearance <slot>          list valid option ids
  3. appearance <slot> <id>       set hair/eyes/etc. and rebuild look self

Fill every core slot, then look self to see the assembled description.
""",
    "relationships": """Notbigville -- relationship tags demo

How you play:
  1. relate                     list your one-sided tags
  2. relate <name> friend       tag someone in the world
  3. friend <name>              shortcut for the same
  4. relate clear <name>        drop your tag toward them

Tags are one-sided; reciprocity is flavor only.
""",
    "personas": """Notbigville -- persona traits demo

The Post Office Operator carries the ``chatty`` trait from personas.json.

How you play:
  1. Walk to the Post Office.
  2. greet Operator             hear trait-colored flavor text.
""",
}

# Engine-generic pages (bug, hedit, …) merge on top so a game never has to
# duplicate them in its own topic dict unless it wants to override flavor.
HELP_TOPICS = {**_HELP_TOPICS_BASEGAME, **HELP_ENGINE_TOPICS}

HELP_CATEGORIES = [
    ("Basegame", [
        "basegame", "paths", "origins", "score", "mail", "shop", "clinic",
        "justice", "breach", "active-combat", "weather", "travel",
        "tornado-hunter", "reporter", "stellar", "dig", "phone",
        "appearance", "relationships", "personas",
    ]),
    *HELP_ENGINE_CATEGORIES,
]
