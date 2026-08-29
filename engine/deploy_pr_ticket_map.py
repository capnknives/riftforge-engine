"""Local PR number → in-game bug/suggestion ids (no GitHub API on live).

When a squash-merge subject ends with ``(#NNNN)`` but omits ``Fix bug #N``,
auto-deploy falls back to PR title/body via GitHub. Live often lacks API auth
or hits rate limits. Agents record ticket ids when opening PRs
(``tools/open_pr.py``) under ``ops/deploy_tickets/<pr>.json`` — one file per
PR so parallel bug-fix branches do not fight over a shared JSON tail. Legacy
rows in ``ops/deploy_pr_ticket_map.json`` are still read.

Map keys are GitHub PR numbers (strings). Values hold bug_ids, suggestion_ids,
and an optional short summary for announce copy.
"""

from __future__ import annotations

import json
import os
import time

_MAP_BASENAME = "deploy_pr_ticket_map.json"
_TICKETS_DIRNAME = "deploy_tickets"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def map_path(root: str | None = None) -> str:
    """Absolute path to the legacy monolithic ticket map under ``ops/``."""
    base = root or _repo_root()
    return os.path.join(base, "ops", _MAP_BASENAME)


def tickets_dir(root: str | None = None) -> str:
    """Directory holding one JSON row per GitHub PR number."""
    base = root or _repo_root()
    return os.path.join(base, "ops", _TICKETS_DIRNAME)


def ticket_path(pr_number: int, root: str | None = None) -> str:
    """Absolute path to ``ops/deploy_tickets/<pr>.json``."""
    return os.path.join(tickets_dir(root), f"{int(pr_number)}.json")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_monolithic(root: str | None = None) -> dict[str, dict]:
    """Rows from the legacy shared ``deploy_pr_ticket_map.json`` file."""
    data = _read_json(map_path(root))
    if not data:
        return {}
    rows = data.get("pulls")
    if not isinstance(rows, dict):
        return {}
    return {str(k): dict(v) for k, v in rows.items() if isinstance(v, dict)}


def _load_ticket_files(root: str | None = None) -> dict[str, dict]:
    """Rows from ``ops/deploy_tickets/<pr>.json`` (parallel-PR safe)."""
    directory = tickets_dir(root)
    if not os.path.isdir(directory):
        return {}
    rows: dict[str, dict] = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        key = name[:-5]
        if not key.isdigit():
            continue
        row = _read_json(os.path.join(directory, name))
        if row:
            rows[key] = row
    return rows


def load(root: str | None = None) -> dict[str, dict]:
    """Return ``{pr_number_str: row_dict}`` from legacy map + per-PR files."""
    rows = _load_monolithic(root)
    rows.update(_load_ticket_files(root))
    return rows


def save(rows: dict[str, dict], root: str | None = None) -> None:
    """Write the legacy monolithic map (tests / one-off migrations only).

    New PR rows should use ``record_pull`` → ``ops/deploy_tickets/<pr>.json``.
    """
    path = map_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "_doc": (
            "GitHub PR → in-game bug_reports.log / suggestions.log ids. "
            "Legacy monolithic map; new PRs use ops/deploy_tickets/<pr>.json."
        ),
        "pulls": rows,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def save_ticket_row(pr_number: int, row: dict, root: str | None = None) -> str:
    """Write one PR row to ``ops/deploy_tickets/<pr>.json``; return rel path."""
    directory = tickets_dir(root)
    os.makedirs(directory, exist_ok=True)
    path = ticket_path(pr_number, root=root)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    rel = os.path.relpath(path, root or _repo_root()).replace("\\", "/")
    return rel


def lookup(pr_number: int, root: str | None = None) -> dict | None:
    """Return a row dict for ``pr_number``, or None."""
    key = str(int(pr_number))
    row = _read_json(ticket_path(pr_number, root=root))
    if row:
        return row
    row = _load_monolithic(root).get(key)
    return row if isinstance(row, dict) else None


def synthetic_pr_text(pr_number: int, root: str | None = None) -> str:
    """Build parseable PR title/body text from the local map (no network)."""
    row = lookup(pr_number, root)
    if not row:
        return ""
    summary = (row.get("summary") or "player-facing fix").strip()
    lines: list[str] = []
    bug_ids = [int(x) for x in (row.get("bug_ids") or []) if str(x).isdigit()]
    suggest_ids = [
        int(x) for x in (row.get("suggestion_ids") or []) if str(x).isdigit()
    ]
    if len(bug_ids) == 1:
        lines.append(f"Fix in-game bug {bug_ids[0]}: {summary}")
        lines.append(f"Fixes bug report {bug_ids[0]}: {summary}")
    elif bug_ids:
        joined = ", ".join(str(n) for n in bug_ids)
        lines.append(f"Fix in-game bugs {joined}: {summary}")
        lines.append(f"Fixes bug reports {joined}: {summary}")
    if len(suggest_ids) == 1:
        lines.append(f"Ship suggestion #{suggest_ids[0]}: {summary}")
        lines.append(f"Ships suggestion report {suggest_ids[0]}: {summary}")
    elif suggest_ids:
        joined = ", ".join(str(n) for n in suggest_ids)
        lines.append(f"Ship suggestions {joined}: {summary}")
        lines.append(f"Ships suggestion reports {joined}: {summary}")
    return "\n".join(lines).strip()


def record_pull(
    pr_number: int,
    *,
    bug_ids: list[int] | None = None,
    suggestion_ids: list[int] | None = None,
    summary: str = "",
    squash_subject: str = "",
    root: str | None = None,
) -> str:
    """Upsert one PR row (idempotent). Returns the committed relative path."""
    key = str(int(pr_number))
    row = dict(lookup(pr_number, root=root) or {})
    if bug_ids:
        row["bug_ids"] = sorted({int(x) for x in bug_ids})
    if suggestion_ids:
        row["suggestion_ids"] = sorted({int(x) for x in suggestion_ids})
    if summary:
        row["summary"] = summary.strip()
    if squash_subject:
        row["squash_subject"] = squash_subject.strip()
    row["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return save_ticket_row(pr_number, row, root=root)
