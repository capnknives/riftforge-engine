"""
engine/content_kinds/engine.py -- generic kind-profile template engine.

Loads machine-readable profiles from directories registered via
``engine.hooks.set_content_kinds_dirs``, merges ``extends`` chains, and
exposes blank / apply / validate / lint helpers used by CLI tools, Area
Studio, and in-game OLC. Game-specific domain validation and catalog
persistence register through hooks (see docs/plans/two_repo_purity.md).
"""

from __future__ import annotations

import copy
import json
import os
import re

from engine import hooks

_PROFILES: dict[str, dict] | None = None

# Policy: ROOM NAME should be City - Main - Sub (authoring; not boot-hard).
_ROOM_TITLE_RE = re.compile(
    r"^[^-]+ - [^-]+ - .+$",
)


class KindValidationError(ValueError):
    """Raised when a kind profile or entity fails hard validation."""

    def __init__(self, kind_id, message, *, missing=None):
        self.kind_id = kind_id
        self.missing = missing or []
        super().__init__(message)


class LintWarning:
    """Non-fatal authoring policy warning."""

    __slots__ = ("policy_id", "message")

    def __init__(self, policy_id, message):
        self.policy_id = policy_id
        self.message = message


def kinds_dir():
    """Primary kind-profile directory (first registered path, or empty)."""
    dirs = hooks.content_kinds_dirs()
    return dirs[0] if dirs else ""


def default_engine_kinds_dir():
    """``engine/content/kinds/`` next to this package -- generic profiles.

    Every ``set_content_kinds_dirs([...])`` call site should register this
    alongside its own game-specific dir (order doesn't matter -- profiles
    merge by ``id`` across every registered dir, and ``extends`` resolves
    against the merged set regardless of which dir a parent physically
    lives in). One helper here means the path convention is defined once,
    not re-derived with ``os.path.join`` at every call site.
    """
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "content",
        "kinds",
    )


