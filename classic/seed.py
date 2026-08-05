"""seed.py -- idempotent classic boot backfill."""

def seed_content(game):
    """Validate content and register tick handlers after maps load."""
    from classic.content_validate import validate_all_content
    from classic.tick_bootstrap import register_default_ticks

    validate_all_content()
    register_default_ticks(game)
