"""
dialogue.py -- generic Conversation Tree walker (node/edge graph).

State shape per tree id on ``character.dialogue_progress``::

    {
      "current_node": "<node_id>",
      "terminal": False,
      "flags": {},
    }

``dialogue_progress`` is a dict of tree_id -> that blob (persisted).
An open menu is *not* persisted: ``character._active_dialogue`` is
transient (tree_id, npc_key, quest_id). Logout closes the menu; the
next ``talk`` resumes at ``current_node`` (or falls through if the
tree is already ``terminal``).

Game-specific check resolvers and grants register via
``set_dialogue_check_resolver`` / ``set_dialogue_grant_handler``
(wired in ``supers/dialogue/policy.py`` for SUPERS). Zero ``supers``
imports.

Menu I/O: numbered ``1. Option text`` lines; ``[Skill]`` prefix is
plain text (hard rule 7 -- never color-only). ALWAYS_ALLOWED verbs
from ``engine.systems.quests`` pass through command dispatch so a
player is never soft-locked inside a menu.
"""

from __future__ import annotations

import random
import re

# ---------------------------------------------------------------------------
# Game hook slots (SUPERS / basegame register at boot)
# ---------------------------------------------------------------------------

_CHECK_RESOLVERS: dict = {}
_GRANT_HANDLER = None


def set_dialogue_check_resolver(kind, fn):
    """Register fn(character, check, *, npc=None, game=None) -> bool.

    ``kind`` is the ``check.kind`` string (``opposed``, ``skill_dc``,
    ``flat``). Engine implements ``flat`` natively; games override or
    add opposed / skill_dc resolvers that call their contest/skill APIs.
    """
    if not kind:
        return
    _CHECK_RESOLVERS[str(kind)] = fn


def set_dialogue_grant_handler(fn):
    """Register fn(character, grant, game) for option / outcome ``grant`` blobs.

    Engine always stamps ``flag`` onto the current tree's flags first;
    the handler adds game vocabulary (``item``, ``dollars``, ``clue``).
    """
    global _GRANT_HANDLER
    _GRANT_HANDLER = fn


def _clear_hooks_for_tests():
    """Reset resolver / grant slots (smoke only)."""
    global _CHECK_RESOLVERS, _GRANT_HANDLER
    _CHECK_RESOLVERS = {}
    _GRANT_HANDLER = None


# ---------------------------------------------------------------------------
# Progress bookkeeping
# ---------------------------------------------------------------------------

def progress(character):
    """Return ``character.dialogue_progress``, creating ``{}`` when absent."""
    box = getattr(character, "dialogue_progress", None)
    if box is None or not isinstance(box, dict):
        character.dialogue_progress = {}
        return character.dialogue_progress
    return box


def progress_entry(character, tree_id):
    """Return one tree progress blob, or ``None`` when the id is unknown."""
    if not tree_id:
        return None
    return progress(character).get(tree_id)


def ensure_progress(character, tree_id, *, start_node=""):
    """Return an existing entry or create a default-shaped new one."""
    prog = progress(character)
    ent = prog.get(tree_id)
    if ent is None or not isinstance(ent, dict):
        ent = {
            "current_node": start_node or "",
            "terminal": False,
            "flags": {},
        }
        prog[tree_id] = ent
    if not isinstance(ent.get("flags"), dict):
        ent["flags"] = {}
    if "terminal" not in ent:
        ent["terminal"] = False
    if "current_node" not in ent:
        ent["current_node"] = start_node or ""
    return ent


def normalize_dialogue_progress(character):
    """Load-time normalize: accept missing keys, always write the v1 shape.

    Boot-heal-discipline: pre-feature saves have no ``dialogue_progress``;
    a corrupt entry becomes a default blob rather than crashing load.
    """
    prog = progress(character)
    for tree_id, ent in list(prog.items()):
        if not isinstance(ent, dict):
            prog[tree_id] = {
                "current_node": "",
                "terminal": False,
                "flags": {},
            }
            continue
        ent.setdefault("current_node", "")
        ent.setdefault("terminal", False)
        if not isinstance(ent.get("flags"), dict):
            ent["flags"] = {}
    return prog


def close_active(character):
    """Drop the transient open-menu pointer (logout / terminal node)."""
    if character is None:
        return
    character._active_dialogue = None


