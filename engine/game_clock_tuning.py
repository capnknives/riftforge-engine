"""
game_clock_tuning.py -- GM-tunable calendar pace vs real time.

The asyncio heartbeat still fires every ~3s (Cadence, combat, needs once
per beat). ``scale`` is **game-days per real day**:

  * **1** (default / stock) -- real-time calendar (one game-day per real day)
  * **3** -- legacy compressed 3x pace (``gm clock scale 3``)
  * up to **60** -- fast-forward for staff testing

Per-heartbeat lifestyle drips (needs, fuel, online rest) multiply by
``scale / LIFESTYLE_CALIBRATION_DAYS`` (calibration = 3) so hunger/thirst/
rest stay aligned with the calendar at every pace.

Overrides persist in SQLite meta (``game_clock_tuning``) across restart
and copyover. Plain ints only -- no networking.
"""

from __future__ import annotations

# Must stay in sync with server.TICKS_PER_GAME_DAY / game_calendar.
TICKS_PER_GAME_DAY = 9600
HEARTBEAT_SECONDS = 3.0
HEARTBEATS_PER_REAL_DAY = 86400.0 / HEARTBEAT_SECONDS  # 28800 at 3s

# Stock world pace (no GM override).
DEFAULT_GAME_DAYS_PER_REAL_DAY = 1
DEFAULT_SCALE = DEFAULT_GAME_DAYS_PER_REAL_DAY
MIN_SCALE = 1
MAX_SCALE = 60

# needs.py / fuel.py *_PER_TICK constants were tuned assuming this many
# game-days per real day at one tick per heartbeat.
LIFESTYLE_CALIBRATION_DAYS = 3

# Legacy compressed pace (old shipped default) -- still available via gm clock.
LEGACY_COMPRESSED_DAYS = 3

TUNING_SPECS = {
    "scale": (
        "int",
        "Game-days per real day (1 = stock real-time; 3 = compressed 3x)",
    ),
}


def default_overrides():
    """Empty overrides blob (stock pace)."""
    return {}


def ensure_game_tuning(game):
    """Attach a sanitized overrides dict on ``game.game_clock_tuning``."""
    if game is None:
        return default_overrides()
    raw = getattr(game, "game_clock_tuning", None)
    if not isinstance(raw, dict):
        game.game_clock_tuning = {}
        return game.game_clock_tuning
    cleaned = {}
    for key, value in raw.items():
        if key not in TUNING_SPECS:
            continue
        try:
            cleaned[key] = max(MIN_SCALE, min(MAX_SCALE, int(value)))
        except (TypeError, ValueError):
            continue
    game.game_clock_tuning = cleaned
    return game.game_clock_tuning


def normalize_loaded(data):
    """Sanitize a meta JSON blob into overrides-only dict."""
    game_stub = type("G", (), {})()
    game_stub.game_clock_tuning = data if isinstance(data, dict) else {}
    return dict(ensure_game_tuning(game_stub))


def get_default(key):
    """Return the code default for ``key``, or None if unknown."""
    if key == "scale":
        return DEFAULT_SCALE
    return None


def get_effective(game, key):
    """Effective value for ``key`` (override, else code default)."""
    default = get_default(key)
    if default is None:
        raise KeyError(key)
    overrides = ensure_game_tuning(game)
    if key in overrides:
        return overrides[key]
    return default


def game_days_per_real_day(game):
    """How many in-game days pass per real day (1 = stock real-time)."""
    return int(get_effective(game, "scale"))


def lifestyle_rate_mult(game):
    """Multiply per-heartbeat needs/fuel/rest drips to match calendar pace.

    Constants in needs.py assume ``LIFESTYLE_CALIBRATION_DAYS`` (3) per
    real day. Stock ``scale`` 1 → 1/3 per heartbeat (same per game-hour).
    ``scale`` 3 → 1.0 (legacy compressed feel).
    """
    if game is None:
        return 1.0
    return float(game_days_per_real_day(game)) / float(LIFESTYLE_CALIBRATION_DAYS)


