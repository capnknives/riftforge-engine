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
_CLIMATE_DIR = os.path.join(_CONTENT_DIR, "climate")


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
    from engine.systems import regional_weather as regional_weather_module

    maps.set_maps_dir(_MAPS_DIR)
    maps.set_zones_dir(_ZONES_DIR)
    regional_weather_module.set_climate_pack_path(
        os.path.join(_CLIMATE_DIR, "conus_normals.json")
    )
    hooks.set_chargen(chargen.run)
    hooks.set_help(help_topics.HELP_TOPICS, help_topics.HELP_CATEGORIES)
    hooks.set_dispatch(commands.dispatch)
    hooks.set_weather_look_clause(regional_weather_module.look_clause)
    hooks.set_weather_look_vision(regional_weather_module.assess_look_vision)

    # Dual-layer America overland (engine/systems/overland.py).
    from engine.systems import overland as overland_mod

    def _ensure_game_defaults(game):
        overland_mod.ensure_game_overland(game)
        overland_mod.stamp_pocket_overland_exits(game)

    hooks.set_ensure_game_defaults(_ensure_game_defaults)

    def _map_center_room(character, game):
        macro = overland_mod._parse_pos_pair(
            getattr(character, "macro_pos", None)
        )
        rooms = getattr(game, "rooms", None) or {}
        if macro is None:
            loc = getattr(character, "location", None)
            if loc is None:
                return None
            macro = overland_mod.current_zone_macro(game, loc)
            if macro is None:
                return None
        key = overland_mod.america_cell_key(*macro)
        return rooms.get(key)

    def _after_zone_enter(character, game, dest):
        overland_mod.clear_overland_coords(character)

    hooks.set_map_center_room(_map_center_room)

    def _try_directional_move(character, direction, game):
        from engine.systems import aerial as aerial_mod
        from engine.systems import globe_flight as globe_flight_mod
        if aerial_mod.flight_tier(character) == "globe":
            return globe_flight_mod.try_globe_fly_move(character, direction, game)
        return overland_mod.try_overland_move(character, direction, game)

    hooks.set_try_directional_move(_try_directional_move)
    hooks.set_try_enter_zone(overland_mod.try_enter_landmark)
    hooks.set_try_exit_zone(overland_mod.try_exit_to_overland)
    hooks.set_after_zone_enter(_after_zone_enter)
    hooks.set_clear_overland_coords(overland_mod.clear_overland_coords)

    def _storm_on_duty(character, game=None):
        return bool(getattr(character, "on_duty", False))

    hooks.set_storm_chase_is_on_duty(_storm_on_duty)

    from engine.systems import mail as mail_mod

    def _after_session_attach(character, game):
        mail_mod.notify_inbox(character, game)

    hooks.set_after_session_attach(_after_session_attach)
