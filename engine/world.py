"""
engine/world.py -- the lean, game-agnostic world model (two-repo purity
Phase 3: docs/plans/two_repo_purity.md).

This file knows NOTHING about networking OR about SUPERS. A Room has no
idea what a socket is; it just holds objects and exits. That separation is
what lets you swap telnet for a WebSocket web client later without touching
any of this. The SUPERS-game-content half of the old root world.py (the
training dummy, wilderness hostiles, procedural dungeons, lockboxes --
anything that reaches into supers.bestiary/supers.faith/supers.stats) moved
to supers/world_ext.py instead; see that module's docstring.

Hierarchy:  GameObject  ->  Room / Item / Character
(Room, Item, and Character all inherit from GameObject, so they share its
key/description without repeating that code.)

Root world.py is now a thin re-export facade over this module PLUS
supers/world_ext.py, so every existing `from world import X` /
`import world; world.X` callsite across the codebase keeps working
unchanged -- see that module's docstring for the full list of re-exports.
"""


class GameObject:
    """Base class for anything that exists in the world (rooms, items, people)."""

    def __init__(self, key, description="You see nothing special."):
        # __init__ runs automatically when you create the object, e.g. Item("sword").
        # 'self' is this particular object; we hang its data off it here.
        self.key = key                  # the object's name, e.g. "a rusted sword"
        self.description = description   # shown when someone looks at it


class Item(GameObject):
    """A thing that can sit in a room or (later) an inventory.

    Most Items are still pure flavor (just a key/description), same as
    before -- `locked`/`loot` default to "not a container" so nothing about
    plain items changes. A container (e.g. a dungeon strongbox) sets
    `locked=True` and a non-empty `loot` list; commands.cmd_open is the only
    thing that reads either field, via 'open <item>'.

    `loot` is a list of small reward dicts rather than one hardcoded type,
    so a future reward (an actual Item drop, once equipment/materials exist
    -- D30) can be added as a new dict "type" without changing the
    container mechanic itself. Today: {"type": "growth", "amount": n} and
    optional {"type": "relic", "id": <divine relic id>} (congregation-
    happiness items for Divine -- see supers.faith.DIVINE_RELICS).
    EXTENSION POINT: give items weight, value, or SUPERS stats/Aspects here.
    """

    def __init__(self, key, description="You see nothing special.",
                 locked=False, loot=None, is_body=False, relic=None,
                 is_buried=False, furniture=False):
        super().__init__(key, description)
        self.locked = locked
        # 'loot if loot is not None else []' avoids the classic Python
        # mutable-default-argument trap: `def __init__(self, loot=[])` would
        # share ONE list across every Item that doesn't pass loot explicitly.
        self.loot = loot if loot is not None else []
        # Section 6 (Death/Spirit): True only for a body Item built by
        # make_body below. Warded by default -- commands.cmd_open/cmd_get
        # both refuse to act on one (destroying/looting/carrying a warded
        # body is a Reckoning-tier act, D7, still open -- until that's
        # built, the honest thing is to simply not offer the interaction at
        # all rather than half-build the stakes around it).
        self.is_body = is_body
        # True once this body has been lowered into a grave (gravedigger job or
        # a player's `bury`). Still a real, present body Item -- Cadence auto-
        # revive needs it in the room -- but flagged so the gravedigger's scan
        # skips it instead of re-exhuming and re-burying the same corpse. Only
        # ever meaningful when is_body is True.
        self.is_buried = is_buried
        # Scavenger harvest flags (supers/scavenge.py): meat/blood can each be
        # taken once from an unburied body without destroying it -- Moss can
        # still bury, spirits can still anchor. Only meaningful when is_body.
        self.body_harvested_meat = False
        self.body_drained = False
        # Divine relic id (supers.faith.DIVINE_RELICS) or None. Carried
        # relics keep a Divine congregation happier -- see faith.py.
        self.relic = relic
        # Lodging: fixed room prop (beds, etc.). commands.cmd_get refuses
        # furniture; it is never inventory. Catalog sets furniture=True via
        # supers.items.make_world_item. owner_key stamps who prefers this
        # bed (soft claim / hotel lease) -- None = unowned.
        self.furniture = bool(furniture)
        self.owner_key = None


