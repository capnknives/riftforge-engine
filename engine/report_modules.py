"""
report_modules.py -- modular diagnostic slices for bug/suggest context.

Engine stays game-agnostic: SUPERS (or another consumer) registers
``ReportModule`` entries via ``register()``. Filing passes recent command
history + description text so modules can fire from live state, verb
domains, keywords, sticky recency, or dependent threshold hooks.
"""

from __future__ import annotations

import re
import time

# How long a domain stays "hot" after it last fired (real seconds).
# Survives filling the 10-line command history with look/say after a fight.
REPORT_DOMAIN_STICKY_SEC = 900

_STICKY_ATTR = "_report_domain_sticky"

# First-token verb -> domain tags (engine-common + SUPERS verbs).
# Unknown verbs are ignored. Consumers may extend via register_verb_domains.
_VERB_DOMAINS = {
    # Combat
    "attack": ("combat",),
    "kill": ("combat",),
    "flee": ("combat",),
    "retreat": ("combat",),
    "approach": ("combat",),
    "rush": ("combat",),
    "lunge": ("combat",),
    "grapple": ("combat",),
    "spar": ("combat",),
    "sparaccept": ("combat",),
    "consider": ("combat",),
    "aim": ("combat",),
    "press": ("combat",),
    "guard": ("combat",),
    "feint": ("combat",),
    "stance": ("combat",),
    "style": ("combat",),
    "fightlog": ("combat",),
    "judgment": ("combat",),
    "blade": ("combat",),
    "bite": ("combat",),
    "maul": ("combat",),
    "devour": ("combat",),
    "consume": ("combat",),
    "crush": ("combat",),
    "rend": ("combat",),
    "stake": ("combat",),
    "decapitate": ("combat",),
    "ward": ("combat", "magic"),
    "smite": ("combat",),
    "health": ("combat",),
    "scan": ("combat",),
    "intimidate": ("combat",),
    "giveup": ("combat", "spirit_death"),
    "yield": ("combat", "spirit_death"),
    "mercy": ("combat", "spirit_death"),
    "haul": ("combat", "spirit_death"),
    "admit": ("spirit_death", "accord_clinic"),
    "admitpatient": ("spirit_death", "accord_clinic"),
    "checkin": ("spirit_death", "accord_clinic"),
    "treat": ("spirit_death", "accord_clinic"),
    "clinic": ("spirit_death", "accord_clinic"),
    "hospital": ("spirit_death", "accord_clinic"),
    # Lifestyle / Cadence
    "needs": ("needs_lifestyle",),
    "buy": ("needs_lifestyle",),
    "eat": ("needs_lifestyle",),
    "drink": ("needs_lifestyle",),
    "sleep": ("needs_lifestyle",),
    "wake": ("needs_lifestyle",),
    "wash": ("needs_lifestyle",),
    "work": ("needs_lifestyle",),
    "quitjob": ("needs_lifestyle",),
    "train": ("needs_lifestyle",),
    "regimen": ("needs_lifestyle",),
    "form": ("needs_lifestyle", "vessel"),
    "pack": ("needs_lifestyle",),
    "seek": ("needs_lifestyle", "craft_contracts"),
    "cook": ("needs_lifestyle", "gathering"),
    "grocery": ("needs_lifestyle",),
    # Craft contracts
    "investigate": ("craft_contracts",),
    "haunt": ("craft_contracts",),
    "claim": ("craft_contracts", "homestead"),
    "trap": ("craft_contracts",),
    "clear": ("craft_contracts",),
    "case": ("craft_contracts",),
    "bleed": ("craft_contracts",),
    # Crime / containers (lockpick / open strongbox class)
    "rob": ("crime",),
    "steal": ("crime",),
    "warrant": ("crime",),
    "arrest": ("crime",),
    "turnin": ("crime",),
    "payfine": ("crime",),
    "hotwire": ("crime", "vehicle_travel"),
    "book": ("crime",),
    "lockup": ("crime",),
    "lockpick": ("crime", "stealth", "containers"),
    "open": ("containers", "crime"),
    "close": ("containers",),
    "unlock": ("containers", "crime"),
    "pick": ("crime", "stealth", "containers"),
    # Vessel
    "possess": ("vessel",),
    "ride": ("vessel", "vehicle_travel"),
    "vacate": ("vessel",),
    "husk": ("vessel",),
    # Divinity
    "dominion": ("divinity",),
    "claimseat": ("divinity",),
    "scry": ("divinity",),
    "focus": ("divinity",),
    "twin": ("divinity",),
    "pray": ("divinity",),
    "bilocate": ("divinity",),
    # Pit
    "dothepit": ("pit_dungeon", "combat"),
    "pit": ("pit_dungeon",),
    "descend": ("pit_dungeon",),
    # Travel / overland / walk
    "drive": ("vehicle_travel",),
    "board": ("vehicle_travel",),
    "enter": ("vehicle_travel",),
    "exit": ("vehicle_travel",),
    "park": ("vehicle_travel",),
    "dispatch": ("vehicle_travel",),
    "fly": ("vehicle_travel",),
    "ferry": ("vehicle_travel",),
    "walk": ("vehicle_travel",),
    "jog": ("vehicle_travel",),
    "run": ("vehicle_travel",),
    "atlas": ("vehicle_travel",),
    "taxi": ("vehicle_travel",),
    "gas": ("vehicle_travel",),
    "mechanic": ("vehicle_travel",),
    "tow": ("vehicle_travel",),
    "leave": ("vehicle_travel",),
    "makecar": ("vehicle_travel",),
    "vehicle": ("vehicle_travel",),
    "vehicles": ("vehicle_travel",),
    "pursue": ("vehicle_travel", "combat"),
    "ram": ("vehicle_travel", "combat"),
    "driveby": ("vehicle_travel", "combat"),
    "jerry": ("vehicle_travel",),
    "repaircar": ("vehicle_travel",),
    "charter": ("vehicle_travel",),
    # Account / chars
    "account": ("account",),
    "delete": ("account",),
    "switch": ("account",),
    # Mission / tutorial
    "mission": ("mission_tutorial",),
    "quest": ("mission_tutorial",),
    "tutorial": ("mission_tutorial",),
    "journal": ("mission_tutorial",),
    # Fold
    "fold": ("fold",),
    "unfold": ("fold",),
    # Spirit
    "spirit": ("spirit_death",),
    "spiritlook": ("spirit_death",),
    # Magic / rites
    "cast": ("magic",),
    "ritual": ("magic",),
    "commune": ("magic",),
    "hex": ("magic",),
    "glamour": ("magic",),
    "slumber": ("magic",),
    "portal": ("magic",),
    "mana": ("magic",),
    "spells": ("magic",),
    # Homestead / housing
    "homestead": ("homestead",),
    "remodel": ("homestead",),
    "yard": ("homestead",),
    "buyhome": ("homestead",),
    "rent": ("homestead",),
    # Gathering / lifestyle professions
    "mine": ("gathering",),
    "smelt": ("gathering",),
    "scavenge": ("gathering",),
    "salvage": ("gathering",),
    "farm": ("gathering",),
    "fish": ("gathering",),
    "hunt": ("gathering", "craft_contracts"),
    "stalk": ("gathering",),
    "snare": ("gathering",),
    "butcher": ("gathering",),
    "fillet": ("gathering",),
    "forage": ("gathering",),
    "craft": ("gathering",),
    "chop": ("gathering",),
    "skin": ("gathering",),
    # Autoloot / corpse scoop
    "autoloot": ("autoloot",),
    "loot": ("autoloot",),
    "scoop": ("autoloot",),
    # Who / roster
    "who": ("who_roster",),
    "whofull": ("who_roster",),
    # Help lookup
    "help": ("help_lookup", "session_ui"),
    "more": ("session_ui", "help_lookup"),
    "stop": ("session_ui",),
    # Skills / mastery (partial-overlay window)
    "skills": ("skills_training",),
    "skill": ("skills_training",),
    "disciplines": ("skills_training",),
    # Changelog unread cursor
    "changes": ("changelog",),
    "unread": ("changelog",),
    # Stealth / veil overlay
    "hide": ("stealth",),
    "sneak": ("stealth",),
    "veil": ("stealth", "magic"),
    "pierceveil": ("stealth", "magic"),
    "veiloff": ("stealth", "magic"),
    # Phone / radio
    "phone": ("comms",),
    "call": ("comms",),
    "radio": ("comms",),
    "mail": ("comms",),
    "oocmail": ("comms",),
    "text": ("comms",),
    "sms": ("comms",),
    # Clinic / Accord / DoorDash (ward courier + cleaner camping)
    "doordash": ("accord_clinic", "needs_lifestyle"),
    # Hold / pin already on combat via grapple; keep struggle/hold explicit
    "hold": ("combat",),
    "pin": ("combat",),
    "struggle": ("combat",),
    # Origin kit (hellhound / trickster)
    "leash": ("origin_kit",),
    "releasehound": ("origin_kit",),
    "warp": ("origin_kit",),
    "laylow": ("origin_kit", "stealth"),
}

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_modules = []
_modules_by_id = {}


