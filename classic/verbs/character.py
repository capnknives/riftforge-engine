"""verbs/character.py -- score / sheet for classic characters."""

from classic import classes as classes_module
from classic import skills as skills_module
from classic import spells as spells_module
from classic import stats as stats_module
from engine.style import format_sheet


def build_sheet_lines(character):
    """Body lines shared by score, sheet, and post-chargen summary."""
    class_id = getattr(character, "classic_class", None) or "(none)"
    level = int(getattr(character, "classic_level", 1) or 1)
    class_label = classes_module.CLASS_NAMES.get(class_id, str(class_id))
    row = classes_module.level_row(class_id, level) if class_id in classes_module.CLASS_ORDER else {}

    body = [
        f"{class_label} level {level}",
        "",
    ]
    for name in stats_module.ABILITY_NAMES:
        score = stats_module.get_ability(character, name)
        mod = stats_module.ability_mod(score)
        sign = "+" if mod >= 0 else ""
        body.append(f"{name}: {score:g} ({sign}{mod})")
    body.append("")
    if row:
        body.append(
            f"Attack +{row['bab']}  Fort +{row['fort']}  "
            f"Ref +{row['ref']}  Will +{row['will']}"
        )
    body.append(f"AC: {stats_module.armor_class(character)}")
    body.append(
        f"HP: {character.hp:g}/{stats_module.max_hp(character)}"
    )
    class_skills = classes_module.CLASS_SKILLS.get(class_id, ())
    if class_skills:
        body.append("")
        body.append("Class skills:")
        for skill in class_skills:
            bonus = skills_module.skill_bonus(character, skill)
            sign = "+" if bonus >= 0 else ""
            body.append(f"  {skill}: {sign}{bonus}")
    known = spells_module.known_spells_for_class(class_id)
    if known:
        body.append("")
        body.append("Known spells:")
        body.extend(known)
    return body


def cmd_score(character, args, game):
    """Show class, abilities, saves, AC, HP, and class skills."""
    screenreader = bool(getattr(character, "screenreader", False))
    for line in format_sheet("Score", build_sheet_lines(character), screenreader=screenreader):
        character.session.send(line)


def cmd_sheet(character, args, game):
    """Full character sheet including level-20 progression preview."""
    screenreader = bool(getattr(character, "screenreader", False))
    body = build_sheet_lines(character)
    class_id = getattr(character, "classic_class", None)
    if class_id in classes_module.CLASS_ORDER:
        body.append("")
        body.append("Progression (levels 1, 10, 20):")
        for lvl in (1, 10, 20):
            row = classes_module.level_row(class_id, lvl)
            body.append(
                f"  L{row['level']}: ATK +{row['bab']}  "
                f"F +{row['fort']}  R +{row['ref']}  W +{row['will']}  "
                f"HD d{row['hit_die']}"
            )
    for line in format_sheet("Sheet", body, screenreader=screenreader):
        character.session.send(line)
