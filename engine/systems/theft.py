"""
theft.py -- generic theft attempt shape for engine consumers.

Games supply skill checks and wallet policy; this module owns the result
dataclass and optional ``justice_on_robbery`` telemetry hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from engine import hooks as hooks_mod


@dataclass
class TheftResult:
    success: bool
    amount_cents: int
    caught: bool
    reason: str | None = None


def attempt_theft(
    actor,
    *,
    amount_cents: int,
    skill_check: Optional[Callable[[], bool]] = None,
    game=None,
) -> TheftResult:
    """Resolve a take attempt; call ``justice_on_robbery`` on success."""
    if amount_cents <= 0:
        return TheftResult(
            success=False,
            amount_cents=0,
            caught=False,
            reason="zero_amount",
        )
    if skill_check is not None and not skill_check():
        return TheftResult(
            success=False,
            amount_cents=0,
            caught=True,
            reason="skill_fail",
        )
    hooks_mod.justice_on_robbery(actor, game, int(amount_cents))
    return TheftResult(
        success=True,
        amount_cents=int(amount_cents),
        caught=False,
    )
