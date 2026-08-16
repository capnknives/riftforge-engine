"""Local PR number → in-game bug/suggestion ids (no GitHub API on live).

When a squash-merge subject ends with ``(#NNNN)`` but omits ``Fix bug #N``,
auto-deploy falls back to PR title/body via GitHub. Live often lacks API auth
or hits rate limits. Agents record ticket ids in ``ops/deploy_pr_ticket_map.json``
when opening PRs (``tools/open_pr.py``); deploy + boot reconcile read that file
first.

Map keys are GitHub PR numbers (strings). Values hold bug_ids, suggestion_ids,
and an optional short summary for announce copy.
"""

from __future__ import annotations

import json
import os
import time

_MAP_BASENAME = "deploy_pr_ticket_map.json"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def map_path(root: str | None = None) -> str:
    """Absolute path to the committed ticket map under ``ops/``."""
    base = root or _repo_root()
    return os.path.join(base, "ops", _MAP_BASENAME)


def load(root: str | None = None) -> dict[str, dict]:
    """Return ``{pr_number_str: row_dict}``; empty when missing or invalid."""
    path = map_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    rows = data.get("pulls") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    return rows


def save(rows: dict[str, dict], root: str | None = None) -> None:
    """Write the map atomically (pretty JSON for human review in PRs)."""
    path = map_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "_doc": (
            "GitHub PR → in-game bug_reports.log / suggestions.log ids. "
            "Written by tools/open_pr.py; read by auto_deploy PR fallback."
        ),
        "pulls": rows,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def lookup(pr_number: int, root: str | None = None) -> dict | None:
    """Return a row dict for ``pr_number``, or None."""
    row = load(root).get(str(int(pr_number)))
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
) -> None:
    """Upsert one PR row (idempotent — safe to call again with same data)."""
    key = str(int(pr_number))
    rows = load(root)
    row = dict(rows.get(key) or {})
    if bug_ids:
        row["bug_ids"] = sorted({int(x) for x in bug_ids})
    if suggestion_ids:
        row["suggestion_ids"] = sorted({int(x) for x in suggestion_ids})
    if summary:
        row["summary"] = summary.strip()
    if squash_subject:
        row["squash_subject"] = squash_subject.strip()
    row["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows[key] = row
    save(rows, root)