class ReportModule:
    """One gated diagnostic slice for bug/suggest context."""

    def __init__(
        self,
        module_id,
        *,
        domains=None,
        live_triggers=None,
        history_domains=None,
        keywords=None,
        phrases=None,
        snapshot=None,
        cross_fire=None,
    ):
        self.id = str(module_id)
        self.domains = tuple(domains or (self.id,))
        self.live_triggers = live_triggers  # fn(character, game) -> bool
        self.history_domains = frozenset(history_domains or self.domains)
        self.keywords = frozenset(
            (k or "").strip().lower() for k in (keywords or ()) if k
        )
        self.phrases = tuple(
            (p or "").strip().lower() for p in (phrases or ()) if p
        )
        self.snapshot = snapshot  # fn(character, game) -> dict|None
        self.cross_fire = tuple(cross_fire or ())


def register(module):
    """Register (or replace) a ReportModule by id."""
    if not isinstance(module, ReportModule):
        raise TypeError("register() expects a ReportModule")
    existing = _modules_by_id.get(module.id)
    if existing is not None:
        _modules.remove(existing)
    _modules.append(module)
    _modules_by_id[module.id] = module
    return module


def register_verb_domains(mapping):
    """Merge extra verb -> domain tuples into the shared map."""
    if not isinstance(mapping, dict):
        return
    for verb, domains in mapping.items():
        key = str(verb or "").strip().lower()
        if not key:
            continue
        prior = list(_VERB_DOMAINS.get(key, ()))
        for dom in domains or ():
            if dom and dom not in prior:
                prior.append(dom)
        _VERB_DOMAINS[key] = tuple(prior)


