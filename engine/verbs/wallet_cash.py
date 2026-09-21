"""wallet_cash.py -- get/put intercept so cash uses bag-shaped syntax.

``get 20 from wallet`` / ``put 20 in wallet`` (and the short ``get 20 wallet``
/ ``put 20 wallet`` forms) run before floor-get and backpack-put. The math
lives in ``engine.systems.economy``; this module is the verb-side call.
"""

from __future__ import annotations


def try_send_wallet_cash(character, args, game, *, direction):
    """Run a wallet cash get/put. Returns True when the verb was cash-vs-wallet.

    ``direction`` is ``get`` (take bills out) or ``put`` (tuck bills in).
    """
    from engine.systems import economy as economy_mod

    cash_try = economy_mod.try_wallet_cash_verb(
        character,
        args or "",
        direction=direction,
        tick=getattr(game, "game_time_ticks", None) if game else None,
    )
    if cash_try is None:
        return False
    _ok, msg = cash_try
    sess = getattr(character, "session", None)
    if sess is not None and msg:
        sess.send(msg)
    return True
