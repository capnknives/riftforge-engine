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
    "weather": """
weather -- regional CONUS sky (look, dial, forecast)

How you play
  1. Stand outdoors (town street or America Overland) and look -- [WX] shows
     condition, temp, and wind for your climate region
  2. weather              full regional snapshot + tornado warnings
  3. forecast             short outlook line
  4. radio tune to WX     NOAA-style rotating bulletin (regional)
  5. Leave a dial on      town WX news cuts in at the top of the hour every four game-hours (Discord mirrors that slot)

Hard to see
  Rain, storm, snow, and nearby tornadoes make outdoor look harder.
  You always get a [WX] vision line when the sky is bad. Sometimes look whites out -- you keep the room name and weather, but people, items, and exits vanish behind "you can't see through …" until you look again.
  Cars soften that; sturdy indoor rooms do not white out (roof dampen only). Walking still works by direction even when you cannot see.

Tornadoes
  Rare severe events move across the atlas. [WARNING] lines escalate when a funnel is near. Seek sturdy shelter (Storm Watch Office, bunkers, clinics). Vehicles are NOT storm cellars. Strong funnels can injure people who are not strong enough outdoors -- clinic, never loot.

Seasonal water
  Winter adds ice-edge look lines on outdoor lakes and water rooms (Lebanon lake, Stull shore, overland lake cells). You can still cross -- the game may flash
  [ALERT] tells on the ice (flavor only in v1; no cold damage).

Staff: gm weather / gm weather tornado (help gm).
See also: help tornado-hunter | help radio | help calendar | help gm
""",
    "travel": """Notbigville -- America overland travel

How you play:
  1. exit from Main Street (NB00001) to step onto the 78x18 atlas
  2. n/s/e/w to cross macro tiles; micro wilderness is 10x10 per tile
  3. enter notbigville (or enter <alias>) at a pocket cell to return to town

Vehicles are not in this demo build -- on-foot only.
""",
    "tornado-hunter": """
tornado-hunter -- Storm Watch desk job + chase board

Desk (boring pay): clock in at Storm Watch Office (east of Main Street N7).

How you play (desk)
  1. Go to Storm Watch Office
  2. work as tornado_hunter   (or work if that is your favorite)
  3. research                 log regional normals for small dollars
  4. radar                    list watches and live tornado tracks

Board (fun chase): anyone at the office can pick up a chase -- on-duty hunters get a turn-in bonus.

How you play (chase)
  1. chaseboard / takechase   accept a live funnel or storm-cell probe
  2. track chase              soft lead toward the target atlas cell
  3. probe                    outdoors within one cell of the target
  4. reportchase              turn in data at Storm Watch for dollars
  5. abandonchase             drop the job with no pay
See also: help weather | help work | help radio
""",
    "reporter": """
reporter -- Mundane Background + Gazette desk gig

Humans: pick with path reporter -- Spirit, commit / settle, and Mutation Pool still apply (help human).
Awakened cover: path background reporter on Monster / Celestial -- craft spends Origin fuel, not Spirit (help path | help fuel).

How you play (field)
  1. photograph       shoot fights, crowds, crime scenes, or sky drama
  2. photos           list held shots on your roll
  3. sellphoto        cash a print at the Lebanon Gazette (west of the Library)

How you play (stories)
  1. storyboard       read tips at the Gazette news desk
  2. takestory        claim a brief (interviews + optional photo)
  3. interview <name> quote someone in the room for your open brief
  4. reportstory      file finished copy for dollars
  5. abandonstory     drop a brief with no pay

Desk gig (anyone with the job)
  1. work as news_reporter at the Gazette
  2. copydesk         small dollars while on duty (like Storm Watch research)

Detective casework can make scenes more photogenic; the Sheriff board and the Gazette are separate loops.

Top stats lean PRE / FOC / FIN.
See also: help detective | help cases | help work | help jobs-hub
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
    "origins": """
origins -- what kind of person you are

Soldier, priest, monster, machine, god -- Lebanon is full of people who used to be something else. Your Origin is which kind of something-else you are; your Path is what that looks like day to day.

