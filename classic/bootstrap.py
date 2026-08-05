"""bootstrap.py -- register classic hooks on the RiftForge engine."""

import os

from engine import hooks

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_MAPS_DIR = os.path.join(_CONTENT_DIR, "maps")
_ZONES_DIR = os.path.join(_CONTENT_DIR, "zones")


def register_core_hooks():
    """Character attach, blob codec, stat hooks."""
    from classic.character_attach import attach_classic
    from classic.persist_blob import apply_character_blob, character_to_blob
    from classic import stats as stats_module

    hooks.set_character_attacher(attach_classic)
    hooks.set_blob_codec(character_to_blob, apply_character_blob)
    stats_module.register_hooks()
    _register_content_kinds_hooks()


def _register_content_kinds_hooks():
    from engine.content_kinds import engine as content_kinds_engine

    kinds_root = os.path.join(_CONTENT_DIR, "kinds")
    hooks.set_content_kinds_dirs([
        content_kinds_engine.default_engine_kinds_dir(),
        kinds_root,
    ])


def register_all_hooks():
    """Full hook set + point maps loader at classic content."""
    register_core_hooks()

    import commands
    import maps
    from classic import chargen
    from classic import help_topics

    maps.set_maps_dir(_MAPS_DIR)
    maps.set_zones_dir(_ZONES_DIR)
    hooks.set_chargen(chargen.run)
    hooks.set_help(help_topics.HELP_TOPICS, help_topics.HELP_CATEGORIES)
    hooks.set_dispatch(commands.dispatch)

    from classic.content_validate import validate_all_content
    validate_all_content()

    # Generic osr swing engine + classic resolver hooks + round tick wiring.
    from engine.systems import combat_osr  # noqa: F401
    from classic.rules.osr_resolvers import register_classic_osr_resolvers
    register_classic_osr_resolvers()
    from classic import combat as _combat_mod  # noqa: F401


def register_default_ticks(game):
    """Delegate to tick_bootstrap."""
    from classic.tick_bootstrap import register_default_ticks as fn
    fn(game)
