"""
player_site.py -- generic worksite ledger helpers for player-built sites.

Game-agnostic: no SUPERS gather lore. Homestead (and future pockets) call
these helpers to deposit / spend / refund material counts keyed by string
ids (catalog item ids). Ledger shape is a plain dict
``{material_id: int_count}``.
"""

from __future__ import annotations

import random


def empty_ledger():
    """Return a fresh material ledger dict."""
    return {}


def ledger_get(ledger, material_id):
    """How many units of ``material_id`` are deposited."""
    if not isinstance(ledger, dict):
        return 0
    try:
        return max(0, int(ledger.get(material_id, 0) or 0))
    except (TypeError, ValueError):
        return 0


def deposit(ledger, material_id, amount):
    """Add ``amount`` of ``material_id`` to ``ledger``. Returns new total.

    Mutates ``ledger`` in place. Raises ``ValueError`` on bad inputs.
    """
    if not isinstance(ledger, dict):
        raise ValueError("ledger must be a dict")
    mid = (material_id or "").strip()
    if not mid:
        raise ValueError("material_id required")
    try:
        n = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be an int") from exc
    if n <= 0:
        raise ValueError("amount must be positive")
    ledger[mid] = ledger_get(ledger, mid) + n
    return ledger[mid]


def can_afford(ledger, cost):
    """True when ``ledger`` covers every ``cost`` entry (id -> need)."""
    if not isinstance(cost, dict):
        return False
    for mid, need in cost.items():
        try:
            need_n = int(need)
        except (TypeError, ValueError):
            return False
        if ledger_get(ledger, mid) < need_n:
            return False
    return True


def missing_for(ledger, cost):
    """List human fragments like ``wood (have 2, need 8)`` for shortfalls."""
    lines = []
    if not isinstance(cost, dict):
        return ["(invalid cost)"]
    for mid, need in cost.items():
        try:
            need_n = int(need)
        except (TypeError, ValueError):
            need_n = 0
        have = ledger_get(ledger, mid)
        if have < need_n:
            lines.append(f"{mid} (have {have}, need {need_n})")
    return lines


def spend(ledger, cost):
    """Subtract ``cost`` from ``ledger``. Returns (ok, message).

    On failure leaves ledger unchanged. On success mutates in place.
    """
    if not can_afford(ledger, cost):
        miss = missing_for(ledger, cost)
        return False, "Missing materials: " + ", ".join(miss) + "."
    for mid, need in cost.items():
        need_n = int(need)
        left = ledger_get(ledger, mid) - need_n
        if left <= 0:
            ledger.pop(mid, None)
        else:
            ledger[mid] = left
    return True, ""


def scale_cost(cost, multiplier):
    """Return a new cost dict with each amount ``ceil(n * multiplier)``.

    Uses integer ceil via ``-(-n // 1)`` pattern after float multiply,
    minimum 1 when the original amount was > 0.
    """
    import math

    out = {}
    if not isinstance(cost, dict):
        return out
    try:
        mult = float(multiplier)
    except (TypeError, ValueError):
        mult = 1.0
    for mid, need in cost.items():
        try:
            need_n = int(need)
        except (TypeError, ValueError):
            continue
        if need_n <= 0:
            continue
        scaled = int(math.ceil(need_n * mult))
        out[mid] = max(1, scaled)
    return out


def refund_fraction(lifetime_deposits, low=0.30, high=0.55, rng=None):
    """Compute a refund ledger from lifetime deposits.

    Picks a random fraction in ``[low, high]`` (inclusive) and returns
    ``{id: floor(count * fraction)}`` for each deposited material that
    yields at least 1. Does not mutate ``lifetime_deposits``.
    """
    if rng is None:
        rng = random
    if not isinstance(lifetime_deposits, dict):
        return {}
    try:
        frac = float(rng.uniform(float(low), float(high)))
    except (TypeError, ValueError):
        frac = 0.30
    out = {}
    for mid, total in lifetime_deposits.items():
        try:
            n = int(total)
        except (TypeError, ValueError):
            continue
        give = int(n * frac)
        if give > 0:
            out[mid] = give
    return out


def format_ledger_line(ledger, label="Deposited"):
    """One-line summary for look / worksite progress."""
    if not isinstance(ledger, dict) or not ledger:
        return f"{label}: (empty)"
    parts = []
    for mid in sorted(ledger.keys()):
        parts.append(f"{ledger_get(ledger, mid)} {mid}")
    return f"{label}: " + ", ".join(parts)


def format_cost_line(cost, label="Requires"):
    """One-line summary of a cost dict."""
    if not isinstance(cost, dict) or not cost:
        return f"{label}: (none)"
    parts = [f"{int(n)} {mid}" for mid, n in sorted(cost.items())]
    return f"{label}: " + ", ".join(parts)
