"""help_topics.py -- basegame's HELP_TOPICS / HELP_CATEGORIES.

Registered via engine.hooks.set_help in
basegame/bootstrap.py.register_all_hooks. Mirrors root help_topics.py's
shape (topic id -> page string; categories -> ordered (heading, [topic
ids]) list) but scoped to just what basegame ships -- AGENTS.md rule 11
("ship help with the feature") applies to basegame verbs the same as
SUPERS ones.
"""

from basegame.chargen import PATHS, PATH_ORDER

_paths_lines = "\n".join(f"  {path_id} -- {PATHS[path_id]}" for path_id in PATH_ORDER)

HELP_TOPICS = {
    "paths": f"""RiftForge reference town -- paths

Every resident is an ordinary human. Your path is the work you do:

{_paths_lines}

Pick your path at character creation; this reference build does not
support changing it later. See help basegame for what else is here.
""",
    "basegame": """RiftForge reference town

This is the engine's own demo game -- proof that RiftForge plays without
any of SUPERS' Origins/planes/game content installed. Look around, walk
between rooms, and see help paths for what each of the four starting jobs
does. Type score to see your sheet.
""",
    "score": """RiftForge reference town -- score

Shows your Path, your six primary stats (the same POW/VIT/FOC/FIN/RES/PRE
spine every game on the engine shares), your Tier, and your HP. `sc` is a
shorthand alias.
""",
}

HELP_CATEGORIES = [
    ("Basegame", ["basegame", "paths", "score"]),
]
