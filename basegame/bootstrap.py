"""bootstrap.py -- register basegame hooks on the RiftForge engine.

Mirrors supers/bootstrap.py's two-tier shape: register_core_hooks() (attach
+ blob + stat hooks) runs at package import (basegame/__init__.py) so any
code that builds a Character without going through server.py still gets a
full attach; register_all_hooks() adds chargen/help/dispatch and is what
game_select.py calls at process start (see game_select.py's docstring for
why server.py never imports basegame directly).

Keeps engine/ free of `import basegame` -- the game reaches into
engine.hooks instead (docs/ENGINE_CONSUMER.md).
"""

import os

from engine import hooks

# basegame ships its own tiny reference town instead of the SUPERS-scale
# content/maps + content/zones trees -- point the shared maps.py loader at
# this package's own content/ before anything calls build_world().
_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_MAPS_DIR = os.path.join(_CONTENT_DIR, "maps")
_ZONES_DIR = os.path.join(_CONTENT_DIR, "zones")


def register_core_hooks():
    """Attach Character composition + persistence blob codec + stat hooks.

    Safe to call more than once (idempotent replace) -- same contract as
    supers.bootstrap.register_core_hooks.
    """
    from basegame.character_attach import attach_basegame
    from basegame.persist_blob import apply_character_blob, character_to_blob
    from basegame import stats as stats_module

    hooks.set_character_attacher(attach_basegame)
    hooks.set_blob_codec(character_to_blob, apply_character_blob)
    stats_module.register_hooks()


def register_all_hooks():
    """Core hooks plus chargen, help topic injection, and command dispatch.

    Game entry points (server.py, via game_select.py) call this before
    Game() so new logins and `help` see basegame content -- including
    pointing the shared maps.py loader at basegame's own content/ before
    server.py's Game.__init__ calls build_world() (must happen here, not
    in register_core_hooks, since that also runs for callers -- tests,
    scripts -- that just want a lean Character attach without touching
    global map-loader state).
    """
    register_core_hooks()

    import commands
    import maps
    from basegame import chargen
    from basegame import help_topics
    from engine.systems import weather as weather_module

    maps.set_maps_dir(_MAPS_DIR)
    maps.set_zones_dir(_ZONES_DIR)
    hooks.set_chargen(chargen.run)
    hooks.set_help(help_topics.HELP_TOPICS, help_topics.HELP_CATEGORIES)
    hooks.set_dispatch(commands.dispatch)
    hooks.set_weather_look_clause(weather_module.look_clause)
