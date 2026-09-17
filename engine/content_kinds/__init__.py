"""Generic kind-profile template engine (engine layer)."""

from engine.content_kinds.audit import (
    AuditSection,
    clear_for_tests as clear_audits_for_tests,
    format_report,
    list_sections,
    register as register_audit,
    run_all as run_audits,
)
from engine.content_kinds.engine import (
    KindValidationError,
    LintWarning,
    apply_template,
    blank,
    diff_missing,
    explain_kind,
    kinds_dir,
    lint_kind,
    list_kinds,
    normalize_kind,
    resolve_kind_id,
    validate_kind,
    _clear_profiles_for_tests,
)

__all__ = [
    "KindValidationError",
    "LintWarning",
    "apply_template",
    "blank",
    "diff_missing",
    "explain_kind",
    "kinds_dir",
    "lint_kind",
    "list_kinds",
    "normalize_kind",
    "resolve_kind_id",
    "validate_kind",
    "_clear_profiles_for_tests",
    "AuditSection",
    "clear_audits_for_tests",
    "format_report",
    "list_sections",
    "register_audit",
    "run_audits",
]
