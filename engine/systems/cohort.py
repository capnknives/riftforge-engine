"""
cohort.py -- generic social cohort records (engine layer).

A cohort is a persistent player-formed group (vampire nest, future shifter
pack, ghost haunt) with a roster, roles, optional lair room key, and
member_kind per seat (player vs npc).

Game-specific kind profiles and prose live in SUPERS JSON catalogs;
this module validates shapes and manipulates roster data without
importing any game package.

Design SoT: docs/plans/vampire_player_nest.md
"""

from __future__ import annotations

import re
import uuid

# member_kind values
MEMBER_PLAYER = "player"
MEMBER_NPC = "npc"
MEMBER_KINDS = frozenset({MEMBER_PLAYER, MEMBER_NPC})

_COHORT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def new_cohort_id(prefix="cohort"):
    """Return a short unique cohort id safe for meta JSON keys."""
    token = uuid.uuid4().hex[:10]
    return f"{prefix}-{token}"


def empty_cohort_index():
    """Return ``{cohort_id: record}`` for ``game.cohorts``."""
    return {}


def validate_member_entry(entry, *, where="member"):
    """Raise AssertionError when a roster row is malformed."""
    if not isinstance(entry, dict):
        raise AssertionError(f"{where}: member must be an object")
    key = (entry.get("key") or "").strip()
    if not key:
        raise AssertionError(f"{where}: member.key required")
    role = (entry.get("role") or "").strip()
    if not role:
        raise AssertionError(f"{where}: member.role required")
    kind = (entry.get("member_kind") or MEMBER_PLAYER).strip().lower()
    if kind not in MEMBER_KINDS:
        raise AssertionError(f"{where}: invalid member_kind {kind!r}")
    return True


def validate_cohort_record(record, *, where="cohort"):
    """Raise AssertionError when a cohort blob fails structural checks."""
    if not isinstance(record, dict):
        raise AssertionError(f"{where}: cohort must be an object")
    cid = (record.get("cohort_id") or "").strip()
    if not cid or not _COHORT_ID_RE.match(cid):
        raise AssertionError(f"{where}: invalid cohort_id {cid!r}")
    kind = (record.get("kind") or "").strip()
    if not kind:
        raise AssertionError(f"{where}: kind required")
    name = (record.get("display_name") or "").strip()
    if not name:
        raise AssertionError(f"{where}: display_name required")
    members = record.get("members")
    if not isinstance(members, list):
        raise AssertionError(f"{where}: members must be a list")
    for idx, row in enumerate(members):
        validate_member_entry(row, where=f"{where}.members[{idx}]")
    patriarch = (record.get("patriarch_key") or "").strip()
    if patriarch and not member_keys(record):
        raise AssertionError(f"{where}: patriarch_key set but members empty")
    return True


def member_keys(record):
    """Return ordered character keys in the cohort roster."""
    if not isinstance(record, dict):
        return []
    out = []
    for row in record.get("members") or []:
        if isinstance(row, dict):
            key = (row.get("key") or "").strip()
            if key:
                out.append(key)
    return out


def find_member(record, character_key):
    """Return the member dict for ``character_key``, or None."""
    if not character_key or not isinstance(record, dict):
        return None
    needle = str(character_key).strip()
    for row in record.get("members") or []:
        if isinstance(row, dict) and (row.get("key") or "").strip() == needle:
            return row
    return None


def count_members(record, *, member_kind=None):
    """Count roster rows, optionally filtered by member_kind."""
    n = 0
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        if member_kind is None:
            n += 1
            continue
        kind = (row.get("member_kind") or MEMBER_PLAYER).strip().lower()
        if kind == member_kind:
            n += 1
    return n


def role_of(record, character_key):
    """Return role string for ``character_key``, or None."""
    row = find_member(record, character_key)
    if row is None:
        return None
    return (row.get("role") or "").strip() or None


def patriarch_key(record):
    """Return explicit patriarch_key or first patriarch-role member."""
    if not isinstance(record, dict):
        return None
    explicit = (record.get("patriarch_key") or "").strip()
    if explicit:
        return explicit
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("role") or "").strip().lower() == "patriarch":
            return (row.get("key") or "").strip() or None
    return None


def oldest_member_key(record, *, exclude_keys=None):
    """Return key of the member with lowest ``joined_tick`` (tie: list order).

    Used for patriarch succession until a richer age model ships.
    """
    exclude = set(exclude_keys or ())
    best_key = None
    best_tick = None
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        key = (row.get("key") or "").strip()
        if not key or key in exclude:
            continue
        tick = int(row.get("joined_tick", 0) or 0)
        if best_tick is None or tick < best_tick:
            best_tick = tick
            best_key = key
    return best_key