def clear_registry():
    """Test helper: drop all registered modules."""
    _modules.clear()
    _modules_by_id.clear()


def list_modules():
    """Return registered modules in registration order."""
    return list(_modules)


def note_domains(character, domains):
    """Stamp sticky timestamps for the given domain ids on ``character``."""
    if character is None or not domains:
        return
    now = time.monotonic()
    sticky = getattr(character, _STICKY_ATTR, None)
    if not isinstance(sticky, dict):
        sticky = {}
        setattr(character, _STICKY_ATTR, sticky)
    for domain in domains:
        key = str(domain or "").strip()
        if key:
            sticky[key] = now


def clear_sticky(character):
    """Drop sticky domain stamps (logout / Echo detach)."""
    if character is None:
        return
    if hasattr(character, _STICKY_ATTR):
        try:
            delattr(character, _STICKY_ATTR)
        except Exception:
            setattr(character, _STICKY_ATTR, {})


def active_sticky_domains(character, *, now=None, ttl=REPORT_DOMAIN_STICKY_SEC):
    """Return domain ids still within the sticky TTL."""
    if character is None:
        return set()
    sticky = getattr(character, _STICKY_ATTR, None)
    if not isinstance(sticky, dict) or not sticky:
        return set()
    if now is None:
        now = time.monotonic()
    alive = set()
    expired = []
    for domain, stamped in sticky.items():
        try:
            age = now - float(stamped)
        except (TypeError, ValueError):
            expired.append(domain)
            continue
        if age <= float(ttl):
            alive.add(str(domain))
        else:
            expired.append(domain)
    for domain in expired:
        sticky.pop(domain, None)
    return alive


def _history_lines(history):
    """Normalize history entries to (line, traceback_or_None) pairs."""
    out = []
    if not history:
        return out
    for entry in history:
        if isinstance(entry, (list, tuple)) and entry:
            line = entry[0]
            tb = entry[1] if len(entry) > 1 else None
            out.append((str(line or ""), tb))
        elif isinstance(entry, str):
            out.append((entry, None))
    return out


def history_domain_hits(history):
    """Map recent command history into domain ids + error flag."""
    domains = set()
    errors_present = False
    for line, tb in _history_lines(history):
        if tb:
            errors_present = True
        text = (line or "").strip()
        if not text:
            continue
        # Skip the filing command itself if somehow still present.
        low = text.lower()
        if low.startswith("bug ") or low.startswith("suggest ") or low in (
            "bug", "suggest",
        ):
            continue
        first = low.split(None, 1)[0]
        # Strip leading slash clients sometimes send.
        if first.startswith("/"):
            first = first[1:]
        for domain in _VERB_DOMAINS.get(first, ()):
            domains.add(domain)
    return domains, errors_present