# ---------------------------------------------------------------------------
# Catalog lookup (loader is a sibling module)
# ---------------------------------------------------------------------------

def _get_tree(tree_id):
    """Return one tree dict or None (imported lazily to avoid cycles)."""
    from engine.systems import dialogue_loader as loader_mod

    return loader_mod.get_tree(tree_id)


def _node(tree, node_id):
    """Return one node dict or None."""
    if not tree or not node_id:
        return None
    nodes = tree.get("nodes") or {}
    return nodes.get(node_id)


# ---------------------------------------------------------------------------
# Requires / visibility (complete_when-shaped + shorthand)
# ---------------------------------------------------------------------------

def _tree_flag(character, tree_id, flag_name):
    """True when this tree (or its linked quest) has ``flag_name`` set."""
    if not flag_name:
        return False
    ent = progress_entry(character, tree_id) or {}
    flags = ent.get("flags") or {}
    if flags.get(flag_name):
        return True
    # Linked quest flags (a Conversation Tree grant may also stamp the
    # quest that opened it -- see the grant handler).
    active = getattr(character, "_active_dialogue", None) or {}
    qid = active.get("quest_id")
    if qid:
        from engine.systems.quest_flags import get_flag

        if get_flag(character, qid, flag_name):
            return True
    return False


def _has_clue(character, clue_id):
    """True when ``clue_id`` is in ``character.known_clues``."""
    if not clue_id:
        return False
    clues = getattr(character, "known_clues", None)
    if isinstance(clues, set):
        return clue_id in clues
    if isinstance(clues, (list, tuple)):
        return clue_id in clues
    return False


def match_requires(character, tree_id, req):
    """Evaluate an option ``requires`` predicate. True = option is visible.

    Accepts both the shorthand used in the design examples
    (``{"not_flag": "x"}``, ``{"has_clue": "y"}``) and the typed
    complete_when shape (``{"type": "not_flag", "flag": "..."}``).
    Unknown shapes default to visible so a typo cannot soft-lock a
    scene behind an un-pickable option.
    """
    if not req or not isinstance(req, dict):
        return True
    kind = req.get("type")
    # Shorthand (no type key): the design's worked-example shape.
    if not kind:
        if "not_flag" in req:
            return not _tree_flag(character, tree_id, req.get("not_flag"))
        if "has_clue" in req:
            return _has_clue(character, req.get("has_clue"))
        if "flag" in req:
            return _tree_flag(character, tree_id, req.get("flag"))
        return True
    if kind == "not_flag":
        return not _tree_flag(character, tree_id, req.get("flag"))
    if kind == "has_clue":
        clue = req.get("clue") or req.get("has_clue")
        return _has_clue(character, clue)
    if kind == "flag":
        return _tree_flag(character, tree_id, req.get("flag"))
    if kind == "character_flag":
        flag_name = req.get("flag")
        if not flag_name:
            return False
        return bool(getattr(character, flag_name, False))
    return True


def visible_options(character, tree, node, tree_id):
    """Return the option dicts that pass ``requires`` (menu order)."""
    out = []
    for option in (node or {}).get("options") or []:
        if not isinstance(option, dict):
            continue
        if match_requires(character, tree_id, option.get("requires")):
            out.append(option)
    return out


# ---------------------------------------------------------------------------
# Menu render (plain numbered list -- rule 7)
# ---------------------------------------------------------------------------

_SKILL_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*")


def option_label(option):
    """Player-facing menu text, with a ``[Skill]`` tag when a check is on.

    Authors normally write the tag into ``label``. If they forget, we
    still prefix so screenreader and sighted players both get the check
    named -- never color-only.
    """
    label = (option.get("label") or option.get("id") or "?").strip()
    check = option.get("check") or {}
    if check and not _SKILL_TAG_RE.match(label):
        kind = (check.get("kind") or "").strip().lower()
        if kind == "opposed":
            tag = (check.get("mode") or "check").strip().title()
        elif kind == "skill_dc":
            tag = (check.get("skill") or "Skill").strip().title()
        elif kind == "flat":
            tag = "Chance"
        else:
            tag = "Check"
        label = f"[{tag}] {label}"
    return label


def format_menu_lines(character, tree, node, tree_id):
    """Return numbered menu lines for the visible options (no ASCII box)."""
    lines = []
    for i, option in enumerate(visible_options(character, tree, node, tree_id), start=1):
        lines.append(f"{i}. {option_label(option)}")
    return lines