Your Origin is the big family you belong to. Under that sits a Path -- the game may call it a Background, Lineage, Mantle, Strain, Tether, Core, or Bloodline depending on the family. Path is the concrete role you play day to day.

How you play
  1. At chargen: mortal or Awakened, then Background or Path (set once)
  2. Shape your look, then starting clothes (skip = road casual; help clothing)
  3. Humans: home state on your ID (skip = Kansas), then a registration address
  4. Mutant, Constructed, and Alien are not Awakened picks at create (help paths)
  5. Awakened Monster / Celestial: optional mortal cover (or Skip)
  6. Optional: seed personality traits + traveler reach (help personality)
  7. path                 see what you have; Humans pick path <id> once
  8. path background      Monster / Celestial: list or set a mortal cover
  9. help paths           browse every live Origin and Path in the catalog
  10. help <your Path>     open that hub (vampire, hunter, angel, …)
  11. train / spar         grow body stats (help training)
  12. learn <id>           open Disciplines you qualify for

Live chargen families (today)
  Human Backgrounds     help human — detective, soldier, hunter, witch, …
  Monster Lineages      help monster — vampire, shifter, ghost, …
  Celestial Mantles     help angel | help demon | help reaper
  Divine Faith Gods     help divine | help god -- episode pantheon worship
  Cosmic Tethers        help elemental | help eldritch | help void-touched

  Type help paths for the full live catalog (built from origins.json).
  Mutant, Constructed, Alien, and Creation are not player picks at create.

Each Path names three preferred primaries (top stats) -- shown on path and on help paths. Hunter / Occultist / Slayer edges line up with those.
Echo training uses them for default gym drills (all six primaries have a solo activity). Chargen or kit tools assign non-Human Paths; bare path always shows what you have.

Mortal cover (Monster / Celestial)
  You keep your Lineage or Mantle. A Mundane Background cover adds the mortal kit (detective, witch, …). Spirit stays Human-only -- cover craft spends your Lineage fuel instead (Ghost Presence, Vampire Blood, Angel Grace, …; check fuel / score). nervework refills that same tank. Type help path for the menu, blocks (no Hunter / Slayer cover), and path background <id>.

Disciplines (learnable powers)
  learn <id>        open a Discipline you qualify for disciplines       what you know and what you can still open
  Some Disciplines stay locked until your Origin or Path matches
  (for example God rites, Hunter arts, Occultist hellcraft).

Useful commands
  path              show Path; Humans pick a Background once
  path background   Monster / Celestial mortal cover
  score             Origin / Path / cover on your sheet
  alignment         reputation axis good / neutral / evil (help reputation)
See also: help paths | help newbie | help human | help monster
""",
    "shop": """Notbigville -- General Store shopping

How you play:
  1. Walk to the General Store (east from Storm Watch, or west from Post Office).
  2. list              see wares and prices
  3. buy <item>        purchase with cash on hand
  4. sell <item>       sell something you carry (half the shelf price)

You need dollars in your wallet to buy. See help score for your cash line.
""",
    "clinic": """
