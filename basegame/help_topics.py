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
    "score": """score -- compact sheet; score combat/vitals/lineage/full for detail

Compact sheet; score combat/vitals/lineage/full for detail.
Type score to do it. help personality is the full article.

How you play
  1. score
  2. help personality -- the full loop

See also: help personality | help echo | help stats | help needs | help tiers
""",
    "mail": """
mail -- letters and consignment via the Post Office

Send from a mail room (the town Post Office on Civic Row).
Text letters and physical consignments use different verbs.

How you play
  1. mail

  2. mail -- list your inbox
  3. mail read <n> -- read letter number n
  4. mail discard <n|all> -- throw away one letter or all
  5. mail send <name> <text> -- queue a letter (also: mail <name> <text>)
  6. mail long send <name> <text> -- longer letter (up to 4000 characters)
  7. mail ship <name> <item> -- consign rare salvage to Curio Lux (traveling salesman)

Curio Lux rotates between Notbigville and the Rift Nexus charter strip. When he is on your square, list / shop / buy from his wagon. Scavengers can mail him rare blades and other odd pieces, and pawn desks forward rare salvage, magi reagents, and hunter combat kit to the same wagon when you sell. Full dealer loop: help curio.

Recipients can be online or Echoes. Login reminds you if mail waits.
Cap 30 letters; paste long prose in each body (mail long is the same ceiling).
See also: help scavenge | help journal | help cadence | help curio | help oocmail
""",
    "weather": """weather -- regional CONUS sky (look, dial, forecast)

How you play
  1. Stand outdoors (town street or America Overland) and look -- [WX] shows condition, temp, and wind for your climate region
  2. weather -- full regional snapshot + tornado warnings
  3. forecast -- short outlook line
  4. radio tune to WX -- NOAA-style rotating bulletin (regional)

What it does
  0 Spirit. Reads the climate band you are standing in, not the whole country.

2. This page covers hard-to-see look, tornadoes, and ice

Hard to see
  Rain, storms, nearby tornadoes, and blowing snow (snow plus strong wind) make outdoor look harder. Rain is overlay only -- it never whites out. Ordinary snow still shows on look -- it does not wipe the street. Gusty snow may add a hard-to-see [WX] line. In a true blow, look can white out: you keep the room name and weather, but people, items, and exits vanish behind "you can't see through …" until you look again. Cars soften that; sturdy indoor rooms do not white out (roof dampen only). Walking still works by direction even when you cannot see.

weather reports the climate band you are standing in, not the whole country. Kansas snow does not mean Miami is snowing.

Tornadoes
  Rare severe events move across the atlas. [WARNING] lines escalate when a funnel is near. Seek sturdy shelter (Storm Watch Office, bunkers, clinics). Vehicles are NOT storm cellars. Strong funnels can injure people who are not strong enough outdoors -- clinic, never loot.

Seasonal water
  Winter adds ice-edge look lines on outdoor lakes and water rooms. You can still cross -- the game may flash [ALERT] tells on the ice (flavor only in v1; no cold damage).
See also: help tornado-hunter | help radio | help calendar
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

How you play
  1. Go to Storm Watch Office
  2. work as tornado_hunter   (or work if that is your favorite)
  3. research                 log regional normals for small dollars
  4. radar                    list watches and live tornado tracks

How you play
  1. chaseboard take         accept a live funnel or storm-cell probe (also: takechase)
  2. track chase              soft lead toward the target atlas cell
  3. probe                    outdoors within one cell of the target
  4. chaseboard report        turn in data at Storm Watch for dollars (also: reportchase)
  5. chaseboard abandon       drop the job with no pay (also: abandonchase)

Board (fun chase): anyone at the office can pick up a chase -- on-duty hunters get a turn-in bonus.

See also: help weather | help work | help radio
""",
    "reporter": """
reporter -- Mundane Background + Gazette desk gig

Humans: pick with path reporter -- Spirit, commit / settle, and Mutation Pool still apply (help human).
Awakened cover: path background reporter on Monster / Celestial -- craft spends Origin fuel, not Spirit (help path | help fuel).

How you play
  1. photograph       shoot fights, crowds, crime scenes, or sky drama (may tag [PHOTO] hunter tells on stills -- not masquerade heat)
  2. photos           list held shots on your roll
  3. sellphoto        cash a print at the Notbigville News Office desk

How you play
  1. storyboard       read tips at the Gazette news desk
  2. storyboard take  claim a brief (interviews + optional photo; also: takestory)
  3. interview <name> quote someone in the room for your open brief
  4. storyboard report file finished copy for dollars (also: reportstory)
  5. storyboard abandon drop a brief with no pay (also: abandonstory)

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
    "origins": """origins -- what kind of person you are

Soldier, priest, monster, machine, god -- the prairie is full of people who used to be something else. Your Origin is which kind of something-else you are; your Path is what that looks like day to day.

How you play
  1. At chargen: mortal or Awakened, then Background or Path (set once)
  2. Mortal Humans: pick one physical school (boxing / wrestling / martial arts) or none
  3. Shape your look, then starting clothes (skip = road casual; help clothing)
  4. Humans: home state on your ID (skip = Kansas), then a registration address
  5. Mutant, Constructed, and Alien are not Awakened picks at create (help paths)
  6. Awakened Monster / Celestial: optional mortal cover (or Skip); optional physical school cover
  7. Optional: seed personality traits + traveler reach (help personality)
  8. path                 see what you have; Humans pick path <id> once
  9. path background      Monster / Celestial: list or set a mortal cover
  10. help paths           browse every live Origin and Path in the catalog
  11. help <your Path>     open that hub (vampire, hunter, angel, …)
  12. train / spar         grow body stats (help training)
  13. learn <id>           open Disciplines you qualify for

