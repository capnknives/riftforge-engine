"""character_attach.py -- the basegame half of Character construction.

Mirrors supers/character_attach.py's role (AGENTS.md rule 4: attach data to
Character, don't subclass): engine/world.py's Character stays generic, and
every basegame-composed field lives here instead. Called once, from
Character.__init__'s final step, via the set_character_attacher hook
(registered in basegame/bootstrap.py.register_core_hooks).

Field names are prefixed ``bg_`` so they read unambiguously next to SUPERS'
own field names in shared docs/examples, even though the two games never
run in the same process (game_select.py picks exactly one). ``character.hp``
is the one exception -- it's generic engine vitals storage now (see
engine/world.py's Character.__init__), not a basegame invention, so it
keeps its shared, unprefixed name. ``character.stats`` / ``character.tier``
are the same story -- the six-primary spine is generic engine content now
(engine/stats.py), already set to its default before this function runs, so
there's no ``bg_stats`` here.
"""

from basegame import needs as needs_module
from basegame import stats as stats_module
import engine.systems.economy as economy_wallet


def attach_basegame(character):
    """Attach every basegame-composed field onto a freshly-built `character`.

    Must be safe to call on a freshly-built Character: only sets defaults,
    never reads/assumes a field already exists (same contract as SUPERS'
    attach_supers).
    """
    # Chosen at chargen (basegame.chargen.run). None until then so a
    # mid-chargen disconnect never leaves a "path-less" character loose in
    # the world -- connection.py only places a character after chargen
    # returns True.
    character.bg_path = None
    # character.hp is engine-owned storage; fill it with basegame's own
    # max-hp formula as soon as character.stats exists (it always does --
    # engine/world.py's Character.__init__ sets the shared spine before any
    # game's attach step runs) so a Character built outside chargen (smoke,
    # NPCs) is never left at hp=0.
    character.hp = stats_module.max_hp(character)
    # Text mail inbox (engine/systems/mail.py) -- same shape SUPERS uses.
    character.mail_inbox = []
    economy_wallet.set_wallet(character, 0, 0)
    character.job = None
    character.on_duty = False
    character.chase_id = None
    character.chase_brief = None
    character.chase_flags = {}
    character.bg_stellar = False
    character.solar_charge = 1.0
    character.stellar_flight_tier = "ground"
    character.stellar_globe_lon = None
    character.stellar_globe_lat = None
    character.stellar_flight_macro = None
    character.orbit_return_room = None
    needs_module.attach_character(character)
