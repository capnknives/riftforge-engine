"""phone.py -- payphone / handset dial demo (engine/systems/phone H7a)."""

from engine.systems import phone as phone_mod


def cmd_dial(character, args, game):
    """Dial a phone number or alias (see 'help phone')."""
    character.session.send(phone_mod.dial(character, game, args))


def cmd_call(character, args, game):
    """Alias for dial."""
    cmd_dial(character, args, game)


def cmd_answer(character, args, game):
    """Answer a ringing phone."""
    del args
    character.session.send(phone_mod.answer_call(character, game))


def cmd_hangup(character, args, game):
    """Hang up the active call."""
    del args
    character.session.send(phone_mod.hangup(character, game))
