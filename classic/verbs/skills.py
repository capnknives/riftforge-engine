"""verbs/skills.py -- roll a skill check vs DC."""

from classic import skills as skills_module


def cmd_skill(character, args, game):
    """Roll d20 + skill bonus vs DC (default DC 12)."""
    parts = (args or "").strip().split()
    if not parts:
        character.session.send("Check what skill?  skill <name> [dc]")
        return
    skill = parts[0].lower()
    dc = 12
    if len(parts) > 1 and parts[1].isdigit():
        dc = int(parts[1])
    if skill not in skills_module.ALL_SKILLS:
        character.session.send(
            f"Unknown skill {skill!r}. See help skills."
        )
        return
    success, roll, total = skills_module.roll_skill_check(
        character, skill, dc,
    )
    sign = "+" if skills_module.skill_bonus(character, skill) >= 0 else ""
    bonus = skills_module.skill_bonus(character, skill)
    if success:
        character.session.send(
            f"[OK] {skill}: rolled {roll}{sign}{bonus} = {total} vs DC {dc}."
        )
    else:
        character.session.send(
            f"[FAIL] {skill}: rolled {roll}{sign}{bonus} = {total} vs DC {dc}."
        )