clinic -- alias for hospitalize / Lebanon Hospital help
See also: help hospital
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
    "breach": """
breach -- Leviathan Bone Gate OR Soldier Master combat breach

Usage: breach

Two Paths share this verb (same word, different gates):

  Leviathan Master+: Stand in the Bone Gate with a bone-white seal key (canonical or a fraying mendkey craft) and Master+ Devouring.
  Costs heavy fuel. Success clears the Purgatory bind so plane exits work; the fraying key crumbles.
  Paying the Ledger never frees a Leviathan.

  Human Soldier Master: committed combat breach buff (mundane ladder).
  See help soldier.
  Requires Path soldier and Adept+ rank on the Soldier ladder.
See also: help leviathan | help purgatory | help soldier
""",
    "active-combat": """
combat -- how fighting works (hub)

Fights in Lebanon get real, fast -- a swung fist, a drawn blade, a name shouted across the bar before the first punch even lands. The line between a bruising and a burial is thin, and it runs right through the finishing blow you choose to throw.

Core four (learn these first)
  Every scrap runs on auto-swings about every three seconds once you have a target. Four verbs shape the tempo:

  attack <name>   engage and keep swinging (real fight or spar <name> for practice).
  guard           spend Momentum to block the next blow that hits you (not your own punch).
  press           spend Momentum for a heavy all-in swing this beat.
  feint           sell a false line; feint_exposed sticks on the mark for the next swing.

  Type guard, press, or feint on their own. Costs and combo priming: help tactics.

The short version
  1. spar <name> -- practice fight. Nobody dies; health snaps back.
  2. attack <name> -- the real thing. Always knocks a foe out first ([KO] in the room).
  3. While they are down (about two minutes), attack <name> again sends them to Lebanon Hospital, or attack <name> lethal is a killing finish where the rules allow it (you confirm twice). Good characters only go lethal against a consenting good player -- every other fight (NPCs, Echoes, idles) ends at the hospital. Wilderness hostiles and evil-vs-evil fights can already finish for keeps (help wilderness | help hospital). Players: autokill on finishes disposable dungeon fodder automatically (help autokill).

How you play (full loop)
  1. attack <name> -- pick your target (one foe at a time). attack mob hits the first hostile NPC in the room.
  2. guard / press / feint -- spend Momentum when you have it (help tactics).
  3. stance -- aggressive / defensive / balanced (help stance).
  4. style / engagement / aim -- Martial|Weapons|Magic voice, scrap distance ([RANGE]), hit location (help style | help engagement | help aim).
  5. protect <name> -- cover an ally instead of swinging.
  6. equip / unequip / flee -- gear up (weapon voice + Weapon Mastery) or leave.

About every three seconds everyone with a target swings once. You and a foe both aiming at each other is an exchange -- you act, they answer. Extra people piling onto the same target adds pressure (help protect | help crowd-combat). Walk into a nest or dungeon room with several hostiles and they all lunge; you still swing at one focus at a time.

Initiative and opening shots
  Finesse sets Initiative on score -- higher FIN (and fast gear) usually wins the beat order.
  When you attack someone who was not already fighting you: ranged guns and ranged magic can snap-shot immediately (the foe skips their first swing). Melee needs hide/sneak or true invisibility -- otherwise you wait for the next beat. See help stealth.

Ending it
  disengage / flee breaks off without leaving the room (help disengage). 0 HP always knocks someone out, never kills outright -- a finish command afterward sends them to the clinic or ends it for good (help death | help hospital). Echoes are never looted for logging off. Immortal foes still need the right method on the finish (stake / decap / folklore steel / help celestial-steel). Exhausted fighters must rest first (help rest).

Practice first
  Town Gym or Spar Gym -- spar the training dummy, or takequest combat_drill for a slow guided lesson (help combat-drill).

Growing stronger
  Real fights pay training progress while you are under softcap, then banked Growth toward the next Tier (help breaking | help training). Spar and gym drills still matter for safe reps.

Going deeper (once the core four click)
  counter / riposte -- turn their swing into your opening (loadout-dependent; help counter).
  unleash / weave -- ultimates and synergy (help unleash | help synergy).
  Path autos -- bite / judgment / maul / hurl / … (help combat-cooldowns).
  condition / cond -- lifeforce band + injuries on a foe.
  radiate (alias aura) -- Ascendant+ Presence cloak.
  Momentum -- builds from winning exchanges (help momentum | help clash).
  Reactions -- miss / dodge / parry / block / crit / hit each swing (help reactions).
  combatnumbers / combatgag / fightlog -- display prefs (help formatting).
  Primaries and fight math -- help stats | help mastery | score.
See also: help sparring | help tactics | help counter | help stats
""",
    "dig": """
room -- GM dig / link / rset rooms (persists map JSON)

In-game write half for content/maps/*.json and content/zones/*.json.
Area Studio remains the preferred full builder (grids, stamps, packages); use these verbs for quick live edits while standing in the world.

Staff address rooms by **ROOM NAME** and **VNUM** only (e.g. MT00002).
Internal keys stay under the hood for exits / save until Phase 3 -- you should not need to type them for link, goto, or set home.

How you play
  1. Stand in a room (gm mode optional but recommended for goto)
  2. room                 inspect ROOM NAME, VNUM, map file, exits, flags
  3. room dig <dir> <ROOM NAME> create a new room that way and link both ways (rewrites map/zone JSON in-process -- does NOT trigger copyover;
      watch_and_run skips content/maps and content/zones)
     Dig auto-qualifies storage keys; set ROOM NAME to the structured shape City - Main - Sub (e.g. Lebanon - Apartments - Floor C).
     Graph stores a map-scoped internal key so it does not collide with Wastes Ash Court. See help build-maps / docs/CONTENT_AUTHORING.md.
  4. room link <dir> <VNUM|ROOM NAME> link to an existing room (both ways when known). Example: room link east MT00002 Shared ROOM NAMES need the VNUM. Refuses to overwrite a destination's reverse exit that already points elsewhere (protects other maps).
  5. room unlink <dir>    remove that exit (+ reverse if it points back)
  6. room rset title …    ROOM NAME (shared names OK; use VNUM to target)
  7. room rset description|zone|area_type|spawn_nest|spawn_hub <text>
  8. room rset jobs|resources <id,id,…>   comma lists (clear to remove)
  9. room rset <flag> on|off   full list: help rset (or bare room rset)
 10. room create <ROOM NAME>   disconnected room in this map
 11. room help / room rset ?   reprint fields + every flag live

Claimable home (help lodging)
  Interior: room rset is_house on
  Street porch / apartment unit: room rset private_home on Then players type claim. Hotels stay rent -- not is_house.

Grid cells: dig adds a hand room + grid.portals entry (cells are not in rooms[]). Settlement pocket enter/exit stays Studio pockets[] -- do not dig in/out for those gateways.

Persists across copyover/restart. Protected from auto-deploy overlays
(help content). Prefer Area Studio for large areas.
See also: help rset | help lodging | help populate | help build-maps
""",
    "phone": """
phone -- physical handsets, numbers, and plane-local calls

Calls go to a **phone number on an item**, not a character name.
call dean only works after you phone save dean 555-0142 (alias → number on *your* phonebook).

How you play
  1. Buy a flip phone (Ash Garage in Lebanon, or the Lawrence gas station) or find a payphone (Lebanon Square).
  2. phone                 -- status: your number(s), call, contacts
  3. phone number          -- show handset number(s)
  4. dial 555-0142         -- ring that handset (same plane only)
     call dean             -- dial a saved alias
  5. answer / hangup       -- pick up or end
  6. phone say hello       -- private line; room sees you talk into a phone
  7. phone save dean 555-0142
     phone forget dean
     phone contacts
  8. dial WKNZ <text>      -- call-in queue (no radio tune needed)
     phone request <song>  -- song request queue
  9. phone ask group|food|water|help -- when an Echo answers (not voicemail)

Payphones
  Cost 1 dollars per outbound call. Usable only if the room has a payphone item or the room description mentions a payphone. Outbound only.

Planes
  Signal stays on the plane you are on (Earth phone cannot ring Hell).

Cadence
  Offline Echoes and town NPCs on Earth earn toward a flip phone (gig work), then walk to an in-town phone shelf (Ash Garage in Lebanon; gas station in Lawrence) and buy one -- same buy verb players use.
  Towns without a shelf do not soft-lock on endless grocery gigs.
  Critical hunger/thirst still outranks shopping.
  On-shift desk clerks stay at their workplace before the phone grind.
  Hunters on a haunt / hunt lead may call a close-tie Echo partner
  (sibling, ashkin, …) with phone ask group so the road trip waits for the meetup -- same ask path players use.
Echoes
  Offline bodies can answer unless echo voicemail on. Live players must type answer. See help echo.

Screenreader: lines use [PHONE] / [CALL] tags (never color alone).
See also: help echo | help radio | help station | help sparaccept
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
