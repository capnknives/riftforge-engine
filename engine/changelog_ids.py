"""Stable changelog numbers — assigned on the game host after deploy, never in PRs.

Feature branches write timestamp-only ``CHANGELOG.d/<slug>.md`` files. A
single fill-only writer stamps ``#N``:

- ``Game()`` boot / copyover (fresh interpreter — the guaranteed mint)
- ``engine.auto_deploy`` after sync **and on idle polls** (live / staging).
  That path must ``importlib.reload`` ``changelog_index``; a stale watcher
  module is why newest ships stayed unnumbered in Aug 2026.
- maintainer ``tools/changelog_assign_ids.py --apply`` / local_ci
  ``changelog_ids`` apply path

That is what makes duplicate ids structurally impossible the way they
happened in Aug 2026: parallel PRs no longer mint against a stale counter,
and a GitHub PR number is never used as a changelog id. GitHub Actions is
**not** the writer — that job dies on billing before it starts.

Live ``changes`` and Discord **read** stamped ids after the deploy stamper
runs. Discord holds the post until every **player** bullet in the batch has
an id, then retries after copyover or the next idle poll so the footer can
say ``changes N``. Staff-only bullets (``[ops]`` / ``[docs]`` / ``[easter]``
and the same heuristics as ``changes ops``) use a **separate** ``#N``
sequence so player Discord numbers stay consecutive. Open those with
``changes ops N``.

Keep unique existing ids **within each sequence**. Duplicate groups:
earliest ``sort_ts`` keeps ``#N``; losers and never-stamped bullets get
that sequence's ``max+1`` in oldest-first order. List order is still ship
time, not id.
"""

from __future__ import annotations

import os
import re
from typing import NamedTuple

from engine.changelog_audience import summary_is_staff_only

# Bold Unreleased lead-in. Tags may sit before or after ``#N``; some legacy
# files put ``[ops]`` before the date. Canonical write is always
# ``#N DATE — [ops] summary`` so in-game ``changes`` can parse the id.
# Group 1 = prefix through ``**``
# Group 2 = tags before id
# Group 3 = existing id or None
# Group 4 = tags after id
# Group 5 = calendar date
# Group 6 = optional HH:MM:SS from T…Z
# Group 7 = dash (em/en/hyphen or mojibake em dash)
# Group 8 = remainder (summary…)
_BULLET_RE = re.compile(
    r"^(\s*-\s+\*\*)"
    r"((?:\[[^\]]+\]\s+)*)"
    r"(?:#(\d+)\s+)?"
    r"((?:\[[^\]]+\]\s+)*)"
    r"(\d{4}-\d{2}-\d{2})"
    r"(?:T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z)?"
    r"(\s+(?:[—–-]|ΓÇö)\s+)"
    r"(.*)$"
)


class BulletLoc(NamedTuple):
    """One Unreleased bullet on disk, ready to keep or restamp."""

    path: str
    line_index: int
    change_id: int
    sort_ts: str
    slug: str
    date: str
    staff_only: bool