def _send(character, lines):
    """Send one string or a list of strings to the character's session."""
    session = getattr(character, "session", None)
    if session is None:
        return
    if isinstance(lines, str):
        lines = [lines]
    for line in lines or []:
        if line is None:
            continue
        text = str(line)
        if text:
            session.send(text)


def render_node(character, tree_id, node_id=None, *, game=None):
    """Show the node's ``say`` plus the numbered option menu.

    A node with ``terminal: true`` ends the scene after ``say`` (no menu).
    """
    tree = _get_tree(tree_id)
    if tree is None:
        close_active(character)
        _send(character, "The conversation trails off.")
        return
    ent = ensure_progress(character, tree_id, start_node=tree.get("start_node") or "")
    if not node_id:
        node_id = ent.get("current_node") or tree.get("start_node")
    ent["current_node"] = node_id
    node = _node(tree, node_id)
    if node is None:
        close_active(character)
        _send(character, "The conversation trails off.")
        return
    say = node.get("say") or []
    _send(character, say)
    if node.get("terminal"):
        _mark_terminal(character, tree_id, game=game)
        return
    menu = format_menu_lines(character, tree, node, tree_id)
    if not menu:
        # No visible replies -- nothing the player can pick. Close
        # rather than soft-lock them in an empty menu.
        _mark_terminal(character, tree_id, game=game)
        return
    _send(character, menu)


# ---------------------------------------------------------------------------
# Open / pick
# ---------------------------------------------------------------------------

def open_tree(character, tree_id, *, npc=None, game=None, quest_id=None):
    """Begin or resume a Conversation Tree. Returns True if the menu opened.

    Already-terminal trees return False so ``talk`` can fall through to
    static ``npc_lines``. Mid-conversation resume uses ``current_node``.
    """
    tree = _get_tree(tree_id)
    if tree is None or character is None:
        return False
    start = tree.get("start_node") or ""
    ent = ensure_progress(character, tree_id, start_node=start)
    if ent.get("terminal"):
        return False
    node_id = ent.get("current_node") or start
    if not node_id or _node(tree, node_id) is None:
        return False
    npc_key = ""
    if npc is not None:
        npc_key = getattr(npc, "key", None) or ""
    character._active_dialogue = {
        "tree_id": tree_id,
        "npc_key": npc_key,
        "quest_id": quest_id,
    }
    # Keep a live NPC pointer for opposed checks this session only.
    character._active_dialogue_npc = npc
    render_node(character, tree_id, node_id, game=game)
    return bool(getattr(character, "_active_dialogue", None))


def _option_keywords(option):
    """Lowercased tokens a player can type instead of the menu number."""
    keys = []
    oid = (option.get("id") or "").strip().lower()
    if oid:
        keys.append(oid)
    label = option_label(option)
    match = _SKILL_TAG_RE.match(label)
    rest = label
    if match:
        keys.append(match.group(1).strip().lower())
        rest = label[match.end():].strip()
    first = rest.split(None, 1)[0].lower() if rest else ""
    first = first.strip(".,!?;:\"'")
    if first and first not in keys:
        keys.append(first)
    return keys


def _resolve_pick(visible, verb, args):
    """Return the chosen option dict, or None."""
    token = (verb or "").strip().lower()
    extra = (args or "").strip().lower()
    if token.isdigit():
        idx = int(token)
        if 1 <= idx <= len(visible):
            return visible[idx - 1]
        return None
    # Full "id with spaces" via verb + args.
    combined = f"{token} {extra}".strip() if extra else token
    for option in visible:
        keys = _option_keywords(option)
        if token in keys or combined in keys:
            return option
        oid = (option.get("id") or "").strip().lower()
        if oid and combined == oid:
            return option
    return None


def _resolve_check(character, check, *, npc=None, game=None):
    """Run one check block. ``flat`` is engine-native; others use hooks."""
    if not check or not isinstance(check, dict):
        return True
    kind = (check.get("kind") or "").strip().lower()
    if kind == "flat":
        fn = _CHECK_RESOLVERS.get("flat")
        if fn is not None:
            return bool(fn(character, check, npc=npc, game=game))
        try:
            chance = float(check.get("chance", 0.0) or 0.0)
        except (TypeError, ValueError):
            chance = 0.0
        return random.random() < chance
    fn = _CHECK_RESOLVERS.get(kind)
    if fn is None:
        return False
    try:
        return bool(fn(character, check, npc=npc, game=game))
    except Exception:
        return False


