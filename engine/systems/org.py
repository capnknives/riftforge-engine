"""
org.py -- generic charter records for Chapters and Factions (engine layer).

A Charter is a persistent player-formed org cell (Tier-1 Chapter or Tier-2
Faction) with a roster, leader/successor keys, optional patron ref, and
relation edges to other charters.

Game-specific archetype catalogs and verbs live in SUPERS; this module
validates shapes and manipulates roster data without importing any game
package.

Design SoT: docs/plans/org_faction_hierarchy.md
Build plan: docs/plans/org_faction_hierarchy_build_plan.md (Phase 1)
"""

from __future__ import annotations

import re
import uuid

from engine.systems import cohort as cohort_engine

# Re-export member_kind constants so callers can import from one place.
MEMBER_PLAYER = cohort_engine.MEMBER_PLAYER
MEMBER_NPC = cohort_engine.MEMBER_NPC
MEMBER_KINDS = cohort_engine.MEMBER_KINDS

TIER_CHAPTER = "chapter"
TIER_FACTION = "faction"
CHARTER_TIERS = frozenset({TIER_CHAPTER, TIER_FACTION})

PVP_OPEN = "open"
PVP_DEFEND = "defend"
PVP_CLOSED = "closed"
PVP_STANCES = frozenset({PVP_OPEN, PVP_DEFEND, PVP_CLOSED})
DEFAULT_PVP_STANCE = PVP_DEFEND

DEFAULT_LEADER_ROLE = "leader"

# Relation edge kinds between charters. Values are dicts with a required ``kind``
# key (not bare strings) so Phase 4 can attach notice-window timestamps, Defy
# flags, and other metadata without reshaping stored records.
RELATION_KNEEL_VASSAL = "kneel_vassal_of"
RELATION_BROKEN_BY = "broken_by"
RELATION_ALLIED_WITH = "allied_with"
RELATION_ALLIANCE_PENDING = "alliance_pending"
RELATION_KINDS = frozenset({
    RELATION_KNEEL_VASSAL,
    RELATION_BROKEN_BY,
    RELATION_ALLIED_WITH,
    RELATION_ALLIANCE_PENDING,
})

PENDING_CONFLICT_KINDS = frozenset({"kneel", "break"})

# Chapter/Faction patron dedication lifecycle.
# Wave 2 expands beyond player God — see docs/plans/host_grace_grid_build.md.
PATRON_KIND_GOD = "god"
PATRON_KIND_HEAVEN = "heaven"
PATRON_KIND_HELL = "hell"
PATRON_KIND_PANTHEON = "pantheon"
PATRON_KIND_AMARA = "amara"
PATRON_KINDS = frozenset({
    PATRON_KIND_GOD,
    PATRON_KIND_HEAVEN,
    PATRON_KIND_HELL,
    PATRON_KIND_PANTHEON,
    PATRON_KIND_AMARA,
})
PATRON_STATUS_PENDING = "pending"
PATRON_STATUS_DEDICATED = "dedicated"
PATRON_STATUS_REFUSED = "refused"
PATRON_STATUSES = frozenset({
    PATRON_STATUS_PENDING,
    PATRON_STATUS_DEDICATED,
    PATRON_STATUS_REFUSED,
})

_CHARTER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def new_charter_id(prefix="charter"):
    """Return a short unique charter id safe for meta JSON keys."""
    # uuid4().hex gives 32 hex chars; slice [:10] keeps keys compact in meta.
    token = uuid.uuid4().hex[:10]
    return f"{prefix}-{token}"


def empty_charter_index():
    """Return ``{charter_id: record}`` for ``game.charters``."""
    return {}


def validate_member_entry(entry, *, where="member"):
    """Raise AssertionError when a roster row is malformed.

    Member rows share the cohort roster shape — delegate to cohort engine.
    """
    return cohort_engine.validate_member_entry(entry, where=where)