def _tokenize(text):
    """Lowercase alphanumeric tokens from free text."""
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(str(text))]


def keyword_module_hits(description, modules=None):
    """Return (module_ids, matched_tokens) from description sniff."""
    modules = modules if modules is not None else _modules
    tokens = _tokenize(description)
    token_set = set(tokens)
    low = " ".join(tokens)
    matched = set()
    module_ids = set()
    for mod in modules:
        hit = False
        for word in mod.keywords:
            if word in token_set:
                matched.add(word)
                hit = True
        for phrase in mod.phrases:
            if phrase and phrase in low:
                matched.add(phrase)
                hit = True
        if hit:
            module_ids.add(mod.id)
    return module_ids, sorted(matched)


def resolve_signals(character, game, *, history=None, description=None):
    """Collect live / history / keyword / sticky / error signals."""
    modules = list(_modules)
    live_ids = set()
    for mod in modules:
        if mod.live_triggers is None:
            continue
        try:
            if mod.live_triggers(character, game):
                live_ids.add(mod.id)
        except Exception:
            # Never abort filing because a trigger raised.
            live_ids.add(mod.id)

    hist_domains, errors_present = history_domain_hits(history)
    hist_ids = set()
    for mod in modules:
        if mod.history_domains & hist_domains:
            hist_ids.add(mod.id)

    kw_ids, kw_matched = keyword_module_hits(description, modules)
    sticky = active_sticky_domains(character)
    sticky_ids = set()
    for mod in modules:
        if sticky & set(mod.domains):
            sticky_ids.add(mod.id)
        # Also match sticky keys stored as module ids directly.
        if mod.id in sticky:
            sticky_ids.add(mod.id)

    return {
        "live": sorted(live_ids),
        "history_domains": sorted(hist_domains),
        "history_modules": sorted(hist_ids),
        "keywords": kw_matched,
        "keyword_modules": sorted(kw_ids),
        "sticky": sorted(sticky),
        "sticky_modules": sorted(sticky_ids),
        "errors_present": bool(errors_present),
    }


def select_modules(signals, *, modules=None):
    """Return ordered module ids to attach from resolved signals."""
    modules = modules if modules is not None else _modules
    chosen = set()
    for key in (
        "live", "history_modules", "keyword_modules", "sticky_modules",
    ):
        for mid in signals.get(key) or ():
            chosen.add(mid)
    if signals.get("errors_present"):
        # Safe widen on real command crashes.
        for mid in ("combat", "needs_lifestyle"):
            if mid in _modules_by_id:
                chosen.add(mid)

    # Cross-fire: if A is chosen, also pull B.
    changed = True
    while changed:
        changed = False
        for mid in list(chosen):
            mod = _modules_by_id.get(mid)
            if mod is None:
                continue
            for other in mod.cross_fire:
                if other in _modules_by_id and other not in chosen:
                    chosen.add(other)
                    changed = True

    # Preserve registration order for stable JSON.
    return [m.id for m in modules if m.id in chosen]


def build_gameplay_modules(character, game, *, history=None, description=None):
    """Run selected module snapshots; return (payload, plan).

    ``payload`` is merged module dicts (no core fields).
    ``plan`` is the ``_context_plan`` metadata block.
    """
    signals = resolve_signals(
        character, game, history=history, description=description,
    )
    selected = select_modules(signals)
    all_ids = [m.id for m in _modules]
    omitted = [mid for mid in all_ids if mid not in selected]

    payload = {}
    context_errors = []
    domains_to_stick = set()

    for mid in selected:
        mod = _modules_by_id.get(mid)
        if mod is None or mod.snapshot is None:
            continue
        try:
            piece = mod.snapshot(character, game)
        except Exception as exc:
            context_errors.append(f"{mid}: {exc!r}")
            continue
        if not piece:
            continue
        if not isinstance(piece, dict):
            context_errors.append(f"{mid}: non-dict snapshot")
            continue
        # Nested context_errors from helpers.
        nested_errs = piece.pop("context_errors", None)
        if nested_errs:
            context_errors.extend(nested_errs)
        for key, value in piece.items():
            if value is None or value == "":
                continue
            # Later modules do not clobber earlier keys unless new.
            if key not in payload:
                payload[key] = value
        domains_to_stick.update(mod.domains)
        domains_to_stick.add(mod.id)

    if domains_to_stick:
        note_domains(character, domains_to_stick)

    plan = {
        "modules": selected,
        "omitted": omitted,
        "signals": signals,
    }
    if context_errors:
        payload["context_errors"] = context_errors
    return payload, plan