class Room(GameObject):
    """A location. Holds exits to other rooms and whatever is currently inside."""

    def __init__(self, key, description=""):
        # super().__init__(...) calls GameObject's __init__ so we don't rewrite
        # the key/description setup — then we add the two things only Rooms need.
        super().__init__(key, description)
        self.exits = {}       # a dict: direction (str) -> Room object, e.g. {"north": alley}
        self.contents = []    # a list of GameObjects currently here (characters AND items)
        # Gravity Training (systems doc section 4-D): environmental multiplier
        # on physical solo training. 1.0 = normal Earth gravity; higher values
        # cost more stamina per rep and raise gain odds. Authored in
        # build_world() (rooms are rebuilt every boot, not DB-backed).
        self.gravity = 1.0
        # Milestone F (a live player suggestion): flags this room as
        # wilderness, where wandering hostiles can occasionally spawn (see
        # check_wilderness_encounter below). Only the 100x100 overworld grid
        # is wilderness; hand-authored areas (plaza/alley/chamber) are safe.
        self.wilderness = False
        # Bestiary wiring: which bestiary.py categories (e.g.
        # "earth-dweller", "fire-being") are eligible to spawn in this
        # room -- set once from the owning map's grid definition
        # (content/maps/*.json's "bestiary_categories"), same as gravity
        # above. Plain data, not derived, so a future planar-influence
        # event can mutate it live on affected rooms (e.g. an Earth room
        # under Fire Plane influence temporarily gaining "fire-being")
        # without any loader or spawn-code changes.
        self.bestiary_categories = []
        # The standalone map editor's D25 follow-on (docs/SYSTEMS_DESIGN.md
        # section 9/10): a formal terrain tag -- "plains", "ruins",
        # "city", etc. (maps.AREA_TYPES is the controlled vocabulary).
        # Wilderness is a separate boolean flag (self.wilderness), not a
        # terrain tag -- see bug #26 / maps.WILD_AREA_TYPES.
        # Two real, engine-side uses: `look` shows area_type (commands.cmd_look),
        # and maps.py seeds a grid/room's bestiary_categories from
        # AREA_TYPES[area_type] whenever the JSON doesn't specify
        # bestiary_categories explicitly (see maps._add_room). Defaults to
        # "plains" -- open scrubland for the overworld grid.
        self.area_type = "plains"
        # Cadence town simulation (docs/SYSTEMS_DESIGN.md D33, a D31 follow-on):
        # which NEEDS resources this room offers autonomous NPCs. Plain string
        # tags -- "food", "water", "sleep", "vendor", "blood" -- authored per
        # room in content/maps/*.json (maps._add_room). Empty = offers
        # nothing; the Cadence loop (supers/cadence.py) reads this to decide
        # where a hungry/thirsty/sleepy NPC should walk.
        self.resources = []
        # Optional job ids this room supports when someone clocks `work`
        # (map JSON "jobs": ["cook"]). First entry is the default claim.
        # Validated against supers/jobs.py at Cadence boot (not at map load
        # -- maps.py stays free of SUPERS imports).
        self.jobs = []
        # Cadence NPCs are CONFINED to their home zone: a grouping id (e.g.
        # "wastes-town") shared by every hand-authored room of one settlement.
        # supers/cadence.py's pathfinder only ever steps between rooms sharing
        # a zone, so an NPC can never wander out through the zone exit onto
        # the 100x100 overworld grid (grid cells leave this None). Set
        # from the map JSON's per-room "zone" (maps._add_room); None = ungrouped
        # (overworld grid, procedural dungeons -- no Cadence NPCs there).
        self.zone = None
        # Grid <-> pocket zone travel (separate from cardinal / in-out moves):
        # zone_entries maps lowercase alias -> hub Room (set on the gateway
        # grid cell by maps._link_pockets). zone_exit_to is the grid cell to
        # return to via the `exit` verb (stamped on the hub and every room
        # sharing its zone). Not stored in exits{}, so `north`/`in` never
        # accidentally walk the pocket link.
        self.zone_entries = {}
        self.zone_exit_to = None
        # Optional scarcity cap per resource tag, e.g. {"sleep": 2} = only two
        # NPCs can occupy the hotel's beds at once. A tag absent here (or <= 0)
        # means unlimited. This is what makes the "last bed" squabble emerge --
        # the Cadence loop counts NPCs already using a tag here and makes a
        # late arrival wait and grumble instead of bedding down. Authored in
        # content/maps/*.json as "resource_capacity"; defaults to no caps.
        self.resource_capacity = {}
        # D34 (a D33 follow-on): flags this room as "eviltown" -- a haunt for
        # evil NPCs (Vampires; later maybe Demons) within an otherwise normal
        # settlement. Two things read this flag: peaceful townsfolk pathing/
        # wandering (supers/cadence.py) treats an evil_zone room as impassable
        # -- ordinary NPCs never path through or idle-wander into one, even
        # though it usually shares the SAME Room.zone as the rest of the town
        # (that's what lets it be reachable at all, e.g. down from a plaza) --
        # and evil NPCs (supers/cadence.py's vampire hunt behavior) are the
        # only Cadence actors exempt from that avoidance (plus allow_evil
        # staff like the Rat's Nest bartender). A boolean flag, same shape
        # as Room.wilderness. Authored in content/maps/*.json as "evil_zone";
        # default False (an ordinary room).
        self.evil_zone = False
        # Sanctuary flags (Cadence conflict ebb/flow): vampires refuse to hunt
        # or enter vampire_safe rooms (Corner Bar, Town Gym); hunters refuse
        # hunter_safe rooms (Rat's Nest Backroom). Authored in map JSON;
        # default False. Same boolean shape as evil_zone / consecrated.
        self.vampire_safe = False
        self.hunter_safe = False
        # Evil ward (Men of Letters bunker, etc.): hard entry refuse for
        # Demons, possessed hosts, Vampires, and evil-aligned characters.
        # Good and neutral may shelter. See supers/wards.py + move_gate.
        self.evil_ward = False
        # Cadence D39: a townsfolk home (distinct from hotel-for-drifters).
        self.is_house = False
        # Private-home hard door (entryway / porch / apartment unit). When
        # True on an entryway, `in` into the is_house interior is gated; when
        # True on an is_house unit (apartments), the door-code step is gated.
        # Unclaimed homes stay open -- see supers.lodging.can_enter_home.
        self.private_home = False
        # Cadence #49: a burial plot the gravedigger fills.
        self.is_grave = False
        # Jail cell the deputy locks criminals into (mirrors is_grave).
        self.is_cell = False
        # Desperation robbery: grocery / fast-food counters tagged robable.
        self.robable = False
        # D29/suggestion #8: which dimensional plane this room sits on
        # (e.g. "earth", "fire", "heaven"). Set from content/maps/*.json's
        # top-level "plane" when the room is grid-built; hand-authored rooms
        # inherit the same map-level plane when their file declares one.
        # Used by maps.render_minimap for plane-keyed color overlays -- never
        # a combat/spawn key by itself (bestiary_categories still owns that).
        self.plane = "earth"
        # Realm family for plane (prime / elemental / spirit). Derived from
        # maps.REALM_FOR_PLANE at load; Cosmic Favor tether is unrelated.
        self.realm = "prime"
        # Owning map file's "id" (e.g. "wastes", "heaven") -- stamped at
        # load so tooling can group rooms by overland. None until loaded.
        self.map_id = None
        # D29 minimap: stamped only on procedural grid cells by
        # maps._build_grid so render_minimap never has to re-parse the
        # "Prefix (x, y)" key string. Hand-authored rooms leave these
        # None -- cmd_map replies "No map here." for those.
        self.grid_prefix = None
        self.grid_x = None
        self.grid_y = None
        # Divine faith economy (Phase 2 item 8 / D20): consecrated ground
        # (chapels) boosts minister flock growth. Authored in map JSON as
        # "consecrated": true; default False so every older room stays
        # ordinary. Live-mutable the same way resources/bestiary tags are.
        self.consecrated = False
        # Temporary consecration from mortal pray (angel radio). Expires
        # when game_time_ticks >= this value; 0 means no temp consecration.
        # Permanent Room.consecrated is unaffected -- temp OR permanent
        # counts as holy for grace drip / look helpers.
        self.temp_consecrated_until = 0
        # Hellcraft circle (Occultist chalk ward). Expires when
        # game_time_ticks >= this value; 0 means no circle. Blocks Demon
        # force-possession while active (supers/hellcraft.py).
        self.hell_circle_until = 0
        # Thin D44 authored traps (supers/traps.py). Map JSON may set these.
        self.devils_trap = False
        self.salt_line = False
        self.iron_ward = False
        # Devil's Gate (D47): map-authored planar hop room. Runtime rotator
        # marks which gates are currently open; closed gates hide from look
        # exits into them (supers/demon_travel.py).
        self.devils_gate = False
        # D47/D48 Demon travel whitelist (mirror of consecrated for Angels).
        # Permanent unholy / Faustian sites Demons may dteleport to. Authored
        # in map JSON; default False so older rooms stay ordinary.
        self.unholy = False
        self.crossroads = False
        self.demon_deal = False
        # Temporary unholy from a crossroads beckon ritual. Expires when
        # game_time_ticks >= this value; 0 means none. Permanent unholy
        # flags are unaffected.
        self.temp_unholy_until = 0
        # Open-sky exposure (Vampire daylight burn, outdoor look ambient).
        # Distinct from wilderness (spawn flag): town streets can be outdoor
        # without rolling wilderness encounters. Authored as "outdoor"; when
        # omitted, maps._add_room defaults True if wilderness else False so
        # overland grids inherit outdoor without per-cell JSON. Sewers,
        # buildings, nests, and flats stay False (indoor / covered).
        self.outdoor = False
        # Floor-sleep rooms (Homeless Camp, vampire nests): sleep resource
        # without bed furniture. maps/_add_room + lodging.ensure_beds skip
        # auto-seeding beds when this is True.
        self.floor_sleep = False
        # Vampire nest lair (sewer floor-sleep dens). Cadence day-refuge
        # prefers these after a Vampire's claimed home. Authored as
        # "vampire_nest"; default False.
        self.vampire_nest = False
        # Town Clinic / hospital room (Evil Strikes Back). Authored as
        # "hospital": true in map JSON; supers/hospital.py finds these.
        self.hospital = False
        # Thin light / vision (D67): dark rooms need a carried light source
        # to see look contents. Authored as "dark": true; default False.
        self.dark = False
        # Secret exits (D66): direction strings that exist in exits{} but
        # stay hidden from look/move until a character searches (or already
        # knows them). Authored as "hidden_directions": ["east"]; default
        # empty. Validated against exits after linking in maps.py.
        self.hidden_directions = ()
        # Optional spoofed look title (Jinn mirage pockets). Storage key
        # stays unique in game.rooms; look / exit lists use look_title()
        # so the victim sees the real place name with no "fake" tell.
        self.look_key = None
        # Runtime Jinn instance id when this room is a private mirage clone.
        self.jinn_instance_id = None

    def look_title(self):
        """Player-facing room name for look / exit lists.

        Returns look_key when set (mirage spoof), otherwise key. Never
        invents dream/fake wording -- callers must not add tells either.
        """
        return self.look_key or self.key

    def add(self, obj):
        # Put an object in this room, but guard against adding it twice.
        if obj not in self.contents:
            self.contents.append(obj)
        # Keep Game.characters in sync (engine/char_index.py) so tick
        # handlers never walk ~12k rooms to rediscover ~50 actors.
        # room.game is stamped in server.Game.__init__; absent in unit
        # stubs that build Rooms without a Game.
        if isinstance(obj, Character):
            game = getattr(self, "game", None)
            if game is not None:
                from engine.char_index import register_character
                register_character(game, obj)

    def remove(self, obj):
        # Take an object out, but only if it's actually here (avoids an error).
        if obj in self.contents:
            self.contents.remove(obj)
        # Drop from the live roster only when leaving the world entirely.
        # Character.move_to removes-then-adds; the add re-registers.
        if isinstance(obj, Character):
            game = getattr(self, "game", None)
            if game is not None:
                from engine.char_index import unregister_character
                unregister_character(game, obj)

    def characters(self):
        """Return only the Characters in this room (used for broadcasting messages)."""
        # This is a "list comprehension": build a new list from self.contents,
        # keeping only the items where isinstance(o, Character) is True.
        return [o for o in self.contents if isinstance(o, Character)]

    def broadcast(self, message, exclude=None, blank_after=False):
        """Send a message to every player standing in this room.

        `exclude` skips one character or an iterable of characters —
        usually whoever caused the event (and sometimes an addressed
        listener), so they don't get a third-person line about themselves
        when they already received a second-person version. Sleeping
        characters (Character.asleep) are also skipped -- sleep closes the
        outside world (lodging); dream content will plug in later.

        When ``blank_after`` is True, each live session that received the
        message also gets an empty line (paragraph spacing). Blanks are
        not mirrored to snoopers -- same rule as snoop.tell_paragraph.
        """
        # Normalize exclude to an identity-set (same shape as gossip push).
        skip = set()
        if exclude is not None:
            if hasattr(exclude, "key") and not isinstance(
                exclude, (list, tuple, set)
            ):
                skip.add(id(exclude))
            else:
                for obj in exclude:
                    if obj is not None:
                        skip.add(id(obj))
        for char in self.characters():
            if id(char) in skip:
                continue
            # Asleep = world closed (supers.lodging); do not deliver room
            # prose until they wake.
            if getattr(char, "asleep", False):
                continue
            if char.session:
                char.session.send(message)
                if blank_after:
                    char.session.send("")
            elif getattr(char, "snoopers", None):
                # Sessionless NPC / offline Echo: still feed GM snoopers the
                # room line they would have heard if they had a Session.
                from engine import snoop
                snoop.mirror_output(char, message)
            # Activity logger (kit progression / Echo soak transcripts):
            # duck-typed -- engine never imports supers.activity_log.
            # Runs for sessioned and sessionless bodies alike.
            logger = getattr(char, "activity_logger", None)
            if logger is not None:
                try:
                    logger.seen(message)
                except Exception:
                    pass


