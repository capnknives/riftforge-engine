"""verbs/character.py -- basegame's `score` command.

Mirrors the shape of supers/verbs/character.py's cmd_score, at basegame's
scale: no GM caste here, so `score` only ever shows the caller's own sheet
(no viewer/target split). Body content is Path/stats/Tier/HP -- everything
this reference game actually tracks on a character.
"""

from basegame.chargen import PATHS
from basegame import stats as stats_module
from engine import stats as engine_stats
from engine.style import format_sheet


def cmd_score(character, args, game):
    """Show the caller's own sheet: path, the six shared primaries, Tier,
    and HP."""
    body = []
    path_label = PATHS.get(character.bg_path, "Path: (none chosen)")
    body.append(path_label.split(" -- ")[0])
    body.append("")
    for name in engine_stats.STAT_NAMES:
        body.append(f"{name}: {character.stats[name]:g}")
    body.append(f"Tier: {character.tier}")
    body.append(f"HP: {character.hp}/{stats_module.max_hp(character)}")

    screenreader = bool(getattr(character, "screenreader", False))
    for line in format_sheet("Score", body, screenreader=screenreader):
        character.session.send(line)
