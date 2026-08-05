"""Stock Gregorian calendar provider (SUPERS default Earth fiction)."""

from datetime import date, timedelta

from engine.calendar_provider import (
    CalendarProvider,
    active_epoch_day_offset,
    clock_fields_from_ticks,
    day_period_for_hour,
)
from engine.game_clock import TICKS_PER_GAME_DAY

# Gregorian epoch: tick 0 / display day 0 = midnight on this date.
EPOCH_YEAR = 2015
EPOCH_MONTH = 10
EPOCH_DAY = 15
EPOCH_DATE = date(EPOCH_YEAR, EPOCH_MONTH, EPOCH_DAY)

DAYS_PER_WEEK = 7

# Lunar cycle length in game-days. 28 matches a familiar Earth-ish moon
# without claiming real astronomy.
LUNAR_CYCLE_DAYS = 28

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

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

SEASON_NAMES = (
    "spring",
    "summer",
    "autumn",
    "winter",
)

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


class GregorianCalendar(CalendarProvider):
    """Gregorian calendar stack + northern seasons + simplified 28-day moon."""

    calendar_id = "gregorian"
    display_name = "Gregorian (Earth)"

    def breakdown(
        self,
        ticks,
        *,
        ticks_per_day=TICKS_PER_GAME_DAY,
        epoch_day_offset=None,
    ):
        ticks = max(0, int(ticks))
        if epoch_day_offset is None:
            epoch_day_offset = active_epoch_day_offset()
        epoch_day_offset = max(0, int(epoch_day_offset))

        day_index = ticks // ticks_per_day
        rem = ticks % ticks_per_day
        hour, minute, second = clock_fields_from_ticks(rem)

        calendar_day = max(0, day_index - epoch_day_offset)
        greg = EPOCH_DATE + timedelta(days=calendar_day)

        year = greg.year
        month = greg.month
        day_of_month = greg.day
        month_name = MONTH_NAMES[month]
        iso = greg.isocalendar()
        week_of_year = iso.week
        day_of_week = iso.weekday
        weekday_name = WEEKDAY_NAMES[greg.weekday()]

        lunar_day = calendar_day % LUNAR_CYCLE_DAYS
        phase_bucket = (lunar_day * len(LUNAR_PHASE_NAMES)) // LUNAR_CYCLE_DAYS
        lunar_phase = LUNAR_PHASE_NAMES[phase_bucket]

        season = self.season_for_month(month)
        period = self.day_period_for_hour(hour)

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
            "day_period": period,
            "era_name": "",
        }

    def format_date(self, cal):
        return (
            f"{cal['weekday_name']}, {cal['month_name']} "
            f"{cal['day_of_month']}, "
            f"{cal['year']} (week {cal['week_of_year']}). "
            f"Season: {cal['season']}. Moon: {cal['lunar_phase']}."
        )

    def format_ambient(self, cal):
        season = cal.get("season", "spring")
        period = cal.get("day_period", "day")
        opening = _AMBIENT_OPENING.get(season, _AMBIENT_OPENING["spring"]).get(
            period, "A quiet day"
        )
        phase = cal.get("lunar_phase", "")
        if phase == "full moon":
            return f"{opening} under a full moon."
        if phase == "new moon":
            return f"{opening} under a new moon."
        return f"{opening}. The moon is {phase}."

    def wilderness_encounter_mult(self, cal):
        season = cal.get("season", "spring")
        period = cal.get("day_period", "day")
        phase = cal.get("lunar_phase", "")
        mult = (
            _SEASON_ENCOUNTER_MULT.get(season, 1.0)
            * _PERIOD_ENCOUNTER_MULT.get(period, 1.0)
            * _LUNAR_ENCOUNTER_MULT.get(phase, 1.0)
        )
        return max(_ENCOUNTER_MULT_MIN, min(_ENCOUNTER_MULT_MAX, mult))

    def season_for_month(self, month):
        return season_for_month(month)

    def day_period_for_hour(self, hour):
        return day_period_for_hour(hour)