class Character(GameObject):
    """A player (or, later, an NPC). The `session` links back to the network
    connection; NPCs leave it as None."""

    def __init__(self, key, description="An ordinary-looking person."):
        super().__init__(key, description)            # set up key/description
        self.location = None    # the Room this character is currently in (None until placed)
        self.session = None     # the network Session driving this character (None = NPC)
        self.inventory = []     # a list of Items this character is carrying
        # A salted hash (see auth.py), never the plaintext password. Empty
        # string means "no password set" -- true for characters created
        # before this feature existed; connection.py lets those reconnect
        # unchallenged but nudges the player toward 'setpass'.
        self.password_hash = ""
        # Milestone 4 (combat): who this character is currently fighting, or
        # None. Sits right alongside the other attached data -- combat.py
        # reads/clears it every tick; nothing here is Origin/Discipline-
        # specific, so it stays plain data on Character (section 1's
        # "attach, don't subclass").
        self.target = None
        # Milestone 5b (Sparring, systems doc section 4-D): does THIS
        # character's own swing this round count as non-lethal? Read fresh
        # by combat.resolve_round every round -- not tied to a "fight"
        # object, so a mid-spar double-cross (one side keeps sparring while
        # the other switches to a real attack) just resolves correctly.
        self.sparring = False
        # Section 3's Adaptation mechanic: this character's own damage
        # mitigation (0.0-1.0) built up by surviving rounds against a
        # higher-tier attacker, +0.03/round up to ADAPTATION_CAP (0.30, or
        # ADAPTATION_CAP_MUTANT 0.45 for Mutant-Origin characters).
        # Transient like .sparring/.target -- reset to 0.0 whenever a fight
        # truly ends, never persisted.
        self.adaptation = 0.0
        # Cinematic combat engine (docs/SYSTEMS_DESIGN.md section 7/9
        # item 9): a hidden per-fight gauge, -100 to +100. The prose
        # renderer (supers/combat_prose.py) still keys Opener bands off it;
        # mid-fight agency also *spends* it -- press / guard cost Momentum
        # up front (combat.py PRESS_MOMENTUM_COST / GUARD_MOMENTUM_COST).
        # Landing a hit pushes the attacker's own momentum up and the
        # defender's down, missing/being dodged does the reverse (see
        # combat.py's MOMENTUM_ATTACKER_SHIFT/MOMENTUM_DEFENDER_SHIFT).
        # Transient like .adaptation: reset to 0.0 whenever a fight truly
        # ends, never persisted.
        self.momentum = 0.0
        # Mid-fight agency (stance + one-shot intents). Stance is a
        # standing preference (persisted); intent is one swing only
        # (press/guard/feint -- never persisted). feint_exposed is set on
        # the DEFENDER by a foe's feint and consumed the next time they
        # are attacked. auto_combat_style re-arms bite/smite/maul/devour
        # each tick (persisted); auto_style_notice_tick throttles "out of
        # fuel" notices so a dry tank doesn't spam every heartbeat.
        self.combat_stance = "balanced"   # balanced | aggressive | defensive
        self.combat_intent = None         # None | "press" | "guard" | "feint"
        self.feint_exposed = False
        self.auto_combat_style = None     # None | bite | smite | maul | devour | stake | slay
        self.auto_style_notice_tick = -999
        # Perspective rendering (section 7 item 4, D17): which pronoun the
        # prose renderer's small conjugation helper uses in third person
        # ("he"/"she"/"they" -- combat_prose.py's ONLY consumer so far).
        # Identity, not fight state, so (unlike momentum/adaptation above)
        # this one IS persisted -- see persistence.py. Defaults to "they" so
        # every existing/old-save character works without ever setting it.
        # Chargen (chargen.py) and the player 'pronoun' command set this;
        # supers.appearance.build_description also reads it for the look noun.
        # Kept here (not moved to attach_supers) since the engine's own
        # `look` command reads it directly.
        self.pronoun = "they"
        # True for any non-player fixture (e.g. the training dummy, a
        # wilderness hostile) -- never a real player. Both an Echo and an
        # NPC have session=None, so anything that needs to tell them apart
        # (display, combat guardrails) checks this flag too.
        self.is_npc = False
        # Orthogonal to is_npc: can this NPC be lethally attacked at all?
        # True for a permanent, spar-only fixture (the training dummy --
        # cmd_attack redirects a lethal attack against it to sparring).
        # False for a real, ephemeral threat (Milestone F's wilderness
        # hostiles) that IS meant to be fought for real. Meaningless on a
        # real player (is_npc=False), so the default here is inert for them.
        self.spar_only = False
        # D24 / section 4-E: when True, other players may spar THIS character's
        # Echo while offline (non-lethal only -- lethal attack still rejects
        # every Echo). Default False keeps Echoes safe by default. Persisted.
        self.spar_opt_in = False
        # Transient: the online sparrer is mid rest-cycle against an Echo
        # (stamina drained, waiting to refill). Like .sparring / .target --
        # never persisted.
        self.spar_resting = False
        # Section 4-E offline regimens: which solo activity this Echo runs
        # while logged off (a key from training.ACTIVITIES), or None = idle.
        # Persisted. Takes effect whenever the character acts_as_echo()
        # (session None, or online idlemode -- see acts_as_echo()).
        self.regimen = None
        # Successful offline gains this logout stretch; capped so offline
        # never outpaces active play. Reset on reconnect; persisted so a
        # mid-logout server restart does not refresh the cap.
        self.offline_gains_this_stretch = 0
        # Player-facing idlemode: stay logged in (session attached) but run
        # as an Echo for Cadence lifestyle AI / invulnerability / look tags.
        # Cleared on reconnect so a fresh login is never stuck idle.
        # Persisted so a mid-idle copyover keeps the flag; reconnect clears it.
        self.idle_mode = False
        # Auto-idle preference: after ~30 real minutes with no typed input,
        # slip into idlemode (supers.verbs.engine_flavor). Default on;
        # toggle with `autoidle`. Persisted. last_input_tick is session-only.
        self.auto_idle = True
        self.last_input_tick = 0
        # Milestone E's catch-up mechanic: a live multiplier on this
        # character's training gain-chance, recomputed fresh every tick by
        # training.catchup_scan (1.5 while sharing a room with a much
        # stronger character, 1.0 otherwise). Like Room.gravity -- never
        # banked, never persisted, always read live at the moment of a roll.
        self.catchup_mult = 1.0
        # GM/admin tooling: "" (not a GM) / "gm" / "head_gm". A rank, not a
        # bool -- a head GM can appoint ordinary GMs later (commands.py's
        # promote/demote), which a plain True/False couldn't express.
        self.gm_rank = ""
        # Classic GM `snoop` (engine/snoop.py): live-only, never persisted.
        # snooping = who THIS character is watching (at most one); snoopers
        # = the set of GMs watching THIS character's viewpoint.
        self.snooping = None
        self.snoopers = set()
        # Legacy D31 counter (online time). Tiredness now uses Character.
        # energy (Cadence meter); online_ticks is only migrated on load via
        # rest.migrate_online_ticks, then left at 0. Kept so old saves still
        # round-trip without a schema break.
        self.online_ticks = 0
        # Lodging (rest vs sleep): awake resting recovers slowly and still
        # hears the room; asleep recovers faster and Room.broadcast skips
        # this character (world closed). dreaming is a stub for a future
        # dream-state pass -- never set True yet. sleep_bed_id is id(bed)
        # while asleep on furniture (transient, not persisted).
        self.resting = False
        self.asleep = False
        self.dreaming = False
        self.sleep_bed_id = None
        # Section 6 (Death, Body & Spirit -- D10/D11/D12/D19 resolved v0.29,
        # see docs/SYSTEMS_DESIGN.md section 9 item 7): True while this
        # character is a discorporate spirit, controlling itself but with no
        # body/HP to fight, spar, or train with. supers/death.py owns the
        # transitions; commands.py/combat.py only read this flag. Persisted
        # (like regimen/spar_opt_in above) so a spirit who logs off reloads
        # as a spirit, not silently revived by a restart.
        self.spirit = False
        # "untethered" (the short RP-safety window right after dropping) or
        # "severed" (tether actively draining) -- meaningless while
        # self.spirit is False. See supers/death.py's tick_spirits.
        self.spirit_state = None
        # How many ONLINE ticks this spirit has spent untethered so far --
        # the counter supers.death.UNTETHERED_WINDOW_TICKS compares against
        # to auto-sever. Never advances while session is None (section 6's
        # "Offline & the death layer": logging off pauses the death timer).
        self.spirit_untethered_ticks = 0
        # The SEVERED-state ceiling counts down from stats.spirit_tether_max
        # (D12) once severed; at 0 the spirit is pulled into the light
        # (supers.death._pulled_into_the_light). Real, persisted state (like
        # hp/stamina above), not derived -- it drains over time, it isn't
        # recomputed fresh each check.
        self.spirit_tether = 0.0
        # A reference to this character's own body Item (world.make_body)
        # while self.spirit is True, else None. Items have no `.location`
        # of their own (only Characters/Rooms track that), so body_room
        # below records which Room actually holds it -- a body never moves
        # once dropped (commands.cmd_get refuses to pick one up), so this
        # pairing never goes stale while the body exists.
        self.body = None
        self.body_room = None

        # Game composition (AGENTS.md rule 4) is registered via engine.hooks
        # -- SUPERS calls set_character_attacher(attach_supers) at package
        # import / server boot (docs/ENGINE_CONSUMER.md). With no attacher
        # registered this is a no-op, so a bare engine Character stays lean
        # (two-repo purity gate: docs/plans/two_repo_purity.md).
        from engine.hooks import attach_character
        attach_character(self)

    def acts_as_echo(self):
        """True for offline Echoes and online players in idlemode (§4-E).

        An Echo is not a separate class -- it is a non-NPC Character whose
        Session is detached (logout), OR a still-connected player who typed
        `idlemode on` so Cadence lifestyle AI can drive their body while
        they watch. NPCs are never Echoes (they use is_npc instead).

        Prefer this over raw `session is None` checks so idlemode and true
        logout stay in sync for invulnerability, look tags, and Echo AI.
        """
        if self.is_npc:
            return False
        if self.session is None:
            return True
        return bool(getattr(self, "idle_mode", False))

    def move_to(self, room):
        """Move this character out of its current room and into `room`."""
        old_key = getattr(self.location, "key", None) if self.location else None
        if self.location:
            # Bypass Room.remove so we do NOT drop out of game.characters
            # mid-move (remove unregisters for true despawns). The
            # destination Room.add re-asserts registration.
            if self in self.location.contents:
                self.location.contents.remove(self)
        self.location = room              # remember our new room
        room.add(self)                    # and actually enter it
        # Duck-typed Echo / kit transcript (supers.activity_log); no import.
        logger = getattr(self, "activity_logger", None)
        if logger is not None:
            new_key = getattr(room, "key", None) if room is not None else None
            if old_key != new_key:
                try:
                    logger.move(old_key, new_key)
                except Exception:
                    pass


