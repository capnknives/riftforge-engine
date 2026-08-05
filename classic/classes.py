"""classes.py -- OSR class tables loaded from validated catalog JSON."""

from classic.content import load_classes_catalog

CLASS_ORDER = ()
CLASS_NAMES = {}
CLASS_BLURBS = {}
_CLASS_META = {}
CLASS_SKILLS = {}


def _ensure_loaded():
    global CLASS_ORDER, CLASS_NAMES, CLASS_BLURBS, _CLASS_META, CLASS_SKILLS
    if CLASS_ORDER:
        return
    catalog = load_classes_catalog()
    order = []
    names = {}
    blurbs = {}
    meta = {}
    skills = {}
    for class_id in catalog["order"]:
        row = catalog["by_id"][class_id]
        order.append(class_id)
        names[class_id] = row["name"]
        blurbs[class_id] = row["summary"]
        meta[class_id] = {
            "hit_die": row["hit_die"],
            "bab": row["bab"],
            "saves": dict(row["saves"]),
            "armor_bonus": row["armor_bonus"],
            "weapon_die": row["weapon_die"],
            "attack_ability": row["attack_ability"],
        }
        skills[class_id] = tuple(row["class_skills"])
    CLASS_ORDER = tuple(order)
    CLASS_NAMES = names
    CLASS_BLURBS = blurbs
    _CLASS_META = meta
    CLASS_SKILLS = skills


def hit_die(class_id):
    _ensure_loaded()
    meta = _CLASS_META.get(class_id) or _CLASS_META["war"]
    return int(meta["hit_die"])


def class_meta(class_id):
    _ensure_loaded()
    return dict(_CLASS_META.get(class_id) or _CLASS_META["war"])


def _bab_for_level(level, progression):
    level = max(1, min(20, int(level)))
    if progression == "full":
        return level
    if progression == "three_quarters":
        return (level * 3) // 4
    return level // 2


def _save_for_level(level, quality):
    level = max(1, min(20, int(level)))
    if quality == "good":
        return 2 + (level - 1) // 3
    return (level - 1) // 6


def level_row(class_id, level):
    _ensure_loaded()
    meta = class_meta(class_id)
    level = max(1, min(20, int(level)))
    saves = meta["saves"]
    return {
        "level": level,
        "bab": _bab_for_level(level, meta["bab"]),
        "fort": _save_for_level(level, saves["fort"]),
        "ref": _save_for_level(level, saves["ref"]),
        "will": _save_for_level(level, saves["will"]),
        "hit_die": meta["hit_die"],
    }


def attack_bonus_at_level(class_id, level):
    _ensure_loaded()
    meta = class_meta(class_id)
    return _bab_for_level(level, meta["bab"])


def starting_armor_bonus(class_id):
    _ensure_loaded()
    return int(class_meta(class_id).get("armor_bonus", 0))


def weapon_die_sides(class_id):
    _ensure_loaded()
    return int(class_meta(class_id).get("weapon_die", 6))


def attack_ability(class_id):
    _ensure_loaded()
    return class_meta(class_id).get("attack_ability", "STR")
