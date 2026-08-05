"""help_topics.py -- classic HELP_TOPICS / HELP_CATEGORIES."""

from classic import classes as classes_module
from classic import skills as skills_module
from classic import spells as spells_module

_classes_lines = "\n".join(
    f"  {cid} -- {classes_module.CLASS_BLURBS[cid]}"
    for cid in classes_module.CLASS_ORDER
)

_skills_lines = "\n".join(f"  {s}" for s in skills_module.ALL_SKILLS)

_spell_lines = "\n".join(
    f"  {sid} -- {row['help']}"
    for sid, row in sorted(spells_module.SPELLS.items())
)

HELP_TOPICS = {
    "classic": """Classic fantasy -- how you play

Millbrook is a ten-room village on the edge of the wilds. Make a character,
walk the village, then step out onto the trail for goblins and wolves.

How you play:
  1. Create a character (class + abilities at login).
  2. score or sheet -- see stats, saves, and skills.
  3. exit the trailhead to reach the wilderness map.
  4. attack <name> -- instant melee; combat continues each heartbeat.
  5. cast <spell> <target> -- mage bolt, cleric heal/smite.
  6. skill <name> [dc] -- roll a skill check (default DC 12).

See also: help classes | help combat | help skills
""",
    "classes": f"""Classic -- classes (levels 1-20)

Pick one at character creation:

{_classes_lines}

Each class has hit dice, attack bonus, Fort/Ref/Will saves, armor, and
class skills on your sheet. Full level-20 rows: sheet command.

See also: help classic | help skills
""",
    "combat": f"""Classic -- active combat

OSR ascending AC: d20 + attack bonus vs target AC. STR adds to War and
Cleric melee; Rogues use DEX. Natural 20 is a critical (extra damage die).

How you play:
  1. attack <name> -- strike now and set them as your target.
  2. While you have a target, the heartbeat swings for you each tick unless
     you just attacked or cast on that same tick.
  3. cast <spell> <target> -- spells below.

Spells (MVP):
{_spell_lines}

See also: help classic | help classes
""",
    "skills": f"""Classic -- skills

Class skills are listed on score/sheet. Roll:

  skill <name> [dc]

Default DC is 12. Bonus = skill ranks + ability modifier.

All skills:
{_skills_lines}

See also: help classes | help classic
""",
    "score": """Classic -- score

Shows class, level, STR/DEX/CON/INT/WIS/CHA with modifiers, attack and
save bonuses, AC, HP, class skills, and known spells (Mage/Cleric).
`sc` is an alias. `sheet` adds level 1/10/20 progression rows.

See also: help sheet | help classic | help combat
""",
}

HELP_CATEGORIES = [
    ("Classic fantasy", ["classic", "classes", "combat", "skills", "score"]),
]
