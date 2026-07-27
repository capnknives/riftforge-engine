"""weather.py -- a small, genuinely generic ambient weather condition.

Not SUPERS' CONUS climatology (supers/weather.py) -- that module is
real-world regional forecasting tied to the America atlas, Reach elemental
planes, and Tier-scaled tornado damage. It was originally scoped to move
here wholesale (docs/plans/two_repo_purity.md Stage 3), but reading it
during that stage showed it's authored SUPERS content, not a generic
mechanism -- extracting it would mean lifting lore-specific machinery, not
factoring out something reusable. Same story for supers/storm_watch.py (a
specific Tornado Hunter job) and supers/daylight.py (Vampire outdoor sun
damage). All three stay in SUPERS untouched; this module is new, written
from scratch to be the actually-generic version.

One global condition per Game that changes periodically, with a
look-clause default any game can use via engine.hooks.set_weather_look_clause
as-is, or override entirely the way SUPERS overrides it with its own
climatology. A game with richer needs (regions, hazards, seasons feeding
back into the roll) layers that on top the same way SUPERS layers Tiers
onto engine/systems/... elsewhere -- this module only owns the minimal
shared shape.
"""

import random

# Default vocabulary + each game's own default look text. A game may swap
# both via set_conditions() without touching this module -- e.g. a snowier
# setting could weight "snow" higher or rename "storm" to "blizzard".
DEFAULT_CONDITIONS = ("clear", "cloudy", "rain", "storm", "snow")

DEFAULT_LOOK_CLAUSES = {
    "clear": "The sky is clear.",
    "cloudy": "Clouds hang low overhead.",
    "rain": "A steady rain falls.",
    "storm": "Thunder rolls somewhere close.",
    "snow": "Snow drifts down, quiet and steady.",
}

# How often the condition rerolls -- 2400 ticks is a quarter of a
# TICKS_PER_GAME_DAY=9600 game-day (see engine/game_calendar.py), so
# weather visibly shifts a few times per day without changing every look.
ROLL_INTERVAL_TICKS = 2400

_conditions = DEFAULT_CONDITIONS
_look_clauses = DEFAULT_LOOK_CLAUSES


def set_conditions(conditions, look_clauses=None):
    """Override the condition vocabulary (and optionally their look text).

    Pass conditions=None to restore the built-in defaults. look_clauses,
    when given, must cover every id in `conditions` -- an unmapped id
    falls back to an empty clause rather than raising, so a game that
    only wants to reweight without renaming can pass look_clauses=None.
    """
    global _conditions, _look_clauses
    _conditions = tuple(conditions) if conditions else DEFAULT_CONDITIONS
    _look_clauses = dict(look_clauses) if look_clauses else DEFAULT_LOOK_CLAUSES


def current_condition(game):
    """This game's current weather condition, rolling a first one if unset."""
    if not hasattr(game, "weather_condition"):
        _reroll(game)
    return game.weather_condition


def tick(game):
    """Reroll the condition once ROLL_INTERVAL_TICKS have passed.

    Registered as a Game.on_tick handler by a game's own tick_bootstrap
    (see basegame/tick_bootstrap.py) -- engine/tick_registry.py runs
    whatever's registered; this module never registers itself.
    """
    now = getattr(game, "game_time_ticks", 0)
    if not hasattr(game, "weather_next_roll_tick") or now >= game.weather_next_roll_tick:
        _reroll(game)


def _reroll(game):
    """Pick a new condition and schedule the next reroll."""
    game.weather_condition = random.choice(_conditions)
    now = getattr(game, "game_time_ticks", 0)
    game.weather_next_roll_tick = now + ROLL_INTERVAL_TICKS


def look_clause(room, game, screenreader=False, character=None):
    """engine.hooks.set_weather_look_clause target: one ambient sentence.

    Indoor rooms get no weather clause, same convention SUPERS uses --
    weather is only worth mentioning where a character can actually see
    the sky. screenreader/character are accepted (not used) so this drops
    in as a same-signature swap for a game's own richer implementation.
    """
    if room is None or not getattr(room, "outdoor", False):
        return None
    condition = current_condition(game)
    return _look_clauses.get(condition, "")
