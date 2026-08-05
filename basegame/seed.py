"""seed.py -- idempotent basegame world backfill, run once at Game boot.

Mirrors supers.boot_seed.seed_content's role but scoped to what
basegame actually needs, called from game_select.seed_content (see
server.py's Game.__init__). The skeleton reference town ships entirely as
static map JSON (basegame/content/), so there is nothing to backfill yet --
this exists as the seam later basegame stages (job boards, nests, cases)
hang their one-time setup on.
"""


def seed_content(game):
    """Stamp overland atlas + pocket exits after maps load."""
    from engine import hooks
    from engine.systems import lodging as lodging_mod
    from world import Item

    hooks.ensure_game_defaults(game)
    inn = game.rooms.get("NB00014")
    if inn is not None and not lodging_mod.beds_in_room(inn):
        bed = Item(
            "a bunk bed",
            "A simple bunk with a thin mattress.",
            furniture=True,
        )
        bed.need = "sleep"
        inn.contents.append(bed)
        lodging_mod.stamp_home_basics(inn)