def tick_credit_per_heartbeat(game):
    """Fractional game ticks earned each heartbeat at this pace."""
    days = float(game_days_per_real_day(game))
    return (days * float(TICKS_PER_GAME_DAY)) / HEARTBEATS_PER_REAL_DAY


def wall_seconds_per_game_tick(game, *, tick_seconds=HEARTBEAT_SECONDS):
    """Real seconds for one ``game_time_ticks`` step at the live calendar pace.

    The asyncio heartbeat still fires every ``tick_seconds`` (~3s). Calendar
    credit per beat depends on ``gm clock scale``:

      * scale 3 (legacy compressed) -- 1.0 tick/beat → ~3s wall per tick
      * scale 1 (stock real-time) -- 1/3 tick/beat → ~9s wall per tick

    Seat→throne ETA lines and similar calendar-based countdowns must use
    this helper so ``gm clock scale N`` instantly rewrites ~minutes.
    """
    credit = tick_credit_per_heartbeat(game)
    if credit <= 0:
        return float(tick_seconds)
    return float(tick_seconds) / float(credit)


def approx_wall_seconds_for_ticks(ticks, game=None, *, tick_seconds=HEARTBEAT_SECONDS):
    """Real seconds for ``ticks`` ``game_time_ticks`` at the live calendar pace."""
    try:
        ticks = max(0, int(ticks))
    except (TypeError, ValueError):
        return 0
    if game is not None:
        sec_per_tick = wall_seconds_per_game_tick(game, tick_seconds=tick_seconds)
    else:
        # Smoke / offline stubs: compressed scale 3 (1 tick per ~3s heartbeat).
        sec_per_tick = float(tick_seconds)
    return int(round(ticks * sec_per_tick))


def ticks_for_wall_seconds(seconds, game=None, *, tick_seconds=HEARTBEAT_SECONDS):
    """``game_time_ticks`` needed to cover ``seconds`` of real wall time.

    Inverse of ``approx_wall_seconds_for_ticks``. For systems whose
    duration is a UX/session-pacing decision (a stun window, a cleanup
    timer, an anti-spam cooldown) rather than a calendar quantity: stamp
    the deadline as ``game_time_ticks + ticks_for_wall_seconds(N, game)``
    instead of a flat precomputed tick count, so the real-world wait stays
    ``N`` seconds at any ``gm clock scale`` instead of drifting when the
    scale default changes (bug: dozens of modules hardcoded "N ticks"
    calibrated to the old 3s/tick pace, which silently tripled in real
    time once stock scale defaulted to 1).
    """
    if game is not None:
        sec_per_tick = wall_seconds_per_game_tick(game, tick_seconds=tick_seconds)
    else:
        # Smoke / offline stubs: compressed scale 3 (1 tick per ~3s heartbeat).
        sec_per_tick = float(tick_seconds)
    if sec_per_tick <= 0:
        sec_per_tick = float(tick_seconds)
    return max(1, int(round(float(seconds) / sec_per_tick)))


def format_tick_cooldown_eta(ticks, game=None, *, tick_seconds=HEARTBEAT_SECONDS):
    """Player-facing ETA for a remaining tick-based cooldown."""
    secs = max(1, approx_wall_seconds_for_ticks(
        ticks, game, tick_seconds=tick_seconds,
    ))
    if secs >= 90:
        mins = max(1, int(round(secs / 60.0)))
        return f"wait about {ticks} more ticks (~{mins} min)"
    return f"wait about {ticks} more ticks (~{secs}s)"


def format_ticks_report_eta(ticks, game=None, *, tick_seconds=HEARTBEAT_SECONDS):
    """Staff/report ETA from remaining ``game_time_ticks`` at the live pace.

    Use this for countdowns stamped on ``game_time_ticks`` (balance wall
    events, haunt timers, …). For **reference-tick** knobs (incap window,
    bodydecay TTLs) whose stored value is already "N seconds at 3s/tick",
    display ``N reference ticks (~{N*3}s wall)`` instead.
    """
    remaining = max(0, int(ticks))
    if remaining <= 0:
        return "now"
    secs = max(
        1,
        approx_wall_seconds_for_ticks(
            remaining, game, tick_seconds=tick_seconds,
        ),
    )
    if secs < 60:
        return f"~{secs}s ({remaining} ticks)"
    mins = secs // 60
    rem_s = secs % 60
    if mins < 120:
        if rem_s:
            return f"~{mins}m {rem_s}s ({remaining} ticks)"
        return f"~{mins}m ({remaining} ticks)"
    hours = mins // 60
    rem_m = mins % 60
    return f"~{hours}h {rem_m}m ({remaining} ticks)"


