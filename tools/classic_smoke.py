"""classic_smoke.py -- gate for the classic OSR game package.

Boots with RIFTFORGE_GAME=classic, runs chargen for each class, checks
Millbrook room count, combat swing, and instant-action tick skip -- without
importing supers.

Stdlib only. Never imported by server.py.
"""

from __future__ import annotations

import asyncio
import os
import sys


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_repo_on_path():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)


class _FakeSession:
    def __init__(self, replies):
        self.lines = []
        self._replies = list(replies)
        self.alive = True
        self.character = None

    def attach(self, character):
        self.character = character
        character.session = self
        return self

    def send(self, message):
        self.lines.append(message)

    async def read_line(self):
        if not self._replies:
            raise AssertionError(
                "FakeSession ran out of replies:\n" + "\n".join(self.lines[-10:])
            )
        return self._replies.pop(0)


def _chargen_replies(class_index):
    from classic import stats as stats_module
    return [str(class_index)] + ["0"] * len(stats_module.ABILITY_NAMES)


async def _run_chargen(game, class_index, class_id):
    from world import Character
    from engine import hooks

    char = Character(f"Smoke{class_id.capitalize()}")
    session = _FakeSession(_chargen_replies(class_index))
    session.attach(char)
    ok = await hooks.run_chargen(session, char)
    assert ok, f"chargen for {class_id!r} should succeed"
    assert char.classic_class == class_id
    char.move_to(game.start_room)
    return char


def main():
    _ensure_repo_on_path()
    os.environ["RIFTFORGE_GAME"] = "classic"

    import game_select
    game_select._reset_for_tests()
    assert game_select.game_name() == "classic"
    assert "supers" not in sys.modules

    import server as server_mod
    assert server_mod._HAS_SUPERS is False

    import commands as commands_mod
    assert "attack" in commands_mod.COMMANDS
    assert "cast" in commands_mod.COMMANDS
    assert "skill" in commands_mod.COMMANDS

    from engine import hooks
    topics = hooks.get_help_topics()
    assert "classic" in topics
    assert "classes" in topics
    assert "combat" in topics

    game = server_mod.Game(db_path=":memory:")
    assert game.start_room is not None
    assert game.start_room.key == "MB00001", game.start_room.key

    from classic import classes as classes_module

    from classic.content_validate import validate_all_content
    validate_all_content()

    # Count Millbrook rooms loaded in world.
    mb_rooms = [
        r for r in game.rooms.values()
        if getattr(r, "key", "").startswith("MB")
    ]
    assert len(mb_rooms) == 10, f"expected 10 Millbrook rooms, got {len(mb_rooms)}"

    war_row_1 = classes_module.level_row("war", 1)
    war_row_20 = classes_module.level_row("war", 20)
    assert war_row_1["bab"] == 1
    assert war_row_20["bab"] == 20

    placed = []
    for index, class_id in enumerate(classes_module.CLASS_ORDER, start=1):
        char = asyncio.run(_run_chargen(game, index, class_id))
        placed.append(char)
    assert len(placed) == len(classes_module.CLASS_ORDER)

    from world import Character
    from classic import combat as combat_mod
    from classic import stats as stats_module

    attacker = placed[0]
    defender = Character("Dummy")
    defender.classic_class = "war"
    defender.classic_level = 1
    defender.classic_abilities = stats_module.default_abilities()
    stats_module.sync_engine_stats(defender)
    defender.classic_armor_bonus = 0
    defender.hp = 30
    defender.move_to(game.start_room)

    swing = combat_mod.resolve_swing(
        attacker, defender, game, rng=lambda: 0.99,
    )
    assert swing["brief"]["engine"] == "osr"
    assert "d20" in swing["brief"]
    assert "ac" in swing["brief"]

    game.game_time_ticks = 5
    combat_mod.resolve_instant_action(
        attacker, defender, game, rng=lambda: 0.99,
    )
    assert attacker.last_instant_action_tick == 5
    before_hp = defender.hp
    combat_mod.resolve_round(game, rng=lambda: 0.99)
    assert defender.hp == before_hp, "instant tick should skip auto-swing"

    walker = placed[0]
    _FakeSession([]).attach(walker)
    dispatch = hooks.get_dispatch()
    dispatch(walker, "south", game)
    assert walker.location.key == "MB00010"
    dispatch(walker, "west", game)
    assert walker.location.key == "WL00001"

    from classic import spells as spells_module
    mage = placed[2]
    _FakeSession([]).attach(mage)
    dispatch(mage, "score", game)
    assert any("Known spells:" in line for line in mage.session.lines)
    assert any("bolt" in line for line in mage.session.lines)
    assert spells_module.spell_ids_for_class("mage") == ["bolt"]
    assert set(spells_module.spell_ids_for_class("cleric")) == {"heal", "smite"}

    # ---- Regression: a real character save must succeed and round-trip. ----
    # engine.persistence passes ``include_heavy=False`` to the blob codec;
    # classic's plain ``character_to_blob(character)`` used to raise
    # TypeError, so no classic character was ever written. save_world
    # swallows per-character failures (it only logs), so assert on the row.
    import json
    from engine import char_index, persistence

    saver = placed[1]
    saver.account = "saveprobe_acct"  # account-linked = must-save login body
    saver.is_npc = False
    char_index.register_character(game, saver)
    # A body never saved before is only written once it is dirty (incremental
    # save); force=True queues it regardless of the Echo coalescing rule.
    persistence.mark_character_dirty(game, saver, force=True)
    persistence.save_world(game.db, game)
    row = game.db.execute(
        "SELECT stats FROM characters WHERE name = ?", (saver.key,),
    ).fetchone()
    assert row is not None, (
        "classic character save wrote no row (blob hook rejected a keyword?)"
    )
    saved_blob = json.loads(row[0])
    assert saved_blob.get("classic_class") == saver.classic_class
    reloaded = Character("SaveProbeReloaded")
    reloaded.classic_class = "war"
    hooks.apply_character_blob(reloaded, saved_blob)
    assert reloaded.classic_class == saver.classic_class, reloaded.classic_class

    print("classic_smoke_ok")


if __name__ == "__main__":
    main()
