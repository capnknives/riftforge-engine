"""
deploy_queue.py -- deferred origin/main tree syncs while build lock is on.

Records commits auto-deploy saw advance but could not ``reset --hard`` yet.
Staff run ``gm deploy sync`` (or ``gm buildlock off confirm``) to queue one
batched catch-up when ready. No auto-sync when ``who`` is empty — explicit
staff action only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

PENDING_NAME = ".auto_deploy_pending.json"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pending_path(root=None):
    return os.path.join(root or _repo_root(), PENDING_NAME)


def _load(root=None):
    path = pending_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    if not isinstance(data, dict):
        return {"entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {"entries": []}
    return data


def _save(root, data):
    path = pending_path(root)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def _commit_subject(sha, root):
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%s", sha],
            cwd=root,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return "(unknown subject)"


def _commits_behind(from_sha, to_sha, root):
    """Count commits on origin/main tip not reachable from synced SHA."""
    if not from_sha or not to_sha or from_sha == to_sha:
        return 0
    try:
        out = subprocess.check_output(
            ["git", "rev-list", "--count", f"{from_sha}..{to_sha}"],
            cwd=root,
            text=True,
        ).strip()
        return max(0, int(out))
    except (subprocess.CalledProcessError, ValueError):
        return 0


def record_blocked_advance(root, *, from_sha, to_sha, subject=None):
    """Remember a tree sync we deferred (idempotent on tip SHA)."""
    if not to_sha or from_sha == to_sha:
        return
    if subject is None:
        subject = _commit_subject(to_sha, root)
    data = _load(root)
    entries = data.setdefault("entries", [])
    for row in entries:
        if isinstance(row, dict) and row.get("tip_sha") == to_sha:
            row["subject"] = subject
            row["from_sha"] = from_sha
            row["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _save(root, data)
            return
    entries.append({
        "from_sha": from_sha,
        "tip_sha": to_sha,
        "subject": subject,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _save(root, data)


def clear_pending(root=None):
    """Drop the pending queue after a successful tree sync."""
    path = pending_path(root)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def has_pending(root=None):
    return bool((_load(root).get("entries") or []))


def latest_pending(root=None):
    """Newest blocked advance entry, or None."""
    entries = _load(root).get("entries") or []
    if not entries:
        return None
    row = entries[-1]
    return row if isinstance(row, dict) else None


def status_lines(root=None, *, synced_sha=None, remote_sha=None):
    """Human-readable pending deploy summary."""
    entries = _load(root).get("entries") or []
    if not entries and synced_sha and remote_sha and synced_sha != remote_sha:
        behind = _commits_behind(synced_sha, remote_sha, root)
        if behind:
            subject = _commit_subject(remote_sha, root)
            return [
                f"Deploy queue: {behind} commit(s) behind origin/main",
                f"  tip: {remote_sha[:12]} — {subject}",
                "  run: gm deploy sync  (or  gm buildlock off confirm)",
            ]
    if not entries:
        return ["Deploy queue: empty"]
    lines = [f"Deploy queue: {len(entries)} blocked advance(s)"]
    for row in entries[-5:]:
        tip = (row.get("tip_sha") or "")[:12]
        subj = row.get("subject") or "(unknown)"
        lines.append(f"  {tip} — {subj}")
    if len(entries) > 5:
        lines.append(f"  … and {len(entries) - 5} more")
    lines.append("  run: gm deploy sync  (or  gm buildlock off confirm)")
    return lines