Second character? account characters lists alts; create opens chargen again. help paths and help <your Path> compare Origins before you pick.

Your Origin is the big family you belong to. Under that sits a Path -- the game may call it a Background, Lineage, Mantle, Strain, Tether, Core, or Bloodline depending on the family. Path is the concrete role you play day to day.

Live chargen families (today)
  Human Backgrounds     help human — detective, soldier, hunter, witch, …
  Monster Lineages      help monster — vampire, shifter, ghost, …
  Celestial Mantles     help angel | help demon | help reaper
  Divine Faith Gods     help divine | help god -- folklore pantheon worship
  Cosmic Tethers        help elemental | help eldritch | help void_touched

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

Live chargen families (today)
  Human Backgrounds     help human — detective, soldier, hunter, witch, …
  Monster Lineages      help monster — vampire, shifter, ghost, …
  Celestial Mantles     help angel | help demon | help reaper
  Divine Faith Gods     help divine | help god -- episode pantheon worship
  Cosmic Tethers        help elemental | help eldritch | help void_touched

How you play
  1. origins more
""",
    "shop": """Notbigville -- General Store shopping

How you play:
  1. Walk to the General Store (east from Storm Watch, or west from Post Office).
  2. list              see wares and prices
  3. buy <item>        purchase with cash on hand
  4. sell <item>       sell something you carry (half the shelf price)

You need dollars in your wallet to buy. See help score for your cash line.
""",
    "clinic": """clinic -- hospitalize

Type hospitalize. clinic still works.

How you play
  1. hospitalize

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
breach -- Soldier breach spike

Usage: breach

How you play
  1. path soldier; signature Master+; commit
  2. breach            short outgoing spike

What it does
  Master signature. Costs 14 Spirit. Short outgoing spike — about half the usual buff window.
See also: help soldier | help suppress | help laststand | help commit
""",
    "active-combat": """
combat -- how fighting works (hub)

Fights in Notbigville get real, fast -- a swung fist, a drawn blade, a name shouted across the bar before the first punch even lands. The line between a bruising and a burial is thin, and it runs right through the finishing blow you choose to throw.

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
  3. While they are down (about two minutes), attack <name> again sends them to the town clinic, or attack <name> lethal is a killing finish where the rules allow it (you confirm twice). Good characters only go lethal against a consenting good player -- every other fight (NPCs, Echoes, idles) ends at the hospital. Wilderness hostiles and evil-vs-evil fights can already finish for keeps (help wilderness | help hospital). Players: autokill on finishes disposable dungeon fodder automatically (help autokill).

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
  Grain-elevator sparring shed (west of the highway shoulder) -- spar the training dummy, or takequest combat_drill for a slow guided lesson (help combat-drill).

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
Authored JSON stores the VNUM as the graph key (exits, home_room, runtime_hub). Leftover dig names remain a silent search fallback -- you should not need to type them for link, goto, or set home.

