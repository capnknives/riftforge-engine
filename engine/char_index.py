"""
engine/char_index.py -- O(1) Character roster for the live world.

The map holds ~12k rooms (100x100 Wastes + planar grids). Walking
``game.rooms.values()`` once per tick handler to rediscover ~50 actors
was the live lag sawtooth: dozens of full scans every 3 seconds on the
single asyncio thread.

Game keeps ``game.characters`` (a set) updated by Room.add / Room.remove
whenever ``room.game`` is stamped. ``RoomMap`` (below) stamps ``room.game``
on every insert into ``game.rooms``, including procedural dungeons and
smoke-test ad-hoc rooms.

Logout does NOT remove a character -- Echoes stay in the world and stay
in the set (logout != deletion). Only Room.remove (despawn / Hakai /
ephemeral kill) drops them. Character.move_to bypasses Room.remove so a
room change does not flicker the roster.
"""


class RoomMap(dict):
    """``game.rooms`` dict that stamps ``room.game`` on every insert.

    Procedural dungeons, strongholds, and smoke tests all do
    ``game.rooms[key] = room``; without this stamp, Room.add would not
    know which Game owns the character roster.
    """

    def __init__(self, game):
        """Bind to the owning Game (called before rooms are filled)."""
        super().__init__()
        self._game = game

    def __setitem__(self, key, room):
        """Insert ``room`` and point ``room.game`` at the owning Game."""
        room.game = self._game
        super().__setitem__(key, room)

    def update(self, other=(), **kwargs):
        """Stamp every room, including bulk ``update`` from build_world."""
        # dict.update can bypass __setitem__ for some call shapes --
        # assign one-by-one so stamping is never skipped.
        if isinstance(other, dict):
            items = other.items()
        else:
            items = other
        for key, room in items:
            self[key] = room
        for key, room in kwargs.items():
            self[key] = room


def iter_characters(game):
    """Return a stable snapshot of every Character currently in the world.

    Prefers ``game.characters`` when the registry is wired. Falls back to a
    full room scan for stubs/tests that never stamped ``room.game``.

    Returns a tuple so callers can despawn mid-loop without "set changed
    size during iteration".
    """
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        return tuple(chars)
    # Slow fallback -- same shape the old tick helpers used.
    from engine.world import Character
    rooms = getattr(game, "rooms", None) or {}
    found = []
    for room in rooms.values():
        for obj in room.contents:
            if isinstance(obj, Character):
                found.append(obj)
    return tuple(found)


def register_character(game, character):
    """Add ``character`` to the live roster (idempotent)."""
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        chars.add(character)


def unregister_character(game, character):
    """Remove ``character`` from the live roster (idempotent)."""
    chars = getattr(game, "characters", None)
    if isinstance(chars, set):
        chars.discard(character)


def rebuild_character_index(game):
    """Rebuild ``game.characters`` from room contents (boot / recovery).

    Normal play should never need this -- Room.add/remove keep the set
    truthful. Useful after a bulk load that bypassed move_to, or if ops
    suspect the index drifted.
    """
    from engine.world import Character
    game.characters = set()
    for room in game.rooms.values():
        for obj in room.contents:
            if isinstance(obj, Character):
                game.characters.add(obj)