def real_hours_per_game_day(game, *, tick_seconds=HEARTBEAT_SECONDS):
    """Wall hours for one in-game day at the current pace."""
    credit = tick_credit_per_heartbeat(game)
    if credit <= 0:
        return 0.0
    beats_per_day = float(TICKS_PER_GAME_DAY) / credit
    return beats_per_day * tick_seconds / 3600.0


def advance_game_time_ticks(game):
    """Advance ``game_time_ticks`` using a fractional credit accumulator."""
    credit = tick_credit_per_heartbeat(game)
    acc = float(getattr(game, "_game_clock_tick_credit", 0.0) or 0.0)
    acc += credit
    whole = int(acc)
    if whole > 0:
        game.game_time_ticks = int(getattr(game, "game_time_ticks", 0) or 0) + whole
        acc -= float(whole)
    game._game_clock_tick_credit = acc
    return whole


def speed_phrase(game, *, tick_seconds=HEARTBEAT_SECONDS, ticks_per_game_day=TICKS_PER_GAME_DAY):
    """Player-facing parenthetical for ``time`` / staff reports."""
    days = game_days_per_real_day(game)
    hours = real_hours_per_game_day(game, tick_seconds=tick_seconds)
    lore = pace_ambient_hint(days)
    if days == DEFAULT_GAME_DAYS_PER_REAL_DAY:
        return (
            f"(Time moves with real life here -- about one game-day per "
            f"real day, ~{hours:.0f} real hours per game-day.)"
        )
    if days == LEGACY_COMPRESSED_DAYS:
        return (
            f"(Time moves {days}x real speed here -- roughly "
            f"{hours:.0f} real hours per game-day.{lore})"
        )
    return (
        f"(World clock: {days} game-days per real day, "
        f"~{hours:.1f} real hours per game-day.{lore})"
    )


def pace_ambient_hint(days):
    """Short ongoing flavor for ``time`` when pace is not stock (no [ALERT])."""
    if days == DEFAULT_GAME_DAYS_PER_REAL_DAY:
        return ""
    if days == LEGACY_COMPRESSED_DAYS:
        return (
            " The old hurry sits on the hours -- three days for every one "
            "you live."
        )
    if days < LEGACY_COMPRESSED_DAYS:
        return " The day unravels slowly; shadows take their time."
    return " The sky keeps score too fast -- days blur at the edges."


def pace_change_line(old_days, new_days):
    """World-visible line when calendar pace shifts (GM dial or future magic).

    Returns None when there is nothing worth announcing (no change).
    """
    try:
        old_days = int(old_days)
        new_days = int(new_days)
    except (TypeError, ValueError):
        return None
    if old_days == new_days:
        return None
    if new_days < old_days:
        if new_days == DEFAULT_GAME_DAYS_PER_REAL_DAY:
            return (
                "The Veil exhales. Daylight and dark fall into step with the "
                "world outside -- time no longer runs ahead of you."
            )
        return (
            "The day's edges soften. Hours drag their feet; sunrise and "
            "sunset arrive when they mean to, not all at once."
        )
    if (
        new_days == LEGACY_COMPRESSED_DAYS
        and old_days != LEGACY_COMPRESSED_DAYS
    ):
        return (
            "Familiar pressure returns beneath the hours -- the old triple "
            "beat, three days burning through one night's sleep."
        )
    if new_days > old_days:
        return (
            "The calendar lurches. Sun and moon race like film on fast "
            "forward; whole afternoons vanish between heartbeats."
        )
    return None


def broadcast_pace_change(game, old_days, new_days):
    """Tell every connected player when the world clock pace changes."""
    line = pace_change_line(old_days, new_days)
    if not line:
        return
    broadcast = getattr(game, "broadcast_all", None)
    if callable(broadcast):
        broadcast(line)


