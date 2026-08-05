"""
fight.py -- generic multi-combatant Fight membership for the engine.

Peeled in shape from ``supers/fight.py`` (docs/plans/fast_paced_combat_engine.md
decision #18) but deliberately smaller: room, members, ``combat_mode``,
join / discard / dissolve. SUPERS keeps its own richer Fight untouched --
zero ``supers`` imports here.

``combat_mode`` is the Fight-level routing flag (decision #8): when the
first engagement into an active-combat room/NPC sets
``fight.combat_mode = "active"``, every later join inherits that mode so
two combat engines never resolve the same bout differently.

Hard parts §10.10: join only inherits ``combat_mode`` -- each member's
offense queue and open-telegraph dict stay empty/independent.
"""

from __future__ import annotations

import itertools

# Module-private Fight registry. Members are strong refs while the bout
# runs; indexes are by id(character) so identity (not key) is what matters.
_fights = {}  # fight_id -> Fight
_member_fight = {}  # id(character) -> fight_id
_next_id = itertools.count(1)

# Known combat_mode values. "narrative" is the default (swing/tick engines);
# "active" selects the timestamp-buffered twitch path.
MODE_NARRATIVE = "narrative"
MODE_ACTIVE = "active"


class Fight:
    """One multi-combatant bout anchored to a single room.

    Members keep their own focus target; the Fight exists so 2v1 pile-ons
    share membership and a single ``combat_mode``.
    """

    def __init__(self, room, *, combat_mode=MODE_NARRATIVE):
        """Create an empty Fight in ``room`` with the given ``combat_mode``."""
        self.id = next(_next_id)
        self.room = room
        self.combat_mode = str(combat_mode or MODE_NARRATIVE)
        self.members = set()

    def add(self, character):
        """Add ``character`` to this Fight and index them.

        Does **not** copy or merge that character's offense queue /
        telegraphs from anyone else (§10.10).
        """
        if character is None:
            return
        self.members.add(character)
        _member_fight[id(character)] = self.id
        _fights[self.id] = self

    def discard(self, character):
        """Remove ``character``; dissolve the Fight if fewer than two remain."""
        if character is None:
            return
        self.members.discard(character)
        _member_fight.pop(id(character), None)
        if len(self.members) < 2:
            self.dissolve()

    def dissolve(self):
        """Drop every member index and remove this Fight from the registry.

        Callers that need to clear per-character combat buffers (offense
        queue, open telegraphs) should do that themselves -- this module
        owns membership only, not active-combat state.
        """
        members = list(self.members)
        for member in members:
            _member_fight.pop(id(member), None)
        self.members.clear()
        _fights.pop(self.id, None)


def get_fight(character):
    """Return the Fight ``character`` belongs to, or None."""
    if character is None:
        return None
    fight_id = _member_fight.get(id(character))
    if fight_id is None:
        return None
    return _fights.get(fight_id)


def join_fight(a, b, *, combat_mode=None):
    """Put ``a`` and ``b`` in the same Fight (create or merge).

    Prefer an existing Fight either already belongs to in the same room;
    otherwise start a fresh one. ``combat_mode`` sticks from the existing
    Fight when merging (decision #8 inherit); only a brand-new Fight uses
    the caller's ``combat_mode`` (default ``narrative``).
    """
    if a is None or b is None:
        return None
    room = getattr(a, "location", None)
    if room is None or room is not getattr(b, "location", None):
        return None

    fight_a = get_fight(a)
    fight_b = get_fight(b)

    if fight_a is not None and fight_a.room is room:
        fight = fight_a
        if fight_b is not None and fight_b is not fight_a:
            # Merge b's bout into a's -- move members, then dissolve the old.
            for member in list(fight_b.members):
                fight.add(member)
            fight_b.members.clear()
            _fights.pop(fight_b.id, None)
    elif fight_b is not None and fight_b.room is room:
        fight = fight_b
    else:
        mode = combat_mode if combat_mode is not None else MODE_NARRATIVE
        fight = Fight(room, combat_mode=mode)

    fight.add(a)
    fight.add(b)
    return fight


def reset_for_tests():
    """Clear every Fight -- smoke / unit tests only."""
    _fights.clear()
    _member_fight.clear()
