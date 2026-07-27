"""verbs/mail.py -- basegame Post Office text mail (engine mail kit).

Uses ``engine.systems.mail`` directly -- no SUPERS ship/courier fiction.
"""

from engine.systems import mail as mail_mod


def cmd_mail(character, args, game):
    """Text mail at a room tagged ``mail``: list / read / discard / send."""
    raw = (args or "").strip()
    if not raw:
        character.session.send("\r\n".join(mail_mod.format_list(character)))
        return
    parts = raw.split(maxsplit=1)
    verb = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if verb == "read":
        _ok, msg = mail_mod.read_letter(character, rest)
        character.session.send(msg)
        return
    if verb in ("discard", "delete", "rm"):
        _ok, msg = mail_mod.discard_letter(character, rest or "all")
        character.session.send(msg)
        return
    if verb == "send":
        bits = rest.split(maxsplit=1)
        if len(bits) < 2:
            character.session.send("Usage: mail send <name> <text>")
            return
        _ok, msg = mail_mod.send_mail(character, bits[0], bits[1], game)
        character.session.send(msg)
        return
    bits = raw.split(maxsplit=1)
    if len(bits) == 2 and verb not in ("list", "inbox"):
        _ok, msg = mail_mod.send_mail(character, bits[0], bits[1], game)
        character.session.send(msg)
        return
    character.session.send(
        "Usage: mail | mail read <n> | mail discard <n|all> | "
        "mail send <name> <text>  (see 'help mail')"
    )
