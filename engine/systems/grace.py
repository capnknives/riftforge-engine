"""grace.py -- Celestial grace tank math (folklore peel Wave 5a).

Live Angels and Nephilim store grace in ``Character.fuel`` via
``supers.fuel`` (the shared fuel chassis). Basegame demo celestials may
use ``Character.grace`` directly. This module holds shared constants and
pure float helpers only — callers mutate Character fields; nothing here
touches networking or score nouns.

The Grace *Discipline* (Mastery 0–4, id ``grace``) is a separate catalog
object from this tank. Do not register grace on ``engine.systems.needs``
attach_meters — needs meters run 0.0 satisfied → 1.0 critical, which is
the wrong shape for a fuel-style 0–100 tank.
"""

GRACE_MAX = 100.0
GRACE_DEFAULT = 80.0
GRACE_DRIP_PER_TICK = 0.05
GRACE_DRIP_HOME_PER_TICK = 0.20


def clamp_grace(value, *, cap=GRACE_MAX) -> float:
    """Clamp *value* into ``[0, cap]`` and round to two decimals like fuel."""
    return round(max(0.0, min(float(cap), float(value))), 2)


def add_grace_amount(current, amount, *, cap=GRACE_MAX) -> tuple[float, float]:
    """Add *amount* to *current*; return ``(new_value, actual_delta)`` after clamp."""
    before = float(current)
    after = clamp_grace(before + float(amount), cap=cap)
    return after, round(after - before, 2)


def grace_drip_amount(*, on_home_plane: bool, on_consecrated: bool) -> float:
    """Passive tick drip rate copied from live ``tick_grace_drip`` priority.

    Home plane (Heaven on live) beats consecrated ground; secular rooms
    drip nothing. Rates match ``GRACE_DRIP_HOME_PER_TICK`` and
    ``GRACE_DRIP_PER_TICK`` — engine name avoids plane-specific prose.
    """
    if on_home_plane:
        return GRACE_DRIP_HOME_PER_TICK
    if on_consecrated:
        return GRACE_DRIP_PER_TICK
    return 0.0
