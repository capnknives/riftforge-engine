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
    hooks.ensure_game_defaults(game)
