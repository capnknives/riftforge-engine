"""
game_calendar.py -- pure converters from game_time_ticks to a Gregorian
calendar stack (suggestions.log #16 / docs/SYSTEMS_DESIGN.md section 9).

Display/flavor plus light world-layer hooks (season, day period, outdoor
ambient line, wilderness encounter multipliers). Training, combat, and
fatigue stay on the raw tick loop (server.TICKS_PER_GAME_DAY pacing
already validated by supers.balance_sim) -- nothing here mutates the
world. Shifter Instinct / Lunar Tide combat hooks read lunar_phase from
breakdown(); this module keeps a simplified 28 game-day moon (not real
astronomy).

Keeping this in engine/ (not supers/) matches the generic clock: no
Origin/Discipline imports, just integers in and a dict / string / float
out.

Source of truth is still Game.game_time_ticks (persisted in meta). New
servers boot with tick 0 = 2015-10-15. Long-lived servers store a
calendar_epoch_day offset so cutover "today" also reads as that date,
then the clock keeps advancing at 3x. This module never reads the
database; callers pass the tick count (and optional epoch offset) in.
"""

from datetime import date, timedelta

# Must stay in sync with server.TICKS_PER_GAME_DAY -- duplicated as a
# default so this module can be imported (and smoke-tested) without
# pulling in the full Game class. server.Game.calendar() passes its own
# constant explicitly when they ever diverge.
TICKS_PER_GAME_DAY = 9600

# 24 game-hours per game-day => 400 ticks per hour. Sub-hour units are
# derived so `time` can show HH:MM without inventing a separate clock.
HOURS_PER_DAY = 24
TICKS_PER_HOUR = TICKS_PER_GAME_DAY // HOURS_PER_DAY  # 400
MINUTES_PER_HOUR = 60
# Floor division: each "game minute" is TICKS_PER_HOUR / 60 ticks.
# With 400 ticks/hour that is not an integer -- we use the remainder
# against TICKS_PER_HOUR and scale minutes as (rem * 60) // TICKS_PER_HOUR
# so the clock never claims 60 minutes.

DAYS_PER_WEEK = 7

# Gregorian epoch: tick 0 / display day 0 = midnight on this date.
# New worlds start here; upgraded worlds rebase via calendar_epoch_day.
EPOCH_YEAR = 2015
EPOCH_MONTH = 10
EPOCH_DAY = 15
EPOCH_DATE = date(EPOCH_YEAR, EPOCH_MONTH, EPOCH_DAY)

# Lunar cycle length in game-days. 28 matches a familiar Earth-ish moon
# without claiming real astronomy -- just enough for `date` to report a
# phase label players can roleplay around. Tied to *calendar* days since
# epoch so day 0 is always a new moon.
LUNAR_CYCLE_DAYS = 28

# English month names -- index 0 unused; index 1..12 match Gregorian months.
MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# date.weekday() is Monday=0 .. Sunday=6.
WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# Meteorological northern-hemisphere seasons (month -> name).
SEASON_NAMES = (
    "spring",
    "summer",
    "autumn",
    "winter",
)

# Eight named phases around the cycle (new -> full -> new). Indexed by
# floor(day_in_cycle / (LUNAR_CYCLE_DAYS / 8)).
LUNAR_PHASE_NAMES = (
    "new moon",
    "waxing crescent",
    "first quarter",
    "waxing gibbous",
    "full moon",
    "waning gibbous",
    "last quarter",
    "waning crescent",
)

# Wilderness encounter chance multipliers (world layer only). Stacked
# multiplicatively in wilderness_encounter_mult(), then clamped.
_SEASON_ENCOUNTER_MULT = {
    "spring": 1.0,
    "summer": 1.1,
    "autumn": 1.0,
    "winter": 0.85,
}
_PERIOD_ENCOUNTER_MULT = {
    "day": 1.0,
    "dawn": 1.15,
    "dusk": 1.15,
    "night": 1.35,
}
_LUNAR_ENCOUNTER_MULT = {
    "full moon": 1.2,
    "new moon": 0.9,
}
_ENCOUNTER_MULT_MIN = 0.5
_ENCOUNTER_MULT_MAX = 1.75