def validate_charter_record(record, *, where="charter"):
    """Raise AssertionError when a charter blob fails structural checks."""
    if not isinstance(record, dict):
        raise AssertionError(f"{where}: charter must be an object")

    cid = (record.get("charter_id") or "").strip()
    if not cid or not _CHARTER_ID_RE.match(cid):
        raise AssertionError(f"{where}: invalid charter_id {cid!r}")

    tier = (record.get("tier") or "").strip().lower()
    if tier not in CHARTER_TIERS:
        raise AssertionError(f"{where}: invalid tier {tier!r}")

    archetype = (record.get("archetype") or "").strip()
    if not archetype:
        raise AssertionError(f"{where}: archetype required")

    name = (record.get("display_name") or "").strip()
    if not name:
        raise AssertionError(f"{where}: display_name required")

    flavor = (record.get("flavor_noun") or "").strip()
    if not flavor:
        raise AssertionError(f"{where}: flavor_noun required")

    members = record.get("members")
    if not isinstance(members, list):
        raise AssertionError(f"{where}: members must be a list")
    for idx, row in enumerate(members):
        validate_member_entry(row, where=f"{where}.members[{idx}]")

    leader_key = (record.get("leader_key") or "").strip()
    if leader_key and find_member(record, leader_key) is None:
        raise AssertionError(
            f"{where}: leader_key {leader_key!r} is not on the roster"
        )

    successor_key = (record.get("successor_key") or "").strip()
    if successor_key and find_member(record, successor_key) is None:
        raise AssertionError(
            f"{where}: successor_key {successor_key!r} is not on the roster"
        )

    successor_visible = record.get("successor_visible", True)
    if not isinstance(successor_visible, bool):
        raise AssertionError(f"{where}: successor_visible must be a bool")

    patron_kind = record.get("patron_kind", "")
    if patron_kind is not None and not isinstance(patron_kind, str):
        raise AssertionError(f"{where}: patron_kind must be a string")

    patron_ref = record.get("patron_ref", "")
    if patron_ref is not None and not isinstance(patron_ref, str):
        raise AssertionError(f"{where}: patron_ref must be a string")

    patron_status = record.get("patron_status")
    if patron_status is None or patron_status == "":
        patron_status = None
    elif patron_status not in PATRON_STATUSES:
        raise AssertionError(
            f"{where}: invalid patron_status {patron_status!r}"
        )
    if patron_status is not None:
        pk = (patron_kind or "").strip()
        pr = (patron_ref or "").strip()
        if not pk:
            raise AssertionError(
                f"{where}: patron_kind required when patron_status is set"
            )
        if not pr:
            raise AssertionError(
                f"{where}: patron_ref required when patron_status is set"
            )

    balance = record.get("currency_balance", 0)
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        raise AssertionError(f"{where}: currency_balance must be int >= 0")

    pvp_stance = (record.get("pvp_stance") or DEFAULT_PVP_STANCE).strip().lower()
    if pvp_stance not in PVP_STANCES:
        raise AssertionError(f"{where}: invalid pvp_stance {pvp_stance!r}")

    for tick_field in (
        "founded_tick",
        "protection_until_tick",
        "break_cooldown_until_tick",
    ):
        tick_val = record.get(tick_field, 0)
        if isinstance(tick_val, bool) or not isinstance(tick_val, int):
            raise AssertionError(f"{where}: {tick_field} must be an int")

    relations = record.get("relations", {})
    if not isinstance(relations, dict):
        raise AssertionError(f"{where}: relations must be an object")
    for rel_id, rel_blob in relations.items():
        if not isinstance(rel_id, str) or not rel_id.strip():
            raise AssertionError(f"{where}: relations keys must be non-empty strings")
        if not isinstance(rel_blob, dict):
            raise AssertionError(
                f"{where}: relations[{rel_id!r}] must be an object"
            )
        rel_kind = rel_blob.get("kind")
        if rel_kind not in RELATION_KINDS:
            raise AssertionError(
                f"{where}: relations[{rel_id!r}] kind {rel_kind!r} is invalid"
            )

    chapter_ids = record.get("member_chapter_ids", [])
    if not isinstance(chapter_ids, list):
        raise AssertionError(f"{where}: member_chapter_ids must be a list")
    for idx, chap_id in enumerate(chapter_ids):
        if not isinstance(chap_id, str) or not chap_id.strip():
            raise AssertionError(
                f"{where}: member_chapter_ids[{idx}] must be a non-empty string"
            )
    if tier == TIER_CHAPTER and chapter_ids:
        raise AssertionError(
            f"{where}: chapter charters cannot list member_chapter_ids"
        )

    parent_faction_id = record.get("parent_faction_id")
    if parent_faction_id is None:
        parent_faction_id = ""
    if not isinstance(parent_faction_id, str):
        raise AssertionError(f"{where}: parent_faction_id must be a string")
    if tier == TIER_FACTION and parent_faction_id.strip():
        raise AssertionError(
            f"{where}: faction charters cannot set parent_faction_id"
        )

    pending_conflict = record.get("pending_conflict")
    if pending_conflict is not None:
        if not isinstance(pending_conflict, dict):
            raise AssertionError(f"{where}: pending_conflict must be an object or null")
        pkind = pending_conflict.get("kind")
        if pkind not in PENDING_CONFLICT_KINDS:
            raise AssertionError(
                f"{where}: pending_conflict kind {pkind!r} is invalid"
            )
        attacker_id = (pending_conflict.get("attacker_charter_id") or "").strip()
        if not attacker_id:
            raise AssertionError(
                f"{where}: pending_conflict.attacker_charter_id required"
            )
        for tick_field in ("declared_tick", "resolve_tick"):
            tick_val = pending_conflict.get(tick_field, 0)
            if isinstance(tick_val, bool) or not isinstance(tick_val, int):
                raise AssertionError(
                    f"{where}: pending_conflict.{tick_field} must be an int"
                )
        defied = pending_conflict.get("defied", False)
        if not isinstance(defied, bool):
            raise AssertionError(f"{where}: pending_conflict.defied must be a bool")

    leader_role = record.get("leader_role")
    if leader_role is not None:
        if not isinstance(leader_role, str) or not leader_role.strip():
            raise AssertionError(f"{where}: leader_role must be a non-empty string")

    return True