def make_body(character):
    """Build (but don't place) the body Item left behind by a lethal drop
    (section 6, combat.py's _handle_drop). "the body of [Name]" -- a prop
    marking where a character fell; the spirit (supers/death.py) must return
    to this spot to self-anchor.

    Belongings nest in body.loot (look in / get from / drag -- suggestions.log
    #49). locked=True marks residual wards; is_body=True gates body-specific
    verbs in commands.py.
    """
    return Item(
        f"the body of {character.key}",
        f"The body of {character.key} lies here, faint wards flickering "
        "across it. Whatever made this happen, they aren't gone for good.",
        locked=True,
        is_body=True,
    )


def break_follows(character):
    """Clear any `follow` bond involving character, both directions.

    Called when a character disconnects (engine/connection.py's
    Session.disconnect) so a logged-out player's followers aren't left
    silently trying to trail a now-session-less Echo, and an Echo isn't left
    holding a stale .following reference to someone else.

    Also clears opaque companion_leader_key markers (SUPERS beckon duty)
    when present -- engine stays game-agnostic; it only wipes the attr.
    """
    target = character.following
    if target is not None and character in target.followers:
        target.followers.remove(character)
    character.following = None
    if getattr(character, "companion_leader_key", None) is not None:
        character.companion_leader_key = None
    for follower in character.followers:
        follower.following = None
        if getattr(follower, "companion_leader_key", None) is not None:
            follower.companion_leader_key = None
    character.followers = []
