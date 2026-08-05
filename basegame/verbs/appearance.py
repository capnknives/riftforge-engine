"""appearance.py -- slot picks via engine/systems/appearance (H7b demo)."""

from engine.systems import appearance as appearance_mod


def cmd_appearance(character, args, game):
    """List or set appearance slots (see 'help appearance')."""
    del game
    appearance_mod.validate_all_kits()
    kit = appearance_mod.kit_for_character(character)
    slots = appearance_mod.slots_for_kit(kit)
    if not hasattr(character, "appearance") or character.appearance is None:
        character.appearance = appearance_mod.default_appearance()

    parts = (args or "").strip().split(None, 1)
    if not parts or not parts[0]:
        lines = ["Your appearance slots:"]
        for slot in slots:
            current = character.appearance.get(slot)
            label = appearance_mod.display(slot, current, kit=kit) if current else "unset"
            lines.append(f"  {slot}: {label}")
        lines.append("Set with: appearance <slot> <option-id>")
        character.session.send("\r\n".join(lines))
        return

    slot = parts[0].strip().lower()
    if slot not in slots:
        character.session.send(
            f"Unknown slot {slot!r}. Try: {', '.join(slots)}"
        )
        return
    if len(parts) < 2 or not parts[1].strip():
        options = appearance_mod.list_options(slot, kit=kit)
        character.session.send(
            f"Options for {slot}:\r\n" + "\r\n".join(f"  {line}" for line in options)
        )
        return

    option_id = parts[1].strip().lower().replace(" ", "_")
    valid = appearance_mod.valid_ids(slot, kit=kit)
    if option_id not in valid:
        character.session.send(
            f"Not a valid {slot} option. Try: {', '.join(sorted(valid))}"
        )
        return
    character.appearance[slot] = option_id
    appearance_mod.apply_appearance(character)
    label = appearance_mod.display(slot, option_id, kit=kit)
    character.session.send(f"You set {slot} to {label}.")
    if appearance_mod.is_complete(character.appearance):
        character.session.send(character.description)
