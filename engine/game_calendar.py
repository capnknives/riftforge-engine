"""
game_calendar.py -- facade over the active CalendarProvider + shared clock.

Display/flavor plus light world-layer hooks (season, day period, outdoor
ambient line, wilderness encounter multipliers). Training, combat, and
fatigue stay on the raw tick loop (server.TICKS_PER_GAME_DAY pacing
already validated by supers.balance_sim) -- nothing here mutates the
world.

Source of truth is still Game.game_time_ticks (persisted in meta). Games
register a CalendarProvider at boot (SUPERS -> Gregorian); swap the
provider to use a custom calendar (e.g. D&D tendays) without forking
engine verbs. See engine/calendar_provider.py and engine/hooks.py.

This module re-exports clock constants and Gregorian epoch symbols for
backward compatibility with existing imports.
"""

__all__ = [
    "CalendarProvider",
    "EPOCH_DATE",
    "EPOCH_DAY",
    "EPOCH_MONTH",
    "EPOCH_YEAR",
    "LUNAR_CYCLE_DAYS",
    "LUNAR_PHASE_NAMES",
    "MONTH_NAMES",
    "SEASON_NAMES",
    "WEEKDAY_NAMES",
    "GregorianCalendar",
    "HOURS_PER_DAY",
    "MINUTES_PER_HOUR",
    "TICKS_PER_GAME_DAY",
    "TICKS_PER_HOUR",
    "DAYS_PER_WEEK",
    "breakdown",
    "day_phase",
    "day_period_for_hour",
    "format_ambient",
    "format_clock",
    "format_date",
    "get_calendar_provider",
    "season_for_month",
    "set_active_epoch_day_offset",
    "set_calendar_provider",
    "wilderness_encounter_mult",
]

from engine.calendar_provider import (  # noqa: F401
    CalendarProvider,
    day_period_for_hour,
    get_calendar_provider,
    set_active_epoch_day_offset,
    set_calendar_provider,
)
from engine.calendars.gregorian import (  # noqa: F401
    EPOCH_DATE,
    EPOCH_DAY,
    EPOCH_MONTH,
    EPOCH_YEAR,
    LUNAR_CYCLE_DAYS,
    LUNAR_PHASE_NAMES,
    MONTH_NAMES,
    SEASON_NAMES,
    WEEKDAY_NAMES,
    GregorianCalendar,
    season_for_month,
)
from engine.game_clock import (  # noqa: F401
    HOURS_PER_DAY,
    MINUTES_PER_HOUR,
    TICKS_PER_GAME_DAY,
    TICKS_PER_HOUR,
)

DAYS_PER_WEEK = 7


def breakdown(ticks, ticks_per_day=TICKS_PER_GAME_DAY, epoch_day_offset=None):
    """Convert game_time_ticks through the active CalendarProvider."""
    return get_calendar_provider().breakdown(
        ticks,
        ticks_per_day=ticks_per_day,
        epoch_day_offset=epoch_day_offset,
    )


def day_phase(ticks, ticks_per_day=TICKS_PER_GAME_DAY, epoch_day_offset=None):
    """Return day_period (night/dawn/day/dusk) for a tick count."""
    cal = breakdown(
        ticks,
        ticks_per_day=ticks_per_day,
        epoch_day_offset=epoch_day_offset,
    )
    return cal.get("day_period", "day")


def format_clock(cal, fmt="24h"):
    """HH:MM string from a breakdown() dict (zero-padded).

    fmt is a per-player display preference (suggestions.log #46), not a
    calendar concept -- the underlying hour/minute are identical either
    way, only the rendering changes.
    """
    if fmt == "12h":
        hour_12 = cal["hour"] % 12 or 12
        suffix = "AM" if cal["hour"] < 12 else "PM"
        return f"{hour_12}:{cal['minute']:02d} {suffix}"
    return f"{cal['hour']:02d}:{cal['minute']:02d}"


def format_date(cal):
    """One-line full-stack date string for the `date` command."""
    return get_calendar_provider().format_date(cal)


def format_ambient(cal):
    """One outdoor look line from season + day period + lunar phase."""
    return get_calendar_provider().format_ambient(cal)


def wilderness_encounter_mult(cal):
    """Float multiplier for WILDERNESS_ENCOUNTER_CHANCE from the calendar."""
    return get_calendar_provider().wilderness_encounter_mult(cal)