# Outdoor look ambient: season -> day_period -> opening clause. Lunar
# phrase is appended separately so full/new moon always reads clearly.
_AMBIENT_OPENING = {
    "spring": {
        "night": "A cool spring night",
        "dawn": "A misty spring dawn",
        "day": "A mild spring day",
        "dusk": "A soft spring dusk",
    },
    "summer": {
        "night": "A warm summer night",
        "dawn": "A bright summer dawn",
        "day": "A hot summer day",
        "dusk": "A long summer dusk",
    },
    "autumn": {
        "night": "A crisp autumn night",
        "dawn": "A chill autumn dawn",
        "day": "A clear autumn day",
        "dusk": "A fading autumn dusk",
    },
    "winter": {
        "night": "A cold winter night",
        "dawn": "A pale winter dawn",
        "day": "A sharp winter day",
        "dusk": "A short winter dusk",
    },
}

# Module-level display epoch for callers that only have ticks (e.g.
# Shifter lunar helpers). Game.__init__ sets this after loading
# calendar_epoch_day so breakdown() without an explicit offset still
# matches Game.calendar(). Pure unit tests should pass epoch_day_offset=0.
_active_epoch_day_offset = 0


def set_active_epoch_day_offset(offset):
    """Publish the live world's calendar_epoch_day for tick-only callers.

    Single-threaded asyncio: one Game owns the process, so a module
    global is enough for instinct/fuel paths that never see Game.
    """
    global _active_epoch_day_offset
    _active_epoch_day_offset = max(0, int(offset))


def season_for_month(month):
    """Meteorological northern-hemisphere season for Gregorian month 1..12."""
    month = int(month)
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def day_period_for_hour(hour):
    """Map a 0..23 clock hour to night / dawn / day / dusk.

    Night matches supers.needs energy decay (hour >= 22 or hour < 6) so
    the calendar and NPC sleepiness agree on what "night" means. Dawn and
    dusk are short shoulders around daylight.
    """
    hour = int(hour) % HOURS_PER_DAY
    if hour >= 22 or hour < 6:
        return "night"
    if hour < 8:
        return "dawn"
    if hour < 18:
        return "day"
    return "dusk"


