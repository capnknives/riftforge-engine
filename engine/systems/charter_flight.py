"""
charter_flight.py -- generic charter flight phase helpers (engine layer).

No SUPERS imports. SUPERS charter planes call these for phase names,
hatch/cockpit lock policy, and globe frame pacing math.
"""

from __future__ import annotations

# Heartbeats between globe frames while aboard (~3s tick).
CHARTER_STEP_EVERY = 1

# Domestic US atlas hops use road scenic pacing / this divisor (~3x faster).
CHARTER_ATLAS_SPEED_DIVISOR = 3

# Flight phases stored on ``game.vehicles[id]["flight_phase"]``.
PHASE_PARKED_OPEN = "parked_open"
PHASE_PREFLIGHT = "preflight"
PHASE_ENROUTE = "charter_enroute"
PHASE_LANDED = "charter_landed"

HATCH_OPEN_PHASES = frozenset({PHASE_PARKED_OPEN, PHASE_LANDED})
COCKPIT_LOCKED_PHASES = frozenset({PHASE_PREFLIGHT, PHASE_ENROUTE})


def hatch_open(flight_phase):
    """True when passengers may leave the aircraft to the apron."""
    return str(flight_phase or "") in HATCH_OPEN_PHASES


def cockpit_locked(flight_phase):
    """True when cabin->cockpit movement is blocked for passengers."""
    return str(flight_phase or "") in COCKPIT_LOCKED_PHASES


def frame_count_for_distance(dist, *, min_frames=10, max_frames=24):
    """Scale globe montage length to atlas Chebyshev distance."""
    if dist is None:
        return min_frames
    try:
        d = int(dist)
    except (TypeError, ValueError):
        return min_frames
    if d < 0:
        d = 0
    # ~2 frames per tile, clamped -- tuned in smoke; see charter_flight.py.
    return max(min_frames, min(max_frames, 8 + d * 2))


def prose_phase_for_frame(index, total):
    """Map frame index to prose pool key (climb / cruise / descent).

    supers/charter_flight.pick_prose_line uses this key to choose
    atmospheric lines from charter_flight_prose.json.
    """
    if total <= 1:
        return "cruise"
    if index <= 0:
        return "climb"
    if index >= total - 1:
        return "descent"
    # Middle third buckets; cruise also rolls optional weather inserts.
    if index < total // 3:
        return "climb"
    if index >= (2 * total) // 3:
        return "descent"
    return "cruise"


def charter_atlas_step_every(road_scenic_step_every):
    """Heartbeats per America macro tile during domestic charter flight.

    ``road_scenic_step_every`` is ``vehicle_roadtrip.scenic_step_every(game)``
    (road cruise pacing). Charter atlas hops run faster by
    ``CHARTER_ATLAS_SPEED_DIVISOR``.
    """
    try:
        base = int(road_scenic_step_every)
    except (TypeError, ValueError):
        base = 10
    if base < 1:
        base = 1
    return max(1, base // CHARTER_ATLAS_SPEED_DIVISOR)
