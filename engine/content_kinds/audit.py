"""
content_kinds/audit.py -- generic builder audit registry (engine layer).

Games register domain-specific hygiene runners at boot (jobs, quests, …).
Offline tools and ``gm content audit`` call ``run_all`` — read-only reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


AuditRunner = Callable[..., list[str]]


@dataclass(frozen=True)
class AuditSection:
    """One registered builder audit domain."""

    domain_id: str
    title: str
    runner: AuditRunner


_sections: list[AuditSection] = []


def register(domain_id: str, title: str, runner: AuditRunner) -> None:
    """Register a builder audit runner (idempotent replace by domain_id)."""
    global _sections
    cleaned = []
    for section in _sections:
        if section.domain_id != domain_id:
            cleaned.append(section)
    cleaned.append(AuditSection(domain_id, title, runner))
    _sections = cleaned


def list_sections() -> list[AuditSection]:
    """Return registered audit sections in registration order."""
    return list(_sections)


def run_all(*, game=None) -> list[tuple[str, str, list[str]]]:
    """Run every registered audit; return (domain_id, title, lines) tuples."""
    results = []
    for section in _sections:
        lines = section.runner(game=game)
        if lines is None:
            lines = []
        results.append((section.domain_id, section.title, list(lines)))
    return results


def format_report(sections, *, prefix="") -> str:
    """Format audit sections for telnet or CLI output."""
    chunks = []
    for domain_id, title, lines in sections:
        header = f"{prefix}{title} ({domain_id})"
        if not lines:
            chunks.append(f"{header}: no issues found")
            continue
        chunks.append(header + ":")
        for line in lines:
            chunks.append(f"  {line}")
    if not chunks:
        return f"{prefix}Builder audit: no domains registered"
    return "\n".join(chunks)


def clear_for_tests() -> None:
    """Reset registry (unit/smoke tests only)."""
    global _sections
    _sections = []
