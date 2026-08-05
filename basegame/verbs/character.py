"""verbs/character.py -- basegame's `score` command (engine sheet schema)."""

from engine.systems import sheet as sheet_mod

_SELF_PANES = frozenset({
    "vitals", "combat", "needs", "full",
})


def cmd_score(character, args, game):
    """Show the caller's sheet via the engine score schema + basegame hooks.

    bare score          -- compact sheet + urgent needs/injuries
    score vitals        -- HP-focused slice
    score combat        -- readiness, aim, per-limb injuries
    score needs         -- hunger/thirst meters
    score full          -- verbose whole sheet
    """
    raw = (args or "").strip().lower()
    viewer_sr = bool(getattr(character, "screenreader", False))
    if raw and raw not in _SELF_PANES:
        character.session.send(
            "Usage: score  -- or score vitals / combat / needs / full "
            "(see 'help score')."
        )
        return
    pane = "default"
    compact = True
    filter_mode = None
    if raw == "full":
        pane = "full"
        compact = False
    elif raw:
        pane = raw
        compact = False
        filter_mode = raw
    ctx = sheet_mod.SheetContext(
        target=character,
        game=game,
        viewer=character,
        pane=pane,
        compact=compact,
        filter_mode=filter_mode,
        screenreader=viewer_sr,
    )
    character.session.send(sheet_mod.render_score(ctx))
    if not compact:
        character.session.send("")