How you play
  1. Stand in a room (gm mode optional but recommended for goto)
  2. room                 inspect ROOM NAME, VNUM, map file, exits, flags
  3. room dig <dir> <ROOM NAME> create a new room that way and link both ways (rewrites map/zone JSON in-process -- does NOT trigger copyover;
      watch_and_run skips content/maps and content/zones)
     Dig stamps a VNUM as the graph key and keeps the leftover dig string as legacy_key; set ROOM NAME to the structured shape City - Main - Sub (e.g. Notbigville - Apartments - Floor C).
     Graph stores the VNUM so it does not collide with Wastes Ash Court. See help build-maps / docs/CONTENT_AUTHORING.md.
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
    "phone": """phone -- physical handsets, numbers, and plane-local calls

Calls go to a **phone number on an item**, not a character name.
call ash only works after you phone save ash 555-0142 (alias → number on *your* phonebook).

How you play
  1. Buy a flip phone or smartphone at the General Store (east of Storm Watch) or find a payphone (Main Street plaza).
  2. phone                 -- status: your number(s), call, contacts
  3. phone number          -- show handset number(s)
  4. phone primary <name|nick>  -- pick which portable speaks, texts, and opens apps
  5. nickname <item> as <nick> -- -- label a handset or any carried item
  5. dial 555-0142         -- ring that handset (same plane only)
  7. call ash -- -- dial a saved alias
  8. call sheriff -- -- ring whoever holds the county desk. They pick up. Type talk (or talk sheriff) on the line.
  6. answer / hangup       -- pick up or end
  7. phone say hello       -- private line; room sees you talk into a phone
  8. phone text ash you ok?  -- SMS a saved alias (voice handset on both ends)
  12. phone texts -- -- numbered inbox on your primary handset
  13. phone texts 2 -- -- read one thread (who / when / body)
  14. phone texts clear -- -- wipe all threads on this handset
  15. phone voicemail -- -- missed-call tape (phone vm)
  9. phone save ash 555-0142
     phone forget ash
     phone contacts
  16. phone claim 555-0142 -- -- keep a disconnected number (after someone is gone, numbers free up in a day unless you claim them)
  17. phone transfer -- -- move your primary number to a second handset (upgrade without losing your line)
  18. phone transfer to <handset> -- -- move your number to the other phone you carry
 10. dial WKNZ <text>      -- call-in queue (no radio tune needed)
  20. phone request <song> -- -- song request queue
 11. phone ask group|food|water|help -- when an Echo answers (not voicemail)
 12. phone speaker on|off     -- speakerphone (crowded rooms drown the words)
  23. approach <name> -- -- overhear a nearby caller when you are close
     phone conference <name> -- add a third party (max 3 on the line)
  24. dial collect <alias> -- -- collect call; callee types answer collect
                               (they pay the collect fee from wallet)
  25. phone bill -- -- smartphone plan owed
  26. phone bill pay -- -- pay that bill from cash
  27. buy <item> peachpay -- -- in-person PeachPay at the counter (smartphone)
  13. phone protect <handset>  -- stamp shield (Operator+ wirecraft or a library drop slot)
  29. phone protect drop -- -- library drop without entering the storm shelter
     phone protect handle <alias> -- secret stamp alias (Desk+ Path or Operator wire)
  30. phone track <number> -- -- legal ping (sheriff/PI) or illegal heat
  31. phone bug <smartphone> -- -- live tap while logged in (flip immune)
  32. page <alias> <short> -- -- pager burst (blackout-live)
  33. ham say <short> -- -- ham handheld, same room only (blackout-live)
  34. dashcam replay -- -- vehicle ring buffer
 14. Smartphones: phone app list | phone app install <id> | phone app open <id>
     phone settings ringtone|texttone|wallpaper <name>
     Flip phones: phone carvana (no app store). Smartphone: install Carvana app
     first, then phone carvana or phone app open carvana (help carvana)

Handsets
  Flip phones are prepaid — buy once, dial and text. Smartphones cost more up front and run apps from Peach Market (phone app list). Flip phones do not install apps — voice, text, and phone carvana only. Smartphones need the Carvana app installed before phone carvana when the smartphone is your primary handset.
  Pagers beep short codes only — not voice dial or SMS. Ham radios and dashcams are wire gear, not phone lines.

Apps (smartphone primary)
  phone app list — Peach Market glance (built-in, installed, not yet installed)
  phone app install <id> | phone app uninstall <id> | phone app open <id>
  Built-in: phone, messages, settings (cannot uninstall)
  Installable v1: carvana, town, weather, news, maps, bank, camera, games, radio, tv, peachpay
  phone settings ringtone|texttone|wallpaper <name> — cosmetic tones on the handset
  phone app open radio | phone app open tv — live program from the town bus (Channel 7 when a host is on air)
  phone radio — smartphone needs Radio app; flips use room or car radio (help radio)

Cell blackout
  Severe weather, a Signal god's parish kit, or staff action can kill cell in a room or zone. Smartphones show no bars unless a Signal god in the room projects Kind coverage (lifeline) — apps, PeachPay, and phone app list refuse the same way. Flip phones stay spotty for outbound dial and text — about one call in three may fail with one bar. Weather WEA still lands on flips. Payphones, house landlines, motel room phones, pagers, and ham rigs ignore the blackout. Signal gods: signal blackout [parish] | signal blackout off (help godkind).

Payphones
  Cost 1 dollars per outbound call. Usable only if the room has a payphone item or the room description mentions a payphone. Outbound only.

House and motel copper
  Dial a house landline or motel room phone and it rings the people in that room (and, for a house line, the homestead owner if they are still inside the house). Empty rooms give a ring-and-no-pickup tell — not "nobody is carrying that phone."

Collect
  dial collect <alias|number> rings collect; the callee must type answer collect. That accepts the charges and takes the collect fee from their wallet. Hang up or refuse if they cannot pay.

Smartphone bill
  Smartphone primaries accrue a monthly plan charge (~$35). Unpaid bills degrade data and apps (one-bar tell); voice may still connect. Type phone bill, then phone bill pay from cash. Flips and pagers stay prepaid.

Planes
  Calls need the same plane (Earth cannot ring Hell). Texts queue on the
  handset and deliver when you share a plane — use oocmail for OOC across
  planes (help oocmail).

Cadence
  Offline Echoes and town NPCs on Earth earn toward a flip phone (gig work), then walk to an in-town phone shelf (General Store in Notbigville) and buy one -- same buy verb players use.
  Towns without a shelf do not soft-lock on endless grocery gigs.
  Critical hunger/thirst still outranks shopping.
  On-shift desk clerks stay at their workplace before the phone grind.
  Hunters on a haunt / hunt lead may call a close-tie Echo partner
  (sibling, ashkin, …) with phone ask group so the road trip waits for the meetup -- same ask path players use.
Echoes
  Offline bodies can answer unless echo voicemail on. Live players must type answer. See help echo.

Texts (SMS)
  phone text <alias|number> <message> sends a short one-way SMS. Both ends need a voice handset (flip or smartphone). Threads live on the handset item — steal the phone, steal the inbox. phone texts lists threads; phone texts <n> shows who, when, and body; phone texts clear wipes every thread on your primary handset. A pocketed phone still buzzes in the room when a text lands. Picking up a handset that already holds threads nudges you to phone texts. True-offline players still get the line on login (queued flush) and the thread on the phone. Live and idlemode watchers get texts immediately. Echoes do not auto-reply to texts. Sheriff and weather WEA texts land in their own official thread, not mixed with friends — smartphones dead in a cell blackout do not receive WEA until coverage returns; flips still get emergency SMS. After greet, talk curio number then text curio where or quote <item> (help curio). Off-plane texts still land on both handsets but the buzz waits until you share a plane.

Voicemail (handset)
  Missed calls on your portable leave a short tape on that handset — phone voicemail or phone vm to list, phone voicemail <n> to listen, phone voicemail delete <n>. Echo voicemail on|off is separate (help echo): that pref blocks Echo pickup, not this tape.

Overhear and speakerphone
  By default only your call peer hears your words; the room sees you talk into a phone. approach <name> while they are on a call to stand close and catch their side. phone speaker on broadcasts your words to the room unless it is crowded (five or more people, or a plaza bustle) — then listeners only get muffled chatter. Vehicle cabins pipe Bluetooth audio to everyone inside.

Conference and collect
  phone conference <alias|number> (or phone add …) while connected adds a third party — three characters max. Collect charges are covered above.

PeachPay
  Install PeachPay on a smartphone primary, stand at an on-duty shop counter, then buy <item> peachpay or phone peachpay <item>. You hand the clerk your phone for a short trust window — steal the phone during the window and the sale dies. No paying from another room.

Wirecraft
  Secret wirecraft ranks up on contested protect, track, and BUG. Protect replies may name your band (Civilian through Ghost) when you outbox a desk stamp — never a raw XP number on score. Track and BUG stay terse (legal ping or heat), not a band ladder readout. Operator-quality shields can be stamped at the town library afternoons (phone protect drop) or the storm-shelter wire desk nights. Ghost-tier operators outbox named stamps. phone protect stamps your handset; phone protect handle <alias> sets a secret handle from Desk-rank Hacker Path or Operator wire — the alias never prints on score. phone protect drop leaves a handset in the library slot without entering the shelter (evils welcome). phone track <number> is legal for sheriff/PI/detective desks (fee) or illegal heat otherwise. phone bug <smartphone> feeds live call audio to you while logged in — flip phones and pagers refuse the tap.

APK Barn
  phone app barn list | phone app barn install torch — sideload sketchy apps outside Peach Market. No online clone or hack bank in v1.

Pager / ham / dashcam
  page <alias|number> <short> — pager SKU only; works in cell blackout. ham say <short> — handheld ham rig, heard in this room only (no phone number, ignores blackout). dashcam replay — ring buffer on the dashcam item; it records as the vehicle rolls (carry it in the cabin). vehicle dashcam replay from inside the cabin.

Civic hold
  When the sheriff desk line is busy, the next caller hears short local hold spots until the desk picks up — not the Veil gateway wait tone.

Screenreader: lines use [PHONE] / [CALL] tags (never color alone).

Cadence
  Offline Echoes and town NPCs on Earth earn toward a flip phone (gig work), then walk to an in-town phone shelf (Radioshack in Lebanon; gas station in Lawrence) and buy one -- same buy verb players use.
  Towns without a shelf do not soft-lock on endless grocery gigs.
  Critical hunger/thirst still outranks shopping.
  On-shift desk clerks stay at their workplace before the phone grind.
  Hunters on a haunt / hunt lead may call a close-tie Echo partner
  (sibling, ashkin, …) with phone ask group so the road trip waits for the meetup -- same ask path players use.
Echoes
  Offline bodies can answer unless echo voicemail on. Live players must type answer. See help echo.

Conference and collect
  phone conference <alias|number> (or phone add …) while connected adds a third party — three characters max. Hang up while they are still ringing and you stay on the original call. Collect charges are covered above.

Wirecraft and Charlie
  Secret wirecraft ranks up on contested protect, track, and BUG. Protect replies may name your band (Civilian through Ghost) when you outbox Charlie's stamp — never a raw XP number on score. Track and BUG stay terse (legal ping or heat), not a band ladder readout. Charlie Bradbury (alive AU) stamps Operator-quality shields at the Lebanon Town Library afternoons (phone protect drop) and the Men of Letters bunker nights. Ash stamps at the Roadhouse rig when he is in the back room. Ghost-tier operators outbox named stamps. phone protect stamps your handset; phone protect handle <alias> sets a secret handle from Desk-rank Hacker Path or Operator wire — the alias never prints on score. phone protect drop leaves a handset in the library slot without entering the bunker (evils welcome). phone track <number> is legal for sheriff/PI/detective desks (fee) or illegal heat otherwise. phone bug <smartphone> feeds live call audio to you while logged in — flip phones and pagers refuse the tap.

Pager / ham / dashcam
  page <alias|number> <short> — pager SKU only; works in cell blackout. The other person must be carrying a pager and logged in — an offline Echo without a pager does not get a fake Page sent. ham say <short> — handheld ham rig, heard in this room only (no phone number, ignores blackout). dashcam replay — ring buffer on the dashcam item; it records as the vehicle rolls (carry it in the cabin). vehicle dashcam replay from inside the cabin.

How you play
  1. phone more
""",
    "appearance": """appearance -- structured look slots

How you play
  1. appearance -- list every slot and your current look
  2. appearance <slot> <id or text> -- set hair, eyes, height, physique, voice, …
  3. pronoun he|she|they|it -- combat prose and look grammar
  4. setdesc -- full freeform look (paste editor; setdesc clear restores auto-build)
  5. setdesc <name> <body-part> -- closer look others type as look <you> <name>

No args: list every slot and your current look description.
  appearance <slot> <id or your own text> Catalog ids (blue, short, …) work as before. Anything else is saved as a custom value (max 40 characters) -- e.g.
      appearance eye color storm grey
      appearance hair color sun-bleached wheat

Chargen asks who you are first (mortal Background or Awakened Nature), then lists the catalog for your look. Elementals see an Aspect kit
(Fire / Water / Air / Earth) -- living matter, not mortal hair and skin.
Void-Touched Cosmics see an uncanny kit (void-bleached skin, wrong eyes).
Glamour over a mortal face is later magic, not chargen. You can still type custom text instead of a number/id. Chargen also asks when you were born (see help age).

One-time refresh: if you have ``appearance_retro_pick`` (Void-Touched catalog expansion), type ``appearance repick``, set each slot, then ``appearance repick done``.

Height bands (mortal catalog): very short, short, below average, average, above average, tall, very tall.
Help flavor only -- approximate ranges like ~5 ft 8 in are not mechanical. Type your own height at chargen or with appearance height if you prefer (e.g. 5 ft 9 in or 175 cm).

Slots: hair style, hair color, eye color, height, physique, skin tone, facial hair, scars, voice.
Voice is a look tag and flavors what listeners hear on room say (in a gravelly voice, …). No mechanical range or carry.
Physique is your build or frame (slim, athletic, average build, stocky, heavyset) -- appearance only, not combat body_type and not a weight stat.
Mortals may age slowly over long in-game time (look only -- no stat loss). Vampires and other supernaturals do not visibly age. Offline Echo bodies age on the same calendar.

  1. pronoun he|she|they|it -- -- combat prose + look grammar
  2. setdesc -- -- full freeform look (paste editor; setdesc clear restores the auto-built sentence from slots)
  3. setshort <text> -- -- short room-face strangers see (height/hair auto, or your own line; see 'help setshort')
  4. gait <id> -- -- how you enter/leave rooms (walk, glide, skip…; see 'help gait')

New characters set this in chargen; these commands edit afterward.

  10. pronoun he|she|they|it -- -- combat prose + look grammar
  11. setdesc -- -- full freeform look (paste editor; setdesc <name> <body-part> for closer looks; see help setdesc)
  12. setshort <text> -- -- short room-face strangers see (height/hair auto, or your own line; see 'help setshort')
  13. gait <id> -- -- how you enter/leave rooms (walk, glide, skip…; see 'help gait')

How you play
  1. appearance more

  2. pronoun he|she|they|it -- -- combat prose + look grammar
  3. setdesc -- -- full freeform look (paste editor; setdesc <name> <body-part> for closer looks; see help setdesc)
  4. setshort <text> -- -- short room-face strangers see (height/hair auto, or your own line; see 'help setshort')
  5. gait <id> -- -- how you enter/leave rooms (walk, glide, skip…; see 'help gait')
See also: help age
""",
    "relationships": """
relationships -- friends, family, rivals, enemies, ashkin, favorites

Tags are YOUR view of someone -- they do not have to agree. Setting a new kind on a person replaces only that person's tag.

How you play
  1. relate -- list your tags and favorite person.
  2. relate <name> <kind> -- set a tag (friend, rival, love, sibling, ashkin, enemy, …).
  3. relate clear <name> -- remove your tag.
  4. favorite <name> -- who the town AI prefers for hangouts when you are lonely.
  5. favorite clear -- auto-pick from your tags again.

Family kinds
  sibling -- brother / sister (legacy brother still works).
  parent -- mom / dad / mentor figure.

Ashkin (help ashkin)
  A Purgatory war-bond -- closer than a buddy, not blood family. You must carry the Purgatory scar yourself before you can tag someone ashkin.

How the town AI uses tags
  When social need is high, it prefers your favorite person. Closer tags beat weaker ones for auto-pick (lover, then family, then friend, …). rival stays competitive without murder. enemy and worse can lead to hunt attempts when you share a zone.
  Offline Echoes use close ties for hunt / rest buddies (never Enemy-tier).

Beckon (help beckon) can pull close Echo / idle ties along as companions. recruit Operator, or ask Operator to help -- a named townie can ride along even without a close tag (help recruit). Mutual siblings can die into the afterlife so a sibling may attempt a rescue (help death).
See also: help ashkin | help beckon | help recruit | help personality
""",
    "personas": """Notbigville -- persona traits demo

The Post Office Operator carries the ``chatty`` trait from personas.json.

How you play:
  1. Walk to the Post Office.
  2. greet Operator             hear trait-colored flavor text.
""",
    "fishing": """fishing -- cast at lakes, rivers, and piers

How you play
  1. Stand at fishable water (look for a pier or lake shore).
  2. fish -- cast a line (always yields fish or junk).
  3. fish status -- water readout for this room.
  4. Buy worms at the General Store, then fish worms for better odds.

See also: help shop
""",
    "lockpick": """lockpick -- bypass mechanical and electronic seals

How you play
  1. lockpick <container> -- pick a locked box or crate nearby.
  2. lockpick door <direction> -- slip a structure door latch.
  3. lockpick clip <container> -- force an electronic seal attempt.

Nearby watchers see a painted name, not a raw storage key.
See also: help justice
""",
    "traps": """traps -- salt, iron, devil's traps, and goofer barriers

Hunter gear and Occultist chalk share the same room marks:

How you play
  1. claim a house          your home joins the list automatically
  2. protect                stand in a room; chalk missing Demon marks (costs kit) and add it to your list
  3. salt here | iron here | trap here | goofer here  lay one mark when you only have that reagent
  4. wardlist               see which rooms you keep chalked
  5. unprotect [room]       drop a room from the list (marks stay)

When you leave a home (sellhome, claim a new house, or homestead), old house rooms drop off wardlist. Civic stamps such as the diner or clinic stay.

When Deal hellhounds hunt you (help deal), pour goofer here (and salt here) to buy time -- the pack chews the line, it does not last forever. Occultists and Demons with pierce sight may also leash a contained hound briefly (help hellhound). Killing a hound still needs an angel blade, demon-killing knife, or The Colt, plus sight (glasses, or being an Angel / Demon / God).

Cadence / your Echo (Hunter, Slayer, or Occultist) re-chalks listed rooms when salt, iron, or the devil's trap goes missing -- they restock kit first when short. Hunters (and Bobby at Singer House) also exorcise
Demon hosts held in a devil's trap in the same room or next door.
Occultist circle is a timed trap, not a permanent chalk mark.

  6. devil's trap -- permanent map traps, or timed circle -- blocks Demon force-possess; soft-blocks Demon leave
  7. salt line -- vessel-free Demons stumble on entry; spirits cannot cross; also slows Deal hellhounds until chewed
  8. iron ward -- modest edge vs Demons / possessed in the room
  goofer barrier Deal hellhound block / slow (goofer dust) -- separate from the Demon protect trio

Magic casters can also pour a temporary saltline (help saltline) -- same soft-block folklore, fades on its own, no permanent chalk list.

Salt-bag kit (inventory). Permanent marks cost folklore reagents from your gear bag or inventory (Ash Garage stock):

  9. salt packet -- -> salt line     (salt here / laysalt)
  10. iron scrap -- -> iron ward     (iron here)
  11. ritual chalk -- -> devil's trap  (trap here)
  ritual chalk or devil's trap paint -> trap-brand on a weakened Demon (trapbrand <target>)
  12. goofer dust -- -> goofer barrier (goofer here) -- hellhound pursuit
  13. protect -- -> burns one of each Demon-trio reagent you still need (salt / iron / chalk -- not goofer)

Abaddon prep (trapbrand) pins a Knight-of-Hell-or-above Demon's smoke to the meatsuit so a later decapitate contains instead of evicting. Room devil's traps alone do not count -- you must brand the host (help decapitate).

Haunt salt (no here) still traps a claimed house haunt and also burns a salt packet -- different craft (help haunts).

  14. devil's trap -- permanent map traps, or timed circle -- blocks Demon force-possess; soft-blocks Demon leave
  15. salt line -- vessel-free Demons stumble on entry; spirits cannot cross; also slows Deal hellhounds until chewed
  16. iron ward -- modest edge vs Demons / possessed in the room
  goofer barrier Deal hellhound block / slow (goofer dust) -- separate from the Demon protect trio

  17. salt packet -- -> salt line     (salt here / laysalt)
  18. iron scrap -- -> iron ward     (iron here)
  19. ritual chalk -- -> devil's trap  (trap here)
  ritual chalk or devil's trap paint -> trap-brand on a weakened Demon (trapbrand <target>)
  20. goofer dust -- -> goofer barrier (goofer here) -- hellhound pursuit
  21. protect -- -> burns one of each Demon-trio reagent you still need (salt / iron / chalk -- not goofer)

devil's trap   permanent map traps, or timed circle -- blocks Demon force-possess; soft-blocks Demon leave
  salt line      vessel-free Demons stumble on entry; spirits cannot cross; also slows Deal hellhounds until chewed
  iron ward      modest edge vs Demons / possessed in the room
  goofer barrier Deal hellhound block / slow (goofer dust) -- separate from the Demon protect trio

How you play
  1. traps more

salt packet   -> salt line     (salt here / laysalt)
  iron scrap    -> iron ward     (iron here)
  ritual chalk  -> devil's trap  (trap here)
  ritual chalk or devil's trap paint -> trap-brand on a weakened Demon (trapbrand <target>)
  goofer dust   -> goofer barrier (goofer here) -- hellhound pursuit
  protect       -> burns one of each Demon-trio reagent you still need (salt / iron / chalk -- not goofer)
""",
    "grace": """grace -- celestial power that fuels radiant abilities

Grace is the shared fuel tank for folklore celestials in this demo build.
It drains when you spend power and slowly refills on holy ground or a
home plane. Your score and HUD gauges show Grace when you carry it.

How you play
  1. score -- read Grace on the vitals band when your character has it.
  2. Stand on consecrated ground (chapel, shrine, temple) to passively
     regain Grace over time.
  3. See help vessel when you ride or possess a mortal host -- grace and
     flesh stay separate concerns.

See also: help vessel | help score
""",
    "vessel": """vessel -- a mortal body a celestial Mantle wears or rides

A vessel is the flesh a celestial inhabits or possesses. Watchers see the
host's face in the room while the Mantle may still act from inside. The
map may mark a ridden body with a vessel token so you can spot possession
at a glance.

How you play
  1. look -- room presence shows the public face, not a storage id.
  2. When you share a room with someone riding a host, their map token may
     read as vessel instead of an ordinary person square.
  3. score vitals -- grace (help grace) tracks radiant fuel separately from
     the host body's lifeforce.

Design and who uses it
Angels and Demons name a mortal vessel at creation (alive or dead, age, look, personality, job when alive). Legacy Mantles get one vessel design prompt later. Alive vessels keep living when you leave; dead designs leave a named husk (corpse Item) on the floor. Gods and Reapers do not use this kit (help god / help reaper / help omnipresence).

Two layers while embodied
  form            stay in the body; reveal grace or hellfire
  vessel leave    step out as spirit -- smoke (Demon; Princes wear a hue) or light (Angel; Grigori hues) on Earth. On Heaven (Angel) or Hell (Demon) you stand in a construct of your meatsuit face -- corporeal, stronger. form still shows the true, hard-to-look-at visage.
While vessel-free, spiritlook <short line> sets custom presence (120 chars) on Heaven, Hell, or Earth.

Player hosts share one body with Lead Driver / Active Passenger. NPC and offline hosts still exile to personal Heaven / Hell (help personal-afterlife).

Combat and durability
Host meat and Mantle grace both matter. Body stats (VIT/POW/RES/FIN) lean on the host; FOC/PRE/ATT stay Mantle. Vessel quality (poor/fair/perfect) scales meatsuit contribution; vessel find shows the blend line.
Riding or embodying a vessel raises max lifeforce above the mortal baseline -- scaled by quality and how you got in: full bonus on your own living vessel, half on a consenting foreign host, poor-rate cap on forced rides (plus faster strain while Hell-trapped) and on your own dead husk after a KO. score and vessel find show the current bonus. When the Mantle leaves, the extra ceiling drops.

Cadence, needs, and travel
When you leave an alive vessel, they stay in the room and resume job and personality. vessel leave sleep parks them asleep so they do not get up for work until someone types wake <name> or you vessel take them again. vessel autosleep on makes every vessel leave park them that way; vessel leave live lets them live once without turning the toggle off. Vacant vessels show on look under their mortal name as ordinary NPCs in the room. While you ride them they stay hidden so the room only sees you. Dead designs leave a named husk Item instead.
While you wear a vessel, your Mantle runs fuel, homesickness, duty, and vessel strain -- not mortal grocery, sleep, wash, or fear hide (help needs). Step out and the vacant vessel feels hunger, thirst, sleep, and fear again until you vessel take your own husk back. Riding a foreign host freezes that mortal's lifestyle meters.
Travel keeps the vessel -- teleport, dteleport, and walking move you and a ridden host together. Leaving the meatsuit is a deliberate vessel leave, not how Cadence goes home. Offline Angel Echoes rarely step out so an alive vessel can live a beat; Demon Echoes stay embodied unless a player leaves. Cadence tries vessel take to return when stranded.

Fights, consent, and death-kick
Fighting your own vessel knocks them out first; finish with attack <name> (hospital) or attack <name> lethal twice to destroy it (help combat).
Angels need consent; player hosts share the wheel unless they vessel dream into Heaven. Demons may vessel force player hosts into a trapped passenger seat (mind / contested vessel seize). High trauma softens PRE and FOC briefly and mind-links echo [TRAUMA]. Empty corpses need no consent.
A lethal hit ejects you (not mortal revive). Pay fuel, then realmhome or remain -- unless banished.

Destruction and reclaim
If the Author is rewriting a destroyed vessel, vessel find shows the timer and vessel take refuses until the new body is placed.
Standing vessel-free with your vessel in the same room on your home plane (Angel in Heaven, Demon in Hell) reclaims it for free -- even at 0 fuel after a death-kick. Elsewhere, vessel quality changes fuel cost and, while embodied, nudges your train softcap (help training).
Offline Echoes Cadence back with vessel take when stranded.

Wear, fuel, and wandering spirits
Long rides can wear quality down; consecrated Angel rest and Demon feast can ease it back -- a [VESSEL] tell when the band moves. Out of fuel, track husk gives a soft lead (help track). Vessel-free spirits may enter claimed homes unless salt or devil's traps seal them (help lodging). Free Angels may teleport vessel while spirit-free.

How you play
  1. vessel more

How you play
  1. vessel lore
See also: help track | help lodging | help training
""",
    "cadence": """cadence -- how offline townsfolk live their day

Notbigville townsfolk get hungry and thirsty over time. When a need
crosses the seek line they walk to a resource room (food at the General
Store, water at the Prairie Inn lobby) or step outside when content.

How you play
  1. Stand on Main Street and look -- seeded townsfolk appear in Room.Occupants on the interior map.
  2. Watch Ellis Quinn or June Hale leave home when hunger rises; they path toward food or water.
  3. help needs -- hunger/thirst meters on your own character use the same engine kit.

This demo runs on the public basegame tick loop only (not live SUPERS Cadence).
See also: help needs | help npcs
""",
    "planes": """planes -- afterlife realms and where you have a body

Purgatory is the corporeal prison among the afterlives: routed souls and
living intruders fight as flesh here, and objects can leave the realm.

How you play
  1. On the prison plane you fight and take hits as a body, not as a
     discorporate spirit.
  2. Gear and objects can be picked up and carried out.
  3. When a soul leaves the prison for Earth, they are not walking flesh
     until reanchored on Earth.

See also: help death | help cosmology
""",
    "purgatory": """purgatory -- the corporeal prison plane (see help planes)

Same article as help planes. The prison plane id is purgatory in map data.

Who arrives here
  Neutral mortal Humans often come here instead of Heaven or Hell.
  Most non-Angel / non-Demon natures check in at The Waystation.
  Angels and Demons use their vessel death-kick instead (help vessel).
  Wilderness and nest hostiles that die on Earth leave a corpse; the soul wakes as living flesh on the Ashen Marches -- Leviathan prey until an escape clock returns them to the living world.

Getting home
  Your nest, crypt, pack den, haunt, or parlor is your HOME -- not where you automatically wake after a normal kill. After Waystation check-in you return to where you died (body_room). Homes matter for chargen spawn, Chuck rewrite while protected, phantomstep, larder, and Ghost haunt bind (haunt moves your home when you claim a new room).
  Mortals and ensouled vampires: after a short linger near your body, a Reaper claims and ferries you (help reaper), then you wait roughly ten real minutes for check-out.
  Monster-frequency souls (Vampire nest, Shifter, Arachne, Leviathan, …): no Reaper queue and no Earth linger -- you wake as flesh on the ash at once. Logging off does not dodge that usher. Your corpse stays on Earth. Leviathans hunt this prison.
  Porter at the Waystation restores you (Lumen does Heaven; Sift does Hell). Bound souls cannot leave this plane -- move, gate, teleport, realmhome, and ridereaper stay sealed until release:
    paydebt in the Ledger Room (dollars) once you are among the living again
    Leviathans never use the Ledger or Fog-Reaper -- only Master+ breach
    at the Bone Gate with the seal key
  Winchester brothers (Sam/Dean): the desk clocks the soul on Heaven/Hell arrival, then holds check-out for a deal window (help relationships).

Living bodies stranded here (flesh, not a death-spirit, not bound):
  Porter approaches and offers passage home -- type acceptpassage
  (see help passage). Homesickness climbs on the wrong plane; Echoes
  Cadence-seek Porter and auto-return. There is no walk path from the Ashen Marches to Earth.

Newbie protection on Purgatory: a confirmed lethal finish is Chuck-rewritten to your origin home (help protection) -- not spirit check-in.
  After protection ends, monster-frequency souls who die here route to The Empty instead of the Waystation desk (help empty).
  NPC Leviathans who suffer true death here melt into ooze and may leave a Leviathan Blossom on the ash (help blossom).

Who lives here
  Porter and Ashen keep the Waystation ledgers (bureaucracy, not theology). A small Leviathan nest also lingers -- Black Nest, Ash
  Courtyard, Bone Gate -- and hunts living Ashen Marches prey the way player Leviathans do (help leviathan). The ash is their territory:
  trespassing Angels/Demons risk black-ooze rupture when standing allows
  (same ≤+1 Tier window as smite -- help ooze); mortals and monsters get consumed. Nest soldiers can still land here -- the Nest answers. A
  landed finish is nothingness: trash erased, important pages rewritten by the Author to origin home. NPC Leviathan true deaths may leave a Leviathan Blossom in the ash.

Places to know
  Waystation, Ledger Room, and Reaper Dock are no-combat desks (Porter check-in, paydebt, Reaper clock-in). Violence belongs on Ash Courtyard, the Ashen Marches wilderness, Black Nest, and Bone Gate.
  Waystation, Ledger Room, Reaper Dock, Ash Courtyard, Black Nest, Bone Gate. Under Black Nest, search may open The Deeper Well
  (help eldritch). Below that, the Bottomless Rung opens the endless pit (help pit). On Earth, a rotating ashen rift near towns also drops you straight into pit B1 -- enter rift when you find one.

How you play
  1. purgatory more
""",
    "domain": """domain -- a claimed wilderness pocket you own

A domain is a claimed wilderness pocket: a walkable macro and micro grid that
belongs to you once it is stamped on the map.

How you play
  1. On a claimed pocket you still type n, s, e, and w to move cell to cell.
  2. Objects can sit on individual cells and stay there until picked up.
  3. The grid can grow as more macro cells are opened at the frontier.

How you play
  9. invite <name>           guest list (standing invite)
  2. portal -- at throne hub: open ~20 min ingress
  3. uninvite <name> -- revoke guest
 10. Guests at throne hub    enter demesne  (while portal is open)
 11. expanddomain            grow macro when throne hold time unlocks tiers
 12. reclaimdomain           after throne loss seals you out, reclaim throne then reopen (work + Kind are never deleted)
 13. creature copy <id>      author a custom creature (help creatures)
 14. mountpocket <x,y> <hub> [span] [enter_as,...]  hang a city zone hub as a settlement pocket on one macro cell. expanddomain first if the grid is too small.
     unmountpocket <x,y>      remove a mounted pocket
 15. create                   list realm town templates + Realm Mana costs
  10. create town <template> [name] -- weave a starter town at your macro cell
     create district <template> at <x,y> [name]   anchor at explicit macro
  11. create shape room <type> [dir] -- add street or nest (Realm Mana)
  12. create weave sewer|gate|shrine -- stamp a feature (Realm Mana)
     create dissolve district <x,y>   remove a town (partial mana refund)

When you found the demesne you mirror your locked God Kind (from chargen or choosekind) on demesne meta (pagan, folk, nature, bloodrite, covenant, or signal). Kind chooses which soft binds and traps scale harder against you on that ground -- host plane and painted terrain amplify matching restraint families. Permanent god-slaying stays rare and is not live yet; realm death still binds you into the demesne for a day (help death).

Ground streets at z=0 paint the macro map; underground and upper floors do not. Realm Mana regens faster inside your demesne.
""",
    "demesne": """demesne -- claimed pocket grid (see help domain)

Same article as help domain.
""",
    "demesnes": """demesnes -- claimed pocket grids (see help domain)

Same article as help domain.
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
        "appearance", "relationships", "personas", "fishing", "lockpick",
        "traps", "grace", "vessel", "cadence", "planes", "domain",
    ]),
    *HELP_ENGINE_CATEGORIES,
]
