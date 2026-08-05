"""
game_clock.py -- shared tick pacing constants (calendar-agnostic).

The compressed game clock (ticks, hours, minutes) is engine infrastructure.
Calendar *labels* (Gregorian month names, D&D tenday names, …) live in a
pluggable CalendarProvider -- see engine/calendar_provider.py and
engine/calendars/gregorian.py.

Must stay in sync with server.TICKS_PER_GAME_DAY and the copies in
supers/needs.py, supers/fuel.py, etc. Smoke + needs_timing assert those
copies when they change.
"""

# Default pacing: 9,600 ticks per game-day (see server.py Milestone E).
TICKS_PER_GAME_DAY = 9600

# 24 game-hours per game-day => 400 ticks per hour at the default pace.
HOURS_PER_DAY = 24
TICKS_PER_HOUR = TICKS_PER_GAME_DAY // HOURS_PER_DAY  # 400
MINUTES_PER_HOUR = 60

# Floor division: each "game minute" is TICKS_PER_HOUR / 60 ticks.
# With 400 ticks/hour that is not an integer -- breakdown() uses the
# remainder against TICKS_PER_HOUR and scales minutes as
# (rem * 60) // TICKS_PER_HOUR so the clock never claims 60 minutes.
