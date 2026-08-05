"""
calendar_provider.py -- pluggable calendar registry for the engine.

Games register a CalendarProvider at boot (SUPERS uses the stock Gregorian
calendar; a fantasy setting could swap in a custom calendar without forking
engine/verbs). The engine keeps game_time_ticks and calendar_epoch_day;
providers only translate ticks -> display dicts and flavor hooks.

Single-threaded asyncio: one Game owns the process, so a module-level
provider slot is enough.
"""

from engine.game_clock import (
    HOURS_PER_DAY,
    MINUTES_PER_HOUR,
    TICKS_PER_GAME_DAY,
    TICKS_PER_HOUR,
)

# Module-level display epoch for callers that only have ticks (e.g. Shifter
# lunar helpers). Game.__init__ sets this after loading calendar_epoch_day
# so breakdown() without an explicit offset still matches Game.calendar().
_active_epoch_day_offset = 0

# Optional override; None => built-in Gregorian default (lazy).
_calendar_provider = None
_default_provider = None


def set_active_epoch_day_offset(offset):
    """Publish the live world's calendar_epoch_day for tick-only callers."""
    global _active_epoch_day_offset
    _active_epoch_day_offset = max(0, int(offset))


def active_epoch_day_offset():
    """Return the module-level epoch offset Game published at boot."""
    return _active_epoch_day_offset


def set_calendar_provider(provider):
    """Install the active calendar (called from engine.hooks at game boot)."""
    global _calendar_provider
    if provider is None:
        raise ValueError("calendar provider cannot be None")
    _calendar_provider = provider


def get_calendar_provider():
    """Return the active calendar, falling back to stock Gregorian."""
    global _default_provider
    if _calendar_provider is not None:
        return _calendar_provider
    if _default_provider is None:
        from engine.calendars.gregorian import GregorianCalendar

        _default_provider = GregorianCalendar()
    return _default_provider


def day_period_for_hour(hour):
    """Map a 0..23 clock hour to night / dawn / day / dusk.

    Shared default used by Gregorian and most game calendars. Night matches
    supers.needs energy decay (hour >= 22 or hour < 6) so the calendar and
    NPC sleepiness agree on what "night" means.
    """
    hour = int(hour) % HOURS_PER_DAY
    if hour >= 22 or hour < 6:
        return "night"
    if hour < 8:
        return "dawn"
    if hour < 18:
        return "day"
    return "dusk"


def clock_fields_from_ticks(rem, *, ticks_per_hour=TICKS_PER_HOUR):
    """Derive hour/minute/second from the sub-day tick remainder.

    Shared helper for CalendarProvider.breakdown() implementations.
    """
    hour = rem // ticks_per_hour
    hour_rem = rem % ticks_per_hour
    minute = (hour_rem * MINUTES_PER_HOUR) // ticks_per_hour
    ticks_per_minute = max(1, ticks_per_hour // MINUTES_PER_HOUR)
    minute_rem = hour_rem - (minute * ticks_per_hour // MINUTES_PER_HOUR)
    second = min(59, (minute_rem * 60) // ticks_per_minute)
    return hour, minute, second


class CalendarProvider:
    """Interface for tick -> calendar display + light world flavor hooks.

    Subclasses implement breakdown() and may override formatters / encounter
    multipliers. Games install one instance via hooks.set_calendar_provider().
    """

    calendar_id = "generic"
    display_name = "Generic calendar"

    def breakdown(
        self,
        ticks,
        *,
        ticks_per_day=TICKS_PER_GAME_DAY,
        epoch_day_offset=None,
    ):
        """Convert absolute game_time_ticks into a calendar dict.

        Standard keys (callers across engine/ and supers/ expect these):
          ticks, day_index, calendar_day, second, minute, hour,
          day_of_month, month, month_name, year, week_of_year,
          day_of_week, weekday_name, lunar_day, lunar_phase,
          season, season_name, day_period, era_name
        """
        raise NotImplementedError

    def format_date(self, cal):
        """One-line full-stack date string for the `date` command."""
        raise NotImplementedError

    def format_ambient(self, cal):
        """One outdoor look line from season + day period + lunar phase."""
        raise NotImplementedError

    def wilderness_encounter_mult(self, cal):
        """Float multiplier for wilderness encounter chance from calendar."""
        return 1.0

    def season_for_month(self, month):
        """Season label for a calendar month (meaning varies by calendar)."""
        return "spring"

    def day_period_for_hour(self, hour):
        """Day/night bucket for a clock hour."""
        return day_period_for_hour(hour)
