"""classic/ -- lean OSR fantasy game on the RiftForge engine.

STR/DEX/CON/INT/WIS/CHA, four classes (War / Cleric / Mage / Rogue) to
level 20, active combat (heartbeat + instant attack/cast), and a tiny
Millbrook village plus wilderness map. Mutually exclusive with supers/ and
basegame/ at runtime -- see game_select.py.

Extension hooks for deeper rules (feats, spellbooks, 5e proficiency) live
in classic/rules/registries.py. See docs/plans/classic_game_mvp.md.
"""

from classic.bootstrap import register_core_hooks

register_core_hooks()
