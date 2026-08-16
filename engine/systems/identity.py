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
    forged_address: str | None = None
    portrait_desc: str | None = None
    expires_tick: int | None = None
    burned: bool = False


@dataclass
class VerifyResult:
    ok: bool
    pierced: bool = False
    reason: str | None = None


# Core body slots used for federal photo-hash checks (not clothing).
from engine.systems.appearance import CORE_SLOTS as _APPEARANCE_SLOTS

REGISTRATION_ADDRESS_MAX = 80

ZONE_COUNTY_LABEL = {
    "lebanon": "Lebanon, KS",
    "lawrence": "Lawrence, KS",
}


def normalize_registration_address(text) -> str | None:
    """Clean a player-typed registration / mailing address for IDs."""
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if len(cleaned) > REGISTRATION_ADDRESS_MAX:
        return None
    low = cleaned.lower()
    if low in (
        "transient",
        "none",
        "no address",
        "no fixed address",
        "no fixed home",
    ):
        return "Transient (no fixed address)"
    return cleaned


def zone_county_label(home_zone: str | None) -> str:
    """Player-facing county line for Kansas IDs."""
    zone = str(home_zone or "lebanon").strip().lower()
    return ZONE_COUNTY_LABEL.get(zone, "Kansas")


def appearance_hash(character) -> str:
    """Stable hash of body slots (not clothing) for ID photo checks."""
    appearance = getattr(character, "appearance", None) or {}
    parts = []
    for slot in _APPEARANCE_SLOTS:
        parts.append(f"{slot}={appearance.get(slot, '')}")
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def format_id_card_lines(
    *,
    card_kind: str,
    portrait_desc: str | None,
    name_line: str | None,
    address_line: str | None,
    id_number: str | None,
    tier: int | None = None,
    screenreader: bool = False,
    card_title: str | None = None,
) -> list[str]:
    """Immersive state ID layout for look / showid / wallet examine.

    ``name_line`` is omitted when the viewer has not learned the holder's
    name (legal IDs). Forged cards always print the forged name on the doc.
    """
    if card_title:
        title = card_title
    elif card_kind == "forged":
        title = "Forged Kansas ID"
        if tier is not None and int(tier) > 0:
            title = f"Tier {int(tier)} forged Kansas ID"
    else:
        title = "Kansas driver's license"

    portrait = (portrait_desc or "").strip() or "a blurred laminate portrait"
    addr = (address_line or "").strip() or "Address not listed"
    number = (id_number or "").strip()

    if screenreader:
        lines = [f"[ID] {title}"]
        lines.append(f"[PHOTO] {portrait}")
        if name_line:
            lines.append(f"[NAME] {name_line}")
        lines.append(f"[ADDRESS] {addr}")
        if number:
            lines.append(f"[ID#] {number}")
        if tier is not None and int(tier) > 0 and card_kind == "forged":
            lines.append(f"[TIER] {int(tier)}")
        return lines

    from engine import style

    lines = [style.paint("gold", title)]
    lines.append(f"Photo: {portrait}")
    if name_line:
        lines.append(f"Name: {name_line}")
    lines.append(f"Address: {addr}")
    if number:
        lines.append(f"ID#: {number}")
    if tier is not None and int(tier) > 0 and card_kind == "forged":
        lines.append(f"Seal: tier {int(tier)}")
    return lines


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
    if hooks_mod.identity_pierce_supernatural(viewer, subject):
        return VerifyResult(ok=False, pierced=True, reason="supernatural_pierce")
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