def _apply_grant(character, grant, *, tree_id=None, game=None):
    """Stamp tree flags, then call the registered grant handler."""
    if not grant or not isinstance(grant, dict):
        return
    if "flag" in grant and tree_id:
        ent = ensure_progress(character, tree_id)
        flags = ent.setdefault("flags", {})
        flags[grant["flag"]] = True
    if _GRANT_HANDLER is not None:
        try:
            _GRANT_HANDLER(character, grant, game)
        except Exception:
            pass


def _mark_terminal(character, tree_id, game=None):
    """Record that this tree finished and close the open menu."""
    ent = ensure_progress(character, tree_id)
    ent["terminal"] = True
    close_active(character)
    # Advance any quest step waiting on dialogue_done (Task A).
    try:
        from engine.systems import quests as quests_engine

        quests_engine.notify(
            character, "dialogue_done", tree=tree_id, game=game,
        )
    except Exception:
        pass


def _goto_or_stay(character, tree_id, outcome, node_id, *, game=None):
    """Follow ``goto``, end on ``terminal``, or re-show the current node."""
    if (outcome or {}).get("terminal"):
        _mark_terminal(character, tree_id, game=game)
        return
    nxt = (outcome or {}).get("goto")
    if nxt:
        ent = ensure_progress(character, tree_id)
        ent["current_node"] = nxt
        render_node(character, tree_id, nxt, game=game)
        return
    render_node(character, tree_id, node_id, game=game)


def pick_option(character, verb, args="", *, game=None):
    """Resolve a menu pick (number or keyword). Returns True if consumed."""
    active = getattr(character, "_active_dialogue", None) or {}
    tree_id = active.get("tree_id")
    if not tree_id:
        close_active(character)
        return False
    tree = _get_tree(tree_id)
    ent = progress_entry(character, tree_id) or {}
    node_id = ent.get("current_node") or (tree or {}).get("start_node")
    node = _node(tree, node_id) if tree else None
    if tree is None or node is None:
        close_active(character)
        _send(character, "The conversation trails off.")
        return True
    visible = visible_options(character, tree, node, tree_id)
    option = _resolve_pick(visible, verb, args)
    if option is None:
        _send(
            character,
            "That's not a reply. Type a number from the list, or look / help / quit.",
        )
        menu = format_menu_lines(character, tree, node, tree_id)
        _send(character, menu)
        return True
    check = option.get("check")
    npc = getattr(character, "_active_dialogue_npc", None)
    character._dialogue_grant_target = tree_id
    try:
        if check:
            passed = _resolve_check(character, check, npc=npc, game=game)
            outcome = option.get("success") if passed else option.get("failure")
            if not isinstance(outcome, dict):
                outcome = {}
            _send(character, outcome.get("say") or [])
            _apply_grant(
                character, outcome.get("grant"), tree_id=tree_id, game=game,
            )
            _goto_or_stay(character, tree_id, outcome, node_id, game=game)
        else:
            # Bare option: grant / goto / terminal sit on the option itself.
            _send(character, option.get("say") or [])
            _apply_grant(
                character, option.get("grant"), tree_id=tree_id, game=game,
            )
            _goto_or_stay(character, tree_id, option, node_id, game=game)
    finally:
        character._dialogue_grant_target = None
    return True


def try_handle_menu_input(character, verb, args="", game=None):
    """Dispatch-hook: consume a menu pick, or return False for ALWAYS_ALLOWED.

    Called from ``commands._dispatch_body`` *before* verb lookup. Returns
    True when the line was handled as dialogue (valid pick or a "not a
    reply" hint). Returns False when there is no open menu, or the verb
    is in ``ALWAYS_ALLOWED`` (look / help / quit / …) so normal dispatch
    continues.
    """
    if not getattr(character, "_active_dialogue", None):
        return False
    raw = (verb or "").strip().lower()
    # Exact set from engine.systems.quests -- do not fork a second list.
    from engine.systems.quests import ALWAYS_ALLOWED

    if raw in ALWAYS_ALLOWED:
        return False
    pick_option(character, verb, args, game=game)
    return True
