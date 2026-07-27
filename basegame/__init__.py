"""basegame/ -- the reference game built on top of the generic RiftForge engine.

A minimal human-only demo (Detective / Medic / Laborer-Courier / Ranger-
Guard paths) that proves the engine is playable on its own, without any of
SUPERS' Origins/Tiers/planes/Cadence installed. Mutually exclusive with
supers/ at runtime -- see game_select.py's docstring for why only one game
package may ever be imported in a process.

Importing this package registers core engine hooks (Character attach +
persist blob + stat hooks), mirroring supers/__init__.py, so game code
that constructs Characters without going through server.py still gets a
full basegame attach.
"""

from basegame.bootstrap import register_core_hooks

register_core_hooks()
