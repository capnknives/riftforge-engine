"""
account_display.py -- framed ``account`` status for players.

Keeps presentation out of ``cmd_account`` so the verb stays readable and
format_sheet / screenreader prefs stay consistent with score / bugs sheets.
"""

from __future__ import annotations


def format_account_status(game, account, viewer, *, screenreader=False, width=52):
    """Build framed lines for bare ``account`` (linked account review).

    Refreshes contribution totals from report logs before rendering.
    Returns a list of lines suitable for ``session.send("\\r\\n".join(...))``.
    """
    from engine import accounts as accounts_mod
    from engine import display_prefs
    from engine import hooks as hooks_mod
    from engine import style

    accounts_mod.refresh_contribution_totals(game, account)
    sr = bool(screenreader)
    sheet_w = int(width or display_prefs.sheet_width(viewer))

    display = (getattr(account, "display_name", None) or account.name or "?").strip()
    bugs = int(getattr(account, "bugs_squashed", 0) or 0)
    ideas = int(getattr(account, "features_suggested", 0) or 0)
    gifted = int(getattr(account, "gifted", 0) or 0)
    gift_bank = int(getattr(account, "gift_bank", 0) or 0)
    points = accounts_mod.contribution_points(account)

    body = []
    if not sr:
        body.append(style.paint("gold", f"  {display}"))
        body.append(style.paint("muted", "  ── roster ──"))
    else:
        body.append(f"Account: {display}.")

    roster_lines = []
    for key in accounts_mod.roster_character_keys(game, account):
        finder = getattr(game, "find_login_character", None)
        body_char = finder(key) if callable(finder) else None
        if body_char is None:
            body_char = game.find_character(key)
        label = hooks_mod.account_roster_label_for(game, key, body_char)
        roster_lines.append(f"  {label}")
    if roster_lines:
        body.extend(roster_lines)
    else:
        body.append(
            "  (no characters yet)" if not sr else "Characters: none."
        )

    if not sr:
        body.append(style.paint("muted", "  ── contributions ──"))
    else:
        body.append("Contributions:")
    body.append(
        style.paint("soft_crimson", f"  Account points: {points}")
        if not sr
        else f"Account points: {points}."
    )
    body.append(f"  Bugs squashed: {bugs}")
    body.append(f"  Ideas shipped: {ideas}")
    body.append(f"  Gifted: {gifted}")
    if gift_bank > 0:
        body.append(
            f"  Gift bank: {gift_bank} "
            "(spend with giftpoints; 1/hour active online)"
        )
    if not sr:
        body.append(
            style.paint(
                "muted",
                "  (resolved reports + gifted; leaderboard uses bugs/ideas)",
            )
        )

    if not sr:
        body.append(style.paint("muted", "  ── preferences ──"))
    else:
        body.append("Preferences:")
    ooc = getattr(account, "ooc_identity", "account")
    body.append(
        f"  OOC name: {ooc}  "
        f"(config oocname account|character)"
    )

    if account.gm_rank in ("gm", "head_gm"):
        if not sr:
            body.append(style.paint("muted", "  ── staff ──"))
        else:
            body.append("Staff:")
        rank_label = "Head GM" if account.gm_rank == "head_gm" else "GM"
        body.append(f"  Rank: {rank_label}")
        cast_n = len(accounts_mod.list_immersion_cast(game))
        body.append(
            f"  Cast roster: {cast_n} immersion cast "
            "(login menu + gm off <name>)"
        )
        state = "on" if account.gm_see_accounts else "off"
        body.append(
            f"  See-accounts: {state} "
            "(config seeaccounts on|off)"
        )
    else:
        grants = accounts_mod.list_assigned_character_keys(account)
        if grants:
            if not sr:
                body.append(style.paint("muted", "  ── assigned ──"))
            else:
                body.append("Assigned characters:")
            for key in grants:
                body.append(f"  {key}")

    title = "ACCOUNT"
    return style.format_sheet(
        title,
        body,
        width=sheet_w,
        screenreader=sr,
    )
