"""
identity.py -- generic legal / forged ID shapes and verification.

Stdlib only; games supply policy overlays via ``engine.hooks``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from engine import hooks as hooks_mod


@dataclass
class LegalId:
    number: str
    legal_name: str
    home_zone: str
    issued_tick: int
    appearance_hash: str
    status: str = "valid"


@dataclass
class ForgedId:
    forge_tier: int
    role_id: str | None
    forged_name: str
    forged_home: str
    appearance_hash: str | None = None
    expires_tick: int | None = None
    burned: bool = False


@dataclass
class VerifyResult:
    ok: bool
    pierced: bool = False
    reason: str | None = None


_APPEARANCE_SLOTS = ("height", "physique", "hair", "eyes", "skin")


def appearance_hash(character) -> str:
    """Stable hash of body slots (not clothing) for ID photo checks."""
    appearance = getattr(character, "appearance", None) or {}
    parts = []
    for slot in _APPEARANCE_SLOTS:
        parts.append(f"{slot}={appearance.get(slot, '')}")
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _doc_tier(doc: Any) -> int:
    if doc is None:
        return 0
    if isinstance(doc, ForgedId):
        return int(doc.forge_tier or 0)
    if isinstance(doc, dict):
        return int(doc.get("forge_tier", 0) or 0)
    return 0


def _doc_appearance_hash(doc: Any) -> str | None:
    if doc is None:
        return None
    if isinstance(doc, (LegalId, ForgedId)):
        return doc.appearance_hash
    if isinstance(doc, dict):
        raw = doc.get("appearance_hash")
        return str(raw) if raw else None
    return None


def verify_id(viewer, subject, doc, *, context: str = "local", game=None) -> VerifyResult:
    """Mechanical ID check for arrest desks and federal rooms.

    ``context`` is ``local`` or ``federal_db``. Games may override via hook.
    """
    overlay = hooks_mod.identity_verify_hook(
        subject, doc, context=context, game=game,
    )
    if overlay is not None:
        return overlay
    if doc is None:
        return VerifyResult(ok=False, reason="no_document")
    if isinstance(doc, dict) and doc.get("burned"):
        return VerifyResult(ok=False, pierced=True, reason="burned")
    if isinstance(doc, ForgedId) and doc.burned:
        return VerifyResult(ok=False, pierced=True, reason="burned")
    known = getattr(viewer, "known_faces", None) or set()
    subj_key = getattr(subject, "key", None)
    if subj_key and subj_key in known:
        return VerifyResult(ok=False, pierced=True, reason="known_face")
    tier = _doc_tier(doc)
    if context == "federal_db":
        if tier < 4:
            return VerifyResult(ok=False, pierced=True, reason="tier_too_low")
        live_hash = appearance_hash(subject)
        doc_hash = _doc_appearance_hash(doc)
        if doc_hash and doc_hash != live_hash:
            return VerifyResult(ok=False, pierced=True, reason="photo_mismatch")
        return VerifyResult(ok=True)
    # Local beat cop: tier 1+ passes; tier 0 legal IDs pass when valid.
    if isinstance(doc, dict) and doc.get("status") not in (None, "valid"):
        return VerifyResult(ok=False, reason="invalid_status")
    if tier >= 1 or isinstance(doc, (LegalId, dict)):
        return VerifyResult(ok=True)
    return VerifyResult(ok=False, reason="insufficient_tier")