def oldest_player_member_key(record, *, exclude_keys=None):
    """Return oldest **player** member key — skips ``member_kind: npc``.

    Locked 2026-08-21: NPC fledglings never inherit patriarch authority
    (docs/plans/npc_helpers.md).
    """
    exclude = set(exclude_keys or ())
    best_key = None
    best_tick = None
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        kind = (row.get("member_kind") or MEMBER_PLAYER).strip().lower()
        if kind != MEMBER_PLAYER:
            continue
        key = (row.get("key") or "").strip()
        if not key or key in exclude:
            continue
        tick = int(row.get("joined_tick", 0) or 0)
        if best_tick is None or tick < best_tick:
            best_tick = tick
            best_key = key
    return best_key


def add_member(record, *, key, role, member_kind=MEMBER_PLAYER, joined_tick=0,
               sire_key=None, meta=None):
    """Append a member row. Returns (ok, message). Does not check caps."""
    if find_member(record, key):
        return False, "Already in this cohort."
    row = {
        "key": key,
        "role": role,
        "member_kind": member_kind,
        "joined_tick": int(joined_tick or 0),
    }
    if sire_key:
        row["sire_key"] = sire_key
    if meta:
        row["meta"] = dict(meta)
    record.setdefault("members", []).append(row)
    return True, "Member added."


def remove_member(record, character_key):
    """Drop ``character_key`` from roster. Returns True when removed."""
    if not isinstance(record, dict):
        return False
    key = (character_key or "").strip()
    if not key:
        return False
    before = list(record.get("members") or [])
    after = [
        row for row in before
        if not (isinstance(row, dict) and (row.get("key") or "").strip() == key)
    ]
    if len(after) == len(before):
        return False
    record["members"] = after
    return True


def set_patriarch(record, character_key):
    """Promote ``character_key`` to patriarch role; demote prior patriarch."""
    key = (character_key or "").strip()
    if not key:
        return False
    found = False
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        row_key = (row.get("key") or "").strip()
        if row_key == key:
            row["role"] = "patriarch"
            found = True
        elif (row.get("role") or "").strip().lower() == "patriarch":
            row["role"] = "fledgling"
    if found:
        record["patriarch_key"] = key
    return found


# Command-bus actions (wave #3). Game kind JSON supplies role_specs.
ACTION_ORDER = "order"
ACTION_INSTRUCT = "instruct"
ACTION_READ = "read"


def cohort_may(record, actor_key, action, target_key=None, *, role_specs=None):
    """True when ``actor_key`` may perform ``action`` in this cohort.

    ``role_specs`` is the kind's ``roles`` object (can_order / can_instruct /
    cannot_target_roles). The engine does not import game catalogs — the
    caller passes the dict. Missing flags default to: patriarch and mate
    may order/instruct; mate cannot target patriarch or other mates;
    anyone on the roster may read (roster / where / report).
    """
    if record is None or not actor_key:
        return False
    actor_role = (role_of(record, actor_key) or "").strip().lower()
    if not actor_role:
        return False
    specs = role_specs if isinstance(role_specs, dict) else {}
    spec = specs.get(actor_role) if isinstance(specs.get(actor_role), dict) else {}
    action = (action or "").strip().lower()

    if action == ACTION_READ:
        return True

    if action not in (ACTION_ORDER, ACTION_INSTRUCT):
        return False

    flag = "can_order" if action == ACTION_ORDER else "can_instruct"
    if flag in spec:
        allowed = bool(spec.get(flag))
    else:
        allowed = actor_role in ("patriarch", "mate")
    if not allowed:
        return False
    if not target_key:
        return True
    if str(target_key).strip() == str(actor_key).strip():
        return False
    if find_member(record, target_key) is None:
        return False
    target_role = (role_of(record, target_key) or "").strip().lower()
    blocked = spec.get("cannot_target_roles")
    if blocked is None and actor_role == "mate":
        blocked = ["patriarch", "mate"]
    blocked_set = {
        str(item).strip().lower()
        for item in (blocked or [])
        if str(item).strip()
    }
    if target_role in blocked_set:
        return False
    return True


def pack_cohort_index(index):
    """Return JSON-safe dict for meta persistence."""
    if not isinstance(index, dict):
        return {}
    out = {}
    for cid, record in index.items():
        if not isinstance(record, dict):
            continue
        validate_cohort_record(record, where=f"cohorts.{cid}")
        out[str(cid)] = dict(record)
    return out


def unpack_cohort_index(blob):
    """Load meta blob onto a fresh index dict."""
    out = empty_cohort_index()
    if not isinstance(blob, dict):
        return out
    for cid, record in blob.items():
        if not isinstance(record, dict):
            continue
        validate_cohort_record(record, where=f"cohorts.{cid}")
        out[str(cid)] = dict(record)
    return out