def breakdown(ticks, ticks_per_day=TICKS_PER_GAME_DAY, epoch_day_offset=None):
    """Convert an absolute tick count into a Gregorian calendar dict.

    All fields are integers (or str for labels). With epoch_day_offset=0,
    tick 0 is midnight on EPOCH_DATE (2015-10-15), autumn, night, new moon.
    epoch_day_offset is the absolute game-day that maps to that epoch --
    rebasing a live world so "today" becomes 2015-10-15 without resetting
    game_time_ticks. When omitted, uses the module-level offset Game set
    at boot (or 0 before any Game exists).

    Returns keys:
      ticks, day_index (0-based absolute game-day from tick 0),
      calendar_day (days since Gregorian epoch, >= 0),
      second, minute, hour (clock-of-day),
      day_of_month, month (1..12), month_name, year (Gregorian),
      week_of_year (ISO), day_of_week (1=Mon..7=Sun), weekday_name,
      lunar_day (0..LUNAR_CYCLE_DAYS-1), lunar_phase,
      season, season_name, day_period,
      era_name (empty -- kept for older call sites)
    """
    ticks = max(0, int(ticks))
    if epoch_day_offset is None:
        epoch_day_offset = _active_epoch_day_offset
    epoch_day_offset = max(0, int(epoch_day_offset))

    day_index = ticks // ticks_per_day
    rem = ticks % ticks_per_day

    # Sub-day clock from the remainder of today's ticks.
    hour = rem // TICKS_PER_HOUR
    hour_rem = rem % TICKS_PER_HOUR
    minute = (hour_rem * MINUTES_PER_HOUR) // TICKS_PER_HOUR
    # "Seconds" are a fine-grained leftover for completeness (#16 asked
    # for the full stack). Scale the leftover ticks inside the current
    # minute into 0..59.
    ticks_per_minute = max(1, TICKS_PER_HOUR // MINUTES_PER_HOUR)
    minute_rem = hour_rem - (minute * TICKS_PER_HOUR // MINUTES_PER_HOUR)
    second = min(59, (minute_rem * 60) // ticks_per_minute)

    # Display day relative to the Gregorian epoch (never negative).
    calendar_day = max(0, day_index - epoch_day_offset)
    greg = EPOCH_DATE + timedelta(days=calendar_day)

    year = greg.year
    month = greg.month
    day_of_month = greg.day
    month_name = MONTH_NAMES[month]
    # ISO: week 1..53, weekday 1=Monday .. 7=Sunday.
    iso = greg.isocalendar()
    week_of_year = iso.week
    day_of_week = iso.weekday
    weekday_name = WEEKDAY_NAMES[greg.weekday()]

    lunar_day = calendar_day % LUNAR_CYCLE_DAYS
    # Eight equal-ish buckets across the cycle.
    phase_bucket = (lunar_day * len(LUNAR_PHASE_NAMES)) // LUNAR_CYCLE_DAYS
    lunar_phase = LUNAR_PHASE_NAMES[phase_bucket]

    season = season_for_month(month)
    day_period = day_period_for_hour(hour)

    return {
        "ticks": ticks,
        "day_index": day_index,
        "calendar_day": calendar_day,
        "second": second,
        "minute": minute,
        "hour": hour,
        "day_of_month": day_of_month,
        "month": month,
        "month_name": month_name,
        "year": year,
        "week_of_year": week_of_year,
        "day_of_week": day_of_week,
        "weekday_name": weekday_name,
        "lunar_day": lunar_day,
        "lunar_phase": lunar_phase,
        "season": season,
        "season_name": season,
        "day_period": day_period,
        "era_name": "",
    }


def format_clock(cal, fmt="24h"):
    """HH:MM string from a breakdown() dict (zero-padded).

    fmt is a per-player display preference (suggestions.log #46), not a
    calendar concept -- the underlying hour/minute are identical either
    way, only the rendering changes. "12h" shows 12-hour clock + AM/PM;
    anything else (including the "24h" default) keeps the original
    zero-padded 24-hour string.
    """
    if fmt == "12h":
        hour_12 = cal["hour"] % 12 or 12   # 0 and 12 both display as 12
        suffix = "AM" if cal["hour"] < 12 else "PM"
        return f"{hour_12}:{cal['minute']:02d} {suffix}"
    return f"{cal['hour']:02d}:{cal['minute']:02d}"


def format_date(cal):
    """One-line full-stack date string for the `date` command."""
    return (
        f"{cal['weekday_name']}, {cal['month_name']} {cal['day_of_month']}, "
        f"{cal['year']} (week {cal['week_of_year']}). "
        f"Season: {cal['season']}. Moon: {cal['lunar_phase']}."
    )


def format_ambient(cal):
    """One outdoor look line from season + day period + lunar phase.

    Meaning lives in the words (accessibility: never color alone). Callers
    should only show this when room.wilderness is true -- city/ruins stay
    quiet so indoor rooms do not claim a sky.
    """
    season = cal.get("season", "spring")
    period = cal.get("day_period", "day")
    # Nested .get with a spring/day fallback so a partial test dict still
    # produces a readable line instead of KeyError.
    opening = _AMBIENT_OPENING.get(season, _AMBIENT_OPENING["spring"]).get(
        period, "A quiet day"
    )
    phase = cal.get("lunar_phase", "")
    if phase == "full moon":
        return f"{opening} under a full moon."
    if phase == "new moon":
        return f"{opening} under a new moon."
    return f"{opening}. The moon is {phase}."


def wilderness_encounter_mult(cal):
    """Float multiplier for WILDERNESS_ENCOUNTER_CHANCE from the calendar.

    Season, day period, and lunar phase stack multiplicatively, then the
    product is clamped to [_ENCOUNTER_MULT_MIN, _ENCOUNTER_MULT_MAX] so a
    weird combo cannot drive odds to zero or absurd heights. Unknown keys
    default to 1.0 (no change). Pure function -- callers pass a breakdown
    dict; this never reads Game state.
    """
    season = cal.get("season", "spring")
    period = cal.get("day_period", "day")
    phase = cal.get("lunar_phase", "")
    mult = (
        _SEASON_ENCOUNTER_MULT.get(season, 1.0)
        * _PERIOD_ENCOUNTER_MULT.get(period, 1.0)
        * _LUNAR_ENCOUNTER_MULT.get(phase, 1.0)
    )
    # min/max clamp: keep the result inside the designed band.
    return max(_ENCOUNTER_MULT_MIN, min(_ENCOUNTER_MULT_MAX, mult))