def set_tuning(game, key, value):
    """Set one override. Returns (ok, message)."""
    key = (key or "").strip().lower()
    if key not in TUNING_SPECS:
        known = ", ".join(sorted(TUNING_SPECS))
        return False, f"Unknown game clock knob '{key}'. Known: {known}"
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return False, f"Bad value for {key}: need a number."
    if parsed < MIN_SCALE:
        parsed = MIN_SCALE
    if parsed > MAX_SCALE:
        return (
            False,
            f"scale max is {MAX_SCALE} (heartbeat still runs every ~3s wall).",
        )
    old_days = game_days_per_real_day(game)
    overrides = ensure_game_tuning(game)
    overrides[key] = parsed
    game.game_clock_tuning = overrides
    # Pace change should not carry stale fractional credit from the old rate.
    game._game_clock_tick_credit = 0.0
    new_days = game_days_per_real_day(game)
    broadcast_pace_change(game, old_days, new_days)
    return True, (
        f"{key} set to {parsed} game-days per real day "
        f"(~{real_hours_per_game_day(game):.1f} real h per game-day; "
        "lifestyle drips scaled to match; persists on save)."
    )


def reset_tuning(game, key=None):
    """Clear one override or all. Returns (ok, message)."""
    old_days = game_days_per_real_day(game)
    overrides = ensure_game_tuning(game)
    if key is None or not str(key).strip():
        if not overrides.get("scale") and old_days == DEFAULT_SCALE:
            game.game_clock_tuning = {}
            return True, (
                "All game clock overrides cleared "
                f"(stock {DEFAULT_GAME_DAYS_PER_REAL_DAY}x real-time calendar)."
            )
        game.game_clock_tuning = {}
        game._game_clock_tick_credit = 0.0
        broadcast_pace_change(game, old_days, DEFAULT_SCALE)
        return True, (
            "All game clock overrides cleared "
            f"(stock {DEFAULT_GAME_DAYS_PER_REAL_DAY}x real-time calendar)."
        )
    key = str(key).strip().lower()
    if key not in TUNING_SPECS:
        known = ", ".join(sorted(TUNING_SPECS))
        return False, f"Unknown game clock knob '{key}'. Known: {known}"
    if key in overrides:
        del overrides[key]
        game.game_clock_tuning = overrides
        game._game_clock_tick_credit = 0.0
        broadcast_pace_change(game, old_days, game_days_per_real_day(game))
        return True, f"{key} reset to default {get_default(key)}."
    return True, f"{key} already at default {get_default(key)}."


def format_tuning_report(game, *, tick_seconds=HEARTBEAT_SECONDS):
    """Multi-line staff sheet for bare ``gm clock``."""
    overrides = ensure_game_tuning(game)
    days = game_days_per_real_day(game)
    default = get_default("scale")
    flagged = " [override]" if "scale" in overrides else ""
    hours = real_hours_per_game_day(game, tick_seconds=tick_seconds)
    lifestyle = lifestyle_rate_mult(game)
    lines = [
        "Game world clock (gm clock)",
        "Heartbeat stays ~3s wall. scale = game-days per real day.",
        "Needs / fuel / online rest drip scale with the calendar.",
        "Overrides persist across restart / copyover.",
        "",
        f"  scale: {days}  (default {default}){flagged}",
        f"      {TUNING_SPECS['scale'][1]}",
        f"  effective: ~{hours:.1f} real h per game-day; "
        f"lifestyle mult {lifestyle:.3g}x calibration "
        f"({LIFESTYLE_CALIBRATION_DAYS} game-days/real day baseline)",
        "",
        "Usage:",
        "  gm clock                    show this sheet",
        f"  gm clock scale {DEFAULT_GAME_DAYS_PER_REAL_DAY}              "
        "real-time calendar (default)",
        f"  gm clock scale {LEGACY_COMPRESSED_DAYS}              "
        "legacy compressed 3x pace",
        "  gm clock scale <N>            faster forward (up to 60)",
        "  gm clock reset              clear override",
        "",
        "See also: time | help gmclock | help calendar",
    ]
    return "\r\n".join(lines)
