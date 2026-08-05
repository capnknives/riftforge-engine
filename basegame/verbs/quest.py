"""
quest.py -- basegame authored quest verbs (fetch_pebble demo).
"""

from __future__ import annotations


def cmd_quest(character, args, game):
    """List active quests and offers, or show the quest log."""
    from basegame import quests as bg_quests
    bg_quests.register_quest_hooks()
    from engine.systems import quests as quests_mod

    text = (args or "").strip().lower()
    if text in ("log", "list", "status"):
        for line in quests_mod.format_log_lines(character):
            character.session.send(line)
        return
    if text.startswith("accept "):
        qid = text.split(None, 1)[1].strip()
        ok, msg = quests_mod.begin(character, qid, game=game)
        character.session.send(msg)
        return
    for line in quests_mod.offer_lines(character, character.location):
        character.session.send(line)


def cmd_quest_accept(character, args, game):
    """Accept an authored quest by id (alias for quest accept <id>)."""
    from basegame import quests as bg_quests
    bg_quests.register_quest_hooks()
    from engine.systems import quests as quests_mod

    qid = (args or "").strip()
    if not qid:
        character.session.send("Accept which quest? (quest accept <id>)")
        return
    ok, msg = quests_mod.begin(character, qid, game=game)
    character.session.send(msg)