def member_keys(record):
    """Return ordered character keys on the charter roster."""
    return cohort_engine.member_keys(record)


def find_member(record, character_key):
    """Return the member dict for ``character_key``, or None."""
    return cohort_engine.find_member(record, character_key)


def count_members(record, *, member_kind=None):
    """Count roster rows, optionally filtered by member_kind."""
    return cohort_engine.count_members(record, member_kind=member_kind)


def role_of(record, character_key):
    """Return role string for ``character_key``, or None."""
    return cohort_engine.role_of(record, character_key)


def add_member(record, *, key, role, member_kind=MEMBER_PLAYER, joined_tick=0,
               sire_key=None, meta=None):
    """Append a member row. Returns (ok, message). Does not check caps."""
    ok, msg = cohort_engine.add_member(
        record,
        key=key,
        role=role,
        member_kind=member_kind,
        joined_tick=joined_tick,
        sire_key=sire_key,
        meta=meta,
    )
    if not ok and msg == "Already in this cohort.":
        return False, "Already in this charter."
    return ok, msg


def remove_member(record, character_key):
    """Drop ``character_key`` from roster. Returns True when removed.

    Charter validation requires ``leader_key`` and ``successor_key`` (when set)
    to reference roster members. When removal succeeds, dangling keys are
    cleared to ``""`` — auto-promotion or successor naming is a separate
    explicit operation (``transfer_to_successor``, ``set_successor``).
    """
    key = (character_key or "").strip()
    if not key:
        return False
    removed = cohort_engine.remove_member(record, key)
    if not removed:
        return False
    if (record.get("successor_key") or "").strip() == key:
        record["successor_key"] = ""
    if (record.get("leader_key") or "").strip() == key:
        record["leader_key"] = ""
    return True