def _load_profiles():
    """Read every *.json profile under registered dirs (cached)."""
    global _PROFILES
    if _PROFILES is not None:
        return _PROFILES
    profiles = {}
    for kinds_root in hooks.content_kinds_dirs():
        if not os.path.isdir(kinds_root):
            continue
        for name in sorted(os.listdir(kinds_root)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(kinds_root, name)
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            kind_id = data.get("id")
            if not kind_id:
                raise KindValidationError(
                    name,
                    f"{path}: kind profile missing 'id'",
                )
            profiles[kind_id] = data
    _PROFILES = profiles
    return profiles


def _clear_profiles_for_tests():
    """Reset profile cache (smoke / unit helpers)."""
    global _PROFILES
    _PROFILES = None


def get_profile(kind_id):
    """Return the raw profile dict for kind_id (fail if unknown)."""
    profiles = _load_profiles()
    profile = profiles.get(kind_id)
    if profile is None:
        known = ", ".join(sorted(profiles)) or "(none)"
        raise KindValidationError(
            kind_id,
            f"Unknown kind {kind_id!r}. Known kinds: {known}",
        )
    return profile


def is_abstract(kind_id):
    """True when the profile is a non-instantiable parent (extends only)."""
    return bool(get_profile(kind_id).get("abstract"))


def list_kinds(*, include_abstract=False):
    """Sorted list of registered kind ids.

    Abstract parent profiles (``abstract: true``) are hidden from OLC
    pickers and ``content_new list`` unless ``include_abstract=True``.
    """
    profiles = _load_profiles()
    out = []
    for kind_id in sorted(profiles):
        if not include_abstract and profiles[kind_id].get("abstract"):
            continue
        out.append(kind_id)
    return out


def _merged_fields(kind_id, *, _seen=None):
    """Resolve extends chain into one fields dict (child overrides parent)."""
    if _seen is None:
        _seen = set()
    if kind_id in _seen:
        raise KindValidationError(
            kind_id,
            f"Circular extends chain involving {kind_id!r}",
        )
    _seen.add(kind_id)
    profile = get_profile(kind_id)
    parent_id = profile.get("extends")
    fields = {}
    if parent_id:
        fields = _merged_fields(parent_id, _seen=_seen)
    for name, spec in (profile.get("fields") or {}).items():
        fields[name] = copy.deepcopy(spec)
    return fields


def _field_names(kind_id):
    return _merged_fields(kind_id)


def blank(kind_id):
    """Return a dict with every declared field set to its default."""
    if is_abstract(kind_id):
        raise KindValidationError(
            kind_id,
            f"Kind {kind_id!r} is abstract — pick a concrete leaf kind.",
        )
    fields = _field_names(kind_id)
    out = {}
    for name, spec in fields.items():
        if "default" in spec:
            out[name] = copy.deepcopy(spec["default"])
        elif spec.get("required"):
            # Required without default — caller must supply before save.
            continue
    return out


def _coerce_value(raw, spec, *, field_name):
    """Parse a CLI/OLC string into the typed Python value for one field."""
    ftype = spec.get("type", "string")
    if raw is None:
        return None
    if ftype == "string":
        text = str(raw).strip()
        if not text and spec.get("required"):
            raise ValueError(f"{field_name}: must be a non-empty string")
        return text
    if ftype == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(
            f"{field_name}: bool must be true/false/yes/no/on/off"
        )
    if ftype == "int":
        if isinstance(raw, bool):
            raise ValueError(f"{field_name}: expected int, got bool")
        if isinstance(raw, int):
            return raw
        return int(str(raw).strip())
    if ftype == "float":
        if isinstance(raw, bool):
            raise ValueError(f"{field_name}: expected number, got bool")
        if isinstance(raw, (int, float)):
            return float(raw)
        return float(str(raw).strip())
    if ftype == "enum":
        text = str(raw).strip()
        allowed = spec.get("values") or []
        if text not in allowed:
            raise ValueError(
                f"{field_name}: must be one of {allowed}, got {text!r}"
            )
        return text
    if ftype == "list":
        if isinstance(raw, list):
            return copy.deepcopy(raw)
        text = str(raw).strip()
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError(f"{field_name}: list value must be a JSON array")
            return parsed
        if not text:
            return []
        return [part.strip() for part in text.split(",") if part.strip()]
    if ftype == "object":
        if isinstance(raw, dict):
            return copy.deepcopy(raw)
        text = str(raw).strip()
        if not text:
            return copy.deepcopy(spec.get("default", {}))
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError(f"{field_name}: object value must be a JSON object")
        return parsed
    raise ValueError(f"{field_name}: unknown field type {ftype!r}")


def apply_template(kind_id, overrides=None, *, base=None, reject_unknown=False):
    """Build a complete entity dict: defaults + typed overrides (+ optional base)."""
    if base is not None:
        out = copy.deepcopy(base)
    else:
        out = blank(kind_id)
    overrides = overrides or {}
    fields = _field_names(kind_id)
    for name, raw in overrides.items():
        if name not in fields:
            if reject_unknown:
                raise KindValidationError(
                    kind_id,
                    f"undeclared field {name!r} (not on kind {kind_id} profile).",
                )
            out[name] = copy.deepcopy(raw)
            continue
        out[name] = _coerce_value(raw, fields[name], field_name=name)
    return out


def normalize_kind(kind_id, obj):
    """Fill missing kind-profile defaults without overwriting existing values.

    Legacy catalogs often omit boolean slots the boot loader treats as
    optional; kind profiles declare them required with defaults. Migration
    and lint use this to align on-disk JSON with the shared schema.
    """
    if not isinstance(obj, dict):
        raise KindValidationError(
            kind_id,
            f"normalize_kind expected dict, got {type(obj).__name__}",
        )
    out = copy.deepcopy(obj)
    for name, value in blank(kind_id).items():
        if name not in out:
            out[name] = copy.deepcopy(value)
    return out


def diff_missing(kind_id, obj):
    """Return required field names still absent or empty on obj."""
    fields = _field_names(kind_id)
    missing = []
    for name, spec in fields.items():
        if not spec.get("required"):
            continue
        if name not in obj:
            missing.append(name)
            continue
        value = obj[name]
        ftype = spec.get("type", "string")
        if ftype == "string" and (not isinstance(value, str) or not value.strip()):
            missing.append(name)
        elif ftype == "list" and value is None:
            missing.append(name)
        elif ftype == "object" and value is None:
            missing.append(name)
    return missing


def explain_kind(kind_id):
    """Human-readable checklist for menus, help, and AI prompts."""
    profile = get_profile(kind_id)
    fields = _field_names(kind_id)
    lines = [
        f"Kind: {kind_id}",
        f"Label: {profile.get('label', kind_id)}",
        f"Writes: {profile.get('writes_to', '?')}",
        "",
        "Fields:",
    ]
    for name, spec in fields.items():
        req = "required" if spec.get("required") else "optional"
        default = spec.get("default", "—")
        doc = spec.get("doc", "")
        ftype = spec.get("type", "string")
        extra = ""
        if ftype == "enum":
            extra = f" values={spec.get('values')}"
        lines.append(
            f"  {name} ({ftype}, {req}, default={default!r}){extra}"
        )
        if doc:
            lines.append(f"    {doc}")
    policies = profile.get("policies") or []
    if policies:
        lines.append("")
        lines.append("Policies (lint warnings unless --strict):")
        for pol in policies:
            lines.append(f"  - {pol.get('id')}: {pol.get('doc', '')}")
    return "\n".join(lines)


def unknown_field_keys(kind_id, obj):
    """Return keys on obj that are not declared on this kind (merged fields)."""
    if not isinstance(obj, dict):
        return []
    allowed = set(_field_names(kind_id))
    return sorted(
        key for key in obj
        if key not in allowed and not str(key).startswith("_")
    )


def reject_unknown_fields(kind_id, obj, where=None):
    """Raise when obj carries keys not declared on the kind profile."""
    where = where or kind_id
    extra = unknown_field_keys(kind_id, obj)
    if extra:
        raise KindValidationError(
            kind_id,
            f"{where}: undeclared field(s): {', '.join(extra)}",
        )


def lint_kind(kind_id, obj, *, warn_unknown=False):
    """Return policy warnings (ROOM NAME shape, short descriptions, …)."""
    warnings = []
    profile = get_profile(kind_id)
    if warn_unknown:
        for key in unknown_field_keys(kind_id, obj):
            warnings.append(LintWarning(
                "unknown_field",
                f"undeclared field {key!r} (not on kind {kind_id} profile).",
            ))
    for pol in profile.get("policies") or []:
        pid = pol.get("id", "policy")
        if pid == "room_title_shape":
            title = obj.get("title") or obj.get("key") or ""
            if title and not _ROOM_TITLE_RE.match(str(title).strip()):
                warnings.append(LintWarning(
                    pid,
                    "ROOM NAME should be City - Main - Sub "
                    f"(got {title!r}; see help build-maps).",
                ))
        if pid == "description_length":
            desc = obj.get("description") or ""
            min_sent = pol.get("min_sentences", 2)
            # Rough sentence count: periods/question/exclamation splits.
            parts = [
                p.strip()
                for p in re.split(r"[.!?]+", str(desc))
                if p.strip()
            ]
            if len(parts) < min_sent:
                warnings.append(LintWarning(
                    pid,
                    f"description looks short ({len(parts)} sentence(s); "
                    f"aim for {min_sent}+ on public rooms).",
                ))
        if pid == "porch_shell":
            if not obj.get("outdoor"):
                warnings.append(LintWarning(
                    pid,
                    "porch rooms should set outdoor=true.",
                ))
            if not obj.get("private_home"):
                warnings.append(LintWarning(
                    pid,
                    "porch rooms should set private_home=true.",
                ))
        if pid == "living_shell":
            if not obj.get("is_house"):
                warnings.append(LintWarning(
                    pid,
                    "living hub rooms should set is_house=true.",
                ))
            if obj.get("outdoor"):
                warnings.append(LintWarning(
                    pid,
                    "living hub rooms should be indoor (outdoor=false).",
                ))
    return warnings


def validate_kind(kind_id, obj, *, strict=False, reject_unknown=False, where=None):
    """Hard-validate obj against kind profile + optional domain hook.

    Raises KindValidationError on failure. Returns obj (possibly normalized)
    on success. When strict=True, policy lint warnings also fail.
    When reject_unknown=True, undeclared keys also fail (new-row authoring).
    """
    where = where or kind_id
    if not isinstance(obj, dict):
        raise KindValidationError(
            kind_id,
            f"{where}: expected dict, got {type(obj).__name__}",
        )
    if reject_unknown:
        reject_unknown_fields(kind_id, obj, where=where)
    missing = diff_missing(kind_id, obj)
    if missing:
        raise KindValidationError(
            kind_id,
            f"{where}: missing required fields: {', '.join(missing)}",
            missing=missing,
        )
    # Type-shape checks from profile metadata.
    fields = _field_names(kind_id)
    for name, spec in fields.items():
        if name not in obj:
            continue
        value = obj[name]
        ftype = spec.get("type", "string")
        try:
            obj[name] = _coerce_value(value, spec, field_name=name)
        except ValueError as err:
            raise KindValidationError(kind_id, f"{where}: {err}") from err
    warns = lint_kind(kind_id, obj)
    if strict and warns:
        msgs = "; ".join(w.message for w in warns)
        raise KindValidationError(
            kind_id,
            f"{where}: policy lint failed (--strict): {msgs}",
        )
    hooks.content_kind_domain_validate(kind_id, obj, where=where)
    return obj