def _repo_root_from_here() -> str:
    """Directory that contains CHANGELOG.md (parent of ``engine/``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fragment_slug(path: str) -> str:
    """``CHANGELOG.d/foo.md`` → ``foo``; empty for the monolith."""
    base = os.path.basename(path)
    if base.lower() == "changelog.md":
        return ""
    if base.lower().endswith(".md"):
        return base[:-3].lower()
    return ""


def sort_ts_for_date(date: str, time_hms: str | None) -> str:
    """UTC sort key matching ``engine.verbs.basic`` midnight fallback."""
    if not date:
        return "0001-01-01T00:00:00Z"
    if time_hms:
        return f"{date}T{time_hms}Z"
    return f"{date}T00:00:00Z"


def parse_bullet_line(line: str) -> tuple[int, str, str, str] | None:
    """Return ``(id, date, sort_ts, rest)`` or None when the line is not a stamp."""
    match = _BULLET_RE.match(line.rstrip("\n"))
    if not match:
        return None
    change_id = int(match.group(3)) if match.group(3) else 0
    date = match.group(5)
    sort_ts = sort_ts_for_date(date, match.group(6))
    return change_id, date, sort_ts, match.group(8)


def _staff_blob_from_match(match: re.Match[str]) -> str:
    """Tags + summary used to decide player vs ``changes ops`` sequence."""
    tags = f"{match.group(2) or ''}{match.group(4) or ''}"
    rest = match.group(8) or ""
    return f"{tags}{rest}".strip()


def bullet_line_is_staff_only(line: str) -> bool:
    """True when this changelog bullet belongs on ``changes ops``, not Discord."""
    match = _BULLET_RE.match(line.rstrip("\n"))
    if not match:
        return False
    return summary_is_staff_only(_staff_blob_from_match(match), "")


def format_stamped_line(line: str, new_id: int) -> str | None:
    """Rewrite one bullet to canonical ``#N DATE — summary``.

    Date-only bullets gain ``T00:00:00Z`` so a newly high id cannot fail the
    recent-fragment sort-stamp gate (ids >= 2280 must carry ``T…Z``). Audience
    tags that sat before the date move into the summary so ``changes`` parses
    the id and timestamp first.
    """
    match = _BULLET_RE.match(line.rstrip("\n"))
    if not match:
        return None
    tags = f"{match.group(2) or ''}{match.group(4) or ''}"
    date = match.group(5)
    time_hms = match.group(6)
    summary = (match.group(8) or "").lstrip()
    if time_hms:
        stamp = f"{date}T{time_hms}Z"
    else:
        stamp = f"{date}T00:00:00Z"
    tag_text = tags.strip()
    if tag_text and not summary.startswith(tag_text):
        summary = f"{tag_text} {summary}"
    newline = "\n" if line.endswith("\n") else ""
    return f"{match.group(1)}#{new_id} {stamp} — {summary}{newline}"


def _fragment_paths(root: str) -> list[str]:
    """Sorted ``CHANGELOG.d/*.md`` paths (skip README)."""
    frag_dir = os.path.join(root, "CHANGELOG.d")
    if not os.path.isdir(frag_dir):
        return []
    paths = []
    for name in sorted(os.listdir(frag_dir)):
        if not name.endswith(".md") or name.lower() == "readme.md":
            continue
        paths.append(os.path.join(frag_dir, name))
    return paths


def scan_bullets(root: str) -> list[BulletLoc]:
    """Every Unreleased bold bullet in fragments + ``CHANGELOG.md``."""
    found: list[BulletLoc] = []
    changelog_md = os.path.join(root, "CHANGELOG.md")
    if os.path.isfile(changelog_md):
        found.extend(_scan_md_unreleased(changelog_md))
    for path in _fragment_paths(root):
        found.extend(_scan_file(path, unreleased_only=False))
    return found


def _scan_file(path: str, *, unreleased_only: bool) -> list[BulletLoc]:
    """Parse matching bullets in *path*."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    in_unreleased = not unreleased_only
    out: list[BulletLoc] = []
    slug = fragment_slug(path)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if unreleased_only:
            if stripped.startswith("## [Unreleased]"):
                in_unreleased = True
                continue
            if in_unreleased and stripped.startswith("## "):
                break
            if not in_unreleased:
                continue
        parsed = parse_bullet_line(line)
        if parsed is None:
            continue
        change_id, date, sort_ts, _rest = parsed
        out.append(
            BulletLoc(
                path=path,
                line_index=idx,
                change_id=change_id,
                sort_ts=sort_ts,
                slug=slug,
                date=date,
                staff_only=bullet_line_is_staff_only(line),
            )
        )
    return out


def _scan_md_unreleased(path: str) -> list[BulletLoc]:
    """``CHANGELOG.md`` Unreleased section only."""
    return _scan_file(path, unreleased_only=True)


def _assign_one_namespace(bullets: list[BulletLoc]) -> dict[tuple[str, int], int]:
    """Fill-only ``#N`` plan for one sequence (player **or** ops, not both)."""
    if not bullets:
        return {}

    by_id: dict[int, list[BulletLoc]] = {}
    unstamped: list[BulletLoc] = []
    max_id = 0
    for bullet in bullets:
        if bullet.change_id:
            by_id.setdefault(bullet.change_id, []).append(bullet)
            max_id = max(max_id, bullet.change_id)
        else:
            unstamped.append(bullet)

    planned: dict[tuple[str, int], int] = {}
    losers: list[BulletLoc] = []
    for _old_id, group in by_id.items():
        if len(group) == 1:
            continue
        ordered = sorted(
            group, key=lambda b: (b.sort_ts, b.slug, b.path, b.line_index)
        )
        # Earliest ship keeps the colliding id; everyone else is restamped.
        losers.extend(ordered[1:])

    need = losers + unstamped
    need.sort(key=lambda b: (b.sort_ts, b.slug, b.path, b.line_index))
    next_id = max_id + 1
    for bullet in need:
        planned[(bullet.path, bullet.line_index)] = next_id
        next_id += 1
    return planned


def plan_assignments(bullets: list[BulletLoc]) -> dict[tuple[str, int], int]:
    """Map ``(path, line_index) → new_id`` for bullets that need a stamp change.

    Player-visible and staff-only bullets are **two sequences**. Discord and
    default ``changes`` use the player counter; ``changes ops N`` uses the
    staff counter. Unchanged keepers are omitted so ``--apply`` only rewrites
    losers and never-stamped rows.
    """
    if not bullets:
        return {}
    players = [b for b in bullets if not b.staff_only]
    staff = [b for b in bullets if b.staff_only]
    planned = _assign_one_namespace(players)
    planned.update(_assign_one_namespace(staff))
    return planned


def _renumber_one_namespace(bullets: list[BulletLoc]) -> dict[tuple[str, int], int]:
    """Oldest-first ``#1`` … ``#N`` for one sequence."""
    ordered = sorted(
        bullets,
        key=lambda b: (b.sort_ts, b.slug, b.path, b.line_index),
    )
    planned: dict[tuple[str, int], int] = {}
    for index, bullet in enumerate(ordered, start=1):
        planned[(bullet.path, bullet.line_index)] = index
    return planned


def plan_renumber_all(bullets: list[BulletLoc]) -> dict[tuple[str, int], int]:
    """Map every bullet to ``#1`` … ``#N`` oldest-first **per sequence**.

    Player and ops each restart at ``#1``. Do **not** run this on the
    ``main`` assign job — that would reshuffle ids on every merge. Feature
    PRs never call this.
    """
    players = [b for b in bullets if not b.staff_only]
    staff = [b for b in bullets if b.staff_only]
    planned = _renumber_one_namespace(players)
    planned.update(_renumber_one_namespace(staff))
    return planned


def apply_assignments(
    planned: dict[tuple[str, int], int],
    *,
    dry_run: bool = False,
) -> int:
    """Rewrite planned bullets. Return how many lines would change / changed."""
    if not planned:
        return 0
    by_path: dict[str, dict[int, int]] = {}
    for (path, line_index), new_id in planned.items():
        by_path.setdefault(path, {})[line_index] = new_id

    changed = 0
    for path, line_map in sorted(by_path.items()):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        dirty = False
        for line_index, new_id in line_map.items():
            if line_index < 0 or line_index >= len(lines):
                continue
            rewritten = format_stamped_line(lines[line_index], new_id)
            if rewritten is None or rewritten == lines[line_index]:
                continue
            lines[line_index] = rewritten
            dirty = True
            changed += 1
        if dirty and not dry_run:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.writelines(lines)
    return changed


def added_fragment_paths(root: str, *, base_ref: str = "origin/main") -> list[str]:
    """``CHANGELOG.d`` files **added** on this branch vs *base_ref* (not modified)."""
    import subprocess

    try:
        out = subprocess.check_output(
            [
                "git",
                "diff",
                "--diff-filter=A",
                "--name-only",
                f"{base_ref}...HEAD",
                "--",
                "CHANGELOG.d/",
            ],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    paths = []
    for rel in out.splitlines():
        rel = rel.strip().replace("\\", "/")
        if not rel.endswith(".md") or rel.lower().endswith("readme.md"):
            continue
        paths.append(os.path.join(root, rel.replace("/", os.sep)))
    return paths


def added_fragments_with_minted_ids(root: str, *, base_ref: str = "origin/main") -> list[str]:
    """New fragment files that already contain a ``#N`` stamp (agent mint)."""
    hits = []
    for path in added_fragment_paths(root, base_ref=base_ref):
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            parsed = parse_bullet_line(line)
            if parsed and parsed[0]:
                hits.append(path)
                break
    return hits


def assign_all(root: str | None = None, *, dry_run: bool = False) -> tuple[int, int]:
    """Scan, plan, apply. Return ``(planned_count, rewritten_count)``."""
    root = root or _repo_root_from_here()
    bullets = scan_bullets(root)
    planned = plan_assignments(bullets)
    rewritten = apply_assignments(planned, dry_run=dry_run)
    return len(planned), rewritten if not dry_run else len(planned)