def leader_key_of(record):
    """Return explicit ``leader_key`` or first member with the leader role."""
    if not isinstance(record, dict):
        return None
    explicit = (record.get("leader_key") or "").strip()
    if explicit:
        return explicit
    leader_role = (record.get("leader_role") or DEFAULT_LEADER_ROLE).strip().lower()
    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("role") or "").strip().lower() == leader_role:
            return (row.get("key") or "").strip() or None
    return None


def successor_key_of(record):
    """Return designated ``successor_key`` or None when unset."""
    if not isinstance(record, dict):
        return None
    key = (record.get("successor_key") or "").strip()
    return key or None


def set_successor(record, character_key, *, visible=True):
    """Set or clear the designated successor. Returns True on success."""
    if not isinstance(record, dict):
        return False
    key = (character_key or "").strip()
    if not key:
        record["successor_key"] = ""
        record["successor_visible"] = bool(visible)
        return True
    if find_member(record, key) is None:
        return False
    record["successor_key"] = key
    record["successor_visible"] = bool(visible)
    return True


def transfer_to_successor(record):
    """Promote ``successor_key`` to leader; demote the prior leader.

    Returns True when a successor was promoted, False when none applied.
    """
    if not isinstance(record, dict):
        return False
    successor = successor_key_of(record)
    if not successor or find_member(record, successor) is None:
        return False

    leader_role = (record.get("leader_role") or DEFAULT_LEADER_ROLE).strip()
    leader_role_lower = leader_role.lower()

    for row in record.get("members") or []:
        if not isinstance(row, dict):
            continue
        row_key = (row.get("key") or "").strip()
        if row_key == successor:
            row["role"] = leader_role
        elif (row.get("role") or "").strip().lower() == leader_role_lower:
            # Generic demotion — charter archetypes may refine role names later.
            row["role"] = "member"

    record["leader_key"] = successor
    record["successor_key"] = ""
    return True


def is_chapter(record):
    """True when ``record`` is a Tier-1 Chapter charter."""
    if not isinstance(record, dict):
        return False
    return (record.get("tier") or "").strip().lower() == TIER_CHAPTER


def is_faction(record):
    """True when ``record`` is a Tier-2 Faction charter."""
    if not isinstance(record, dict):
        return False
    return (record.get("tier") or "").strip().lower() == TIER_FACTION


def add_chapter_to_faction(record, chapter_charter_id):
    """Append a chapter charter id to a Faction. Returns True on success."""
    if not is_faction(record):
        return False
    chap_id = (chapter_charter_id or "").strip()
    if not chap_id:
        return False
    ids = record.setdefault("member_chapter_ids", [])
    if chap_id in ids:
        return False
    ids.append(chap_id)
    return True


def remove_chapter_from_faction(record, chapter_charter_id):
    """Remove a chapter charter id from a Faction. Returns True when removed."""
    if not is_faction(record):
        return False
    chap_id = (chapter_charter_id or "").strip()
    if not chap_id:
        return False
    before = list(record.get("member_chapter_ids") or [])
    after = [item for item in before if item != chap_id]
    if len(after) == len(before):
        return False
    record["member_chapter_ids"] = after
    return True


def pack_charter_index(index):
    """Return JSON-safe dict for meta persistence."""
    if not isinstance(index, dict):
        return {}
    out = {}
    for cid, record in index.items():
        if not isinstance(record, dict):
            continue
        try:
            validate_charter_record(record, where=f"charters.{cid}")
        except AssertionError as exc:
            print(f"[org] skipped invalid charter {cid}: {exc}")
            continue
        out[str(cid)] = dict(record)
    return out


def unpack_charter_index(blob):
    """Load meta blob onto a fresh index dict."""
    out = empty_charter_index()
    if not isinstance(blob, dict):
        return out
    for cid, record in blob.items():
        if not isinstance(record, dict):
            continue
        try:
            validate_charter_record(record, where=f"charters.{cid}")
        except AssertionError as exc:
            print(f"[org] skipped invalid charter {cid}: {exc}")
            continue
        out[str(cid)] = dict(record)
    return out
