"""
kokid_notify.py -- Kokid announces fixer / implementer work on wiznet.

After GM ``squashbug`` / ``squashbugs`` or ``sendsuggest`` / ``squashsuggest``
POSTs to a Cursor webhook:

1. **Pickup** -- Kokid says he's working the bug(s) or suggestion(s) on wiznet.
2. **Watch** -- remember the id(s).
3. **PR ready** -- when an open (including **draft**), or recently merged,
   ``Fix bug #<id>: …`` / ``Ship suggestion #<id>: …`` PR appears, Kokid announces
   on wiznet. Cursor automations often leave the PR as draft until merge; skipping
   drafts was silencing the "I'm done" line.

``gm diaglog analyze`` uses the same pattern for lag dumps:

1. **Queued** -- Kokid wiznet with the Gist / hub link when the analyzer
   webhook POST succeeds.
2. **Watch** -- remember the pending lag run (``.kokid_lag_watch.json``).
3. **PR ready** -- when an open ``Fix lag: …`` PR appears, Kokid wiznet
   the PR link and clear the watch.

No separate GM-chat channel -- wiznet already is staff chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime


KOKID_NAME = "Kokid"

WATCH_NAME = ".kokid_pr_watch.json"
ANNOUNCED_NAME = ".kokid_pr_announced.json"
LAG_WATCH_NAME = ".kokid_lag_watch.json"

# Lag analyzer PR titles (see lag-diag-analyzer.INSTRUCTIONS.md).
_FIX_LAG_RE = re.compile(r"(?i)^fix\s+lag:\s*(.*)")

DEFAULT_POLL_SECONDS = 45
# While a watch is fresh (after squashbug), poll faster so draft/open PRs
# are not missed in the automation's short open window.
DEFAULT_BURST_POLL_SECONDS = 15
DEFAULT_BURST_WINDOW_SECONDS = 15 * 60
# Fixer PRs often squash-merge in under a minute; scan recently merged too.
DEFAULT_CLOSED_LOOKBACK_SECONDS = 6 * 3600
REPO_API_PULLS = (
    "https://api.github.com/repos/capnknives/RiftForge/pulls"
    "?state=open&per_page=30&sort=updated&direction=desc"
)
REPO_API_CLOSED_PULLS = (
    "https://api.github.com/repos/capnknives/RiftForge/pulls"
    "?state=closed&per_page=30&sort=updated&direction=desc"
)

# Staff / container logs: one poll summary line per cycle while ids are watched.
_POLL_LOG_PREFIX = "[kokid]"

_FIX_IN_TITLE_RE = re.compile(
    r"(?i)(?:fix(?:es|ed)?)\s+"
    r"(?:bugs?|bug_reports\.log)\s*#"
    r"(\d+)"
    r"(?:\s*[-–—]\s*#?(\d+))?"
    r"((?:\s*,\s*#?\d+)*)"
)


def _repo_root():
    """Checkout root (bind-mount in Docker)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _watch_path(root=None):
    return os.path.join(root or _repo_root(), WATCH_NAME)


def _announced_path(root=None):
    return os.path.join(root or _repo_root(), ANNOUNCED_NAME)


def _load_json(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def watch_bug_ids(bug_ids, *, root=None):
    """Remember bug ids waiting for an open Fix PR (Kokid wiznet announce)."""
    return _watch_ids("bugs", bug_ids, root=root)


def watch_suggestion_ids(suggestion_ids, *, root=None):
    """Remember suggestion ids waiting for an open Ship PR."""
    return _watch_ids("suggestions", suggestion_ids, root=root)


def _watch_ids(bucket, ids, *, root=None):
    """Add ids under ``bugs`` or ``suggestions`` in the watch file."""
    root = root or _repo_root()
    path = _watch_path(root)
    state = _load_json(path, {"bugs": {}, "suggestions": {}})
    bucket_map = state.setdefault(bucket, {})
    now = time.time()
    changed = False
    added = []
    for raw in ids or ():
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid <= 0:
            continue
        key = str(sid)
        if key not in bucket_map:
            bucket_map[key] = {"since": now}
            changed = True
            added.append(sid)
    if changed:
        _save_json(path, state)
        if added:
            label = "bug" if bucket == "bugs" else "suggestion"
            listed = format_id_list(added)
            print(
                f"{_POLL_LOG_PREFIX} watching {label} report(s) {listed} "
                f"(poll every {_poll_interval_seconds():.0f}s; "
                f"gm reports kokid probe for live GitHub scan)",
                flush=True,
            )
    return changed


def clear_watched_bug_ids(bug_ids, *, root=None):
    """Drop bug ids from the watch list after announce."""
    return _clear_watched_ids("bugs", bug_ids, root=root)


def clear_watched_suggestion_ids(suggestion_ids, *, root=None):
    """Drop suggestion ids from the watch list after announce."""
    return _clear_watched_ids("suggestions", suggestion_ids, root=root)


def _clear_watched_ids(bucket, ids, *, root=None):
    root = root or _repo_root()
    path = _watch_path(root)
    state = _load_json(path, {"bugs": {}, "suggestions": {}})
    bucket_map = state.get(bucket) or {}
    changed = False
    for raw in ids or ():
        key = str(int(raw))
        if key in bucket_map:
            del bucket_map[key]
            changed = True
    if not changed:
        return False
    if bucket_map:
        state[bucket] = bucket_map
        _save_json(path, state)
    else:
        state.pop(bucket, None)
        if state.get("bugs") or state.get("suggestions"):
            _save_json(path, state)
        else:
            try:
                os.remove(path)
            except OSError:
                _save_json(path, {"bugs": {}, "suggestions": {}})
    return True


def watched_bug_ids(*, root=None):
    """Return the set of bug ids currently awaiting a fixer PR."""
    return _watched_ids("bugs", root=root)


def watched_suggestion_ids(*, root=None):
    """Return the set of suggestion ids currently awaiting a Ship PR."""
    return _watched_ids("suggestions", root=root)


def _watched_ids(bucket, *, root=None):
    state = _load_json(_watch_path(root), {"bugs": {}, "suggestions": {}})
    out = set()
    for key in (state.get(bucket) or {}):
        try:
            out.add(int(key))
        except ValueError:
            continue
    return out


def _load_announced(*, root=None):
    data = _load_json(_announced_path(root), {"prs": []})
    out = set()
    for raw in data.get("prs") or []:
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _mark_announced(pr_number, *, root=None):
    root = root or _repo_root()
    path = _announced_path(root)
    have = _load_announced(root=root)
    have.add(int(pr_number))
    ordered = sorted(have)[-200:]
    _save_json(path, {"prs": ordered})


def suggestion_ids_from_pr_title(title):
    """Parse Ship suggestion #N ids from a PR title."""
    title = (title or "").strip()
    try:
        from engine.auto_deploy import parse_deploy_metadata

        _bugs, suggestion_ids, summary = parse_deploy_metadata(title)
        return list(suggestion_ids), summary
    except Exception:
        return [], title


def bug_ids_from_pr_title(title):
    """Parse Fix bug #N ids from a PR title (same spirit as auto_deploy)."""
    title = (title or "").strip()
    try:
        from engine.auto_deploy import parse_deploy_metadata

        bug_ids, _sug, summary = parse_deploy_metadata(title)
        return list(bug_ids), summary
    except Exception:
        pass
    match = _FIX_IN_TITLE_RE.search(title)
    if not match:
        return [], title
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    extra = match.group(3) or ""
    ids = [start]
    if end is not None:
        lo, hi = sorted((start, end))
        ids = list(range(lo, hi + 1))
    for piece in re.findall(r"#?(\d+)", extra):
        ids.append(int(piece))
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    rest = title[match.end():].lstrip(" :-—–")
    return seen, rest or title


def format_id_list(bug_ids):
    """Format ids as ``#102-105, #107, #110`` (ranges collapsed).

    Staff-facing wiznet voice -- hashes are fine in-game (not a GitHub
    PR body). Empty input → ``""``.
    """
    ids = sorted({int(x) for x in (bug_ids or []) if int(x) > 0})
    if not ids:
        return ""
    parts = []
    start = prev = ids[0]
    for n in ids[1:]:
        if n == prev + 1:
            prev = n
            continue
        if start == prev:
            parts.append(f"#{start}")
        else:
            parts.append(f"#{start}-{prev}")
        start = prev = n
    if start == prev:
        parts.append(f"#{start}")
    else:
        parts.append(f"#{start}-{prev}")
    return ", ".join(parts)


def format_pickup_message(bug_ids):
    """Kokid line when starting work after squashbug / squashbugs."""
    ids = sorted({int(x) for x in (bug_ids or []) if int(x) > 0})
    if not ids:
        return "I'm standing by for bug reports."
    if len(ids) == 1:
        return f"I'm getting to work on bug report #{ids[0]}."
    listed = format_id_list(ids)
    return f"I'm picking up a batch to diagnose bugs {listed}."


def format_suggestion_pickup_message(suggestion_ids):
    """Kokid line when starting work after sendsuggest / squashsuggest."""
    ids = sorted({int(x) for x in (suggestion_ids or []) if int(x) > 0})
    if not ids:
        return "I'm standing by for player suggestions."
    if len(ids) == 1:
        return f"I'm getting to work on suggestion report #{ids[0]}."
    listed = format_id_list(ids)
    return f"I'm picking up a batch of player suggestions {listed}."


def _lag_watch_path(root=None):
    return os.path.join(root or _repo_root(), LAG_WATCH_NAME)


def lag_diag_pending(*, root=None):
    """Return pending lag-diag watch dict, or None."""
    data = _load_json(_lag_watch_path(root), {})
    if not isinstance(data, dict) or not data.get("since"):
        return None
    return data


def mark_lag_diag_pending(
    gist_url, issue_url=None, reporter="?", *, root=None,
):
    """Remember a queued ``gm diaglog analyze`` run awaiting a Fix lag PR."""
    root = root or _repo_root()
    path = _lag_watch_path(root)
    payload = {
        "since": time.time(),
        "gist_url": (gist_url or "").strip(),
        "issue_url": (issue_url or "").strip(),
        "reporter": (reporter or "?").strip() or "?",
    }
    _save_json(path, payload)
    return True


def clear_lag_diag_pending(*, root=None):
    """Drop the lag-diag watch after Kokid announces the fix PR."""
    path = _lag_watch_path(root)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        _save_json(path, {})
    return True


def lag_reporter_wiznet_label(game, reporter):
    """Staff wiznet face for lag Kokid lines -- never ``gmspirit:`` / ``husk:`` keys.

    ``gm diaglog analyze`` passes ``Character.key`` (often ``gmspirit:Name``
    while ``gm on``). Kokid must read ``Name(GM)`` like bare ``wiznet``.
    """
    from engine.command_support import strip_ephemeral_storage_prefix
    from engine.gm_notify import wiznet_speaker_label
    from world import Character

    raw = (reporter or "?").strip()
    if not raw or raw == "?":
        return "?"

    char = None
    if game is not None:
        find = getattr(game, "find_character", None)
        if callable(find):
            char = find(raw)
        if char is None:
            roster = getattr(game, "characters", None) or []
            for body in roster:
                if getattr(body, "key", None) == raw:
                    char = body
                    break

    if isinstance(char, Character):
        return wiznet_speaker_label(char)

    peeled = strip_ephemeral_storage_prefix(raw)
    if raw.lower().startswith("gmspirit:") and peeled and peeled != "?":
        if peeled.endswith("(GM)"):
            return peeled
        return f"{peeled}(GM)"
    return peeled or raw


def format_lag_queued_message(gist_url, issue_url=None, reporter="?"):
    """Kokid line when ``gm diaglog analyze`` queues the Cursor webhook."""
    link = (gist_url or "").strip() or (issue_url or "").strip()
    who = (reporter or "?").strip()
    if who and who != "?":
        lead = f"{who} queued a lag dump — I'm sending it to the analyzer."
    else:
        lead = "A lag dump is queued — I'm sending it to the analyzer."
    if link:
        return f"{lead} Dump: {link} I'll wiznet when the Fix lag PR opens."
    return f"{lead} I'll wiznet when the Fix lag PR opens."


def format_lag_pr_ready_message(pr_number, pr_url=None, *, summary=""):
    """Kokid line when a ``Fix lag: …`` PR opens after a watched analyze."""
    summary = (summary or "").strip()
    if len(summary) > 80:
        summary = summary[:77] + "..."
    parts = [f"Lag fix ready for review — PR {int(pr_number)}"]
    if summary:
        parts.append(f"({summary})")
    if pr_url:
        parts.append(str(pr_url).strip())
    return " ".join(parts)


def lag_summary_from_pr_title(title):
    """Return summary text after ``Fix lag:`` in a PR title, or ''."""
    match = _FIX_LAG_RE.match((title or "").strip())
    if not match:
        return ""
    return (match.group(1) or "").strip()


def announce_lag_queued(game, gist_url, issue_url=None, reporter="?"):
    """Wiznet: Kokid queued a lag-diag analyzer run (+ watch for Fix lag PR)."""
    mark_lag_diag_pending(gist_url, issue_url, reporter)
    label = lag_reporter_wiznet_label(game, reporter)
    return announce_as_kokid(
        game,
        format_lag_queued_message(gist_url, issue_url, label),
    )


def announce_pickup(game, bug_ids):
    """Wiznet: Kokid is starting work on these bug report ids."""
    return announce_as_kokid(game, format_pickup_message(bug_ids))


def announce_suggestion_pickup(game, suggestion_ids):
    """Wiznet: Kokid is starting work on these suggestion report ids."""
    return announce_as_kokid(game, format_suggestion_pickup_message(suggestion_ids))


def format_pr_ready_message(bug_ids, pr_number, pr_url=None, *, summary="", merged=False):
    """Staff-facing Kokid line (prose bug ids — not bare GitHub # traps)."""
    ids = sorted({int(x) for x in (bug_ids or []) if int(x) > 0})
    if not ids:
        bug_bit = "A bug"
    elif len(ids) == 1:
        bug_bit = f"Bug report {ids[0]}"
    else:
        bug_bit = f"Bug reports {ids[0]}-{ids[-1]}"
    summary = (summary or "").strip()
    if len(summary) > 80:
        summary = summary[:77] + "..."
    if merged:
        parts = [f"{bug_bit} fix merged — PR {int(pr_number)}"]
    else:
        parts = [f"{bug_bit} fix is ready for review — PR {int(pr_number)}"]
    if summary:
        parts.append(f"({summary})")
    if pr_url:
        parts.append(str(pr_url).strip())
    return " ".join(parts)


def format_suggestion_pr_ready_message(
    suggestion_ids, pr_number, pr_url=None, *, summary="", merged=False,
):
    """Staff-facing Kokid line when a Ship suggestion PR opens."""
    ids = sorted({int(x) for x in (suggestion_ids or []) if int(x) > 0})
    if not ids:
        sug_bit = "A player suggestion"
    elif len(ids) == 1:
        sug_bit = f"Suggestion report {ids[0]}"
    else:
        sug_bit = f"Suggestion reports {ids[0]}-{ids[-1]}"
    summary = (summary or "").strip()
    if len(summary) > 80:
        summary = summary[:77] + "..."
    if merged:
        parts = [f"{sug_bit} shipped — PR {int(pr_number)}"]
    else:
        parts = [f"{sug_bit} is ready for review — PR {int(pr_number)}"]
    if summary:
        parts.append(f"({summary})")
    if pr_url:
        parts.append(str(pr_url).strip())
    return " ".join(parts)


def announce_as_kokid(game, message):
    """Speak on wiznet as Kokid to every online staff GM.

    Pass the plain name string (not a ``SimpleNamespace``). Wiznet labels
    go through ``_public_label``, which stringifies arbitrary objects --
    ``SimpleNamespace(key='Kokid')`` became ``namespace(key='Kokid')`` on
    the wire. Real GM speakers stay Character instances and are unchanged.
    """
    from engine import gm_notify

    text = (message or "").strip()
    if not text:
        return False
    return gm_notify.wiznet_broadcast(game, KOKID_NAME, text, exclude=None)


def _poll_interval_seconds():
    """Seconds between GitHub PR polls while ids are watched."""
    try:
        return float(os.environ.get("RIFTFORGE_KOKID_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    except (TypeError, ValueError):
        return float(DEFAULT_POLL_SECONDS)


def _burst_poll_seconds():
    """Faster interval while a watch is still in the post-pickup window."""
    try:
        return float(
            os.environ.get(
                "RIFTFORGE_KOKID_BURST_POLL_SECONDS",
                DEFAULT_BURST_POLL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return float(DEFAULT_BURST_POLL_SECONDS)


def _burst_window_seconds():
    """How long after a watch is queued to keep using the burst interval."""
    try:
        return float(
            os.environ.get(
                "RIFTFORGE_KOKID_BURST_WINDOW_SECONDS",
                DEFAULT_BURST_WINDOW_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return float(DEFAULT_BURST_WINDOW_SECONDS)


def _watch_is_in_burst_window(*, root=None):
    """True when any watched bug/suggestion was queued recently."""
    root = root or _repo_root()
    state = _load_json(_watch_path(root), {"bugs": {}, "suggestions": {}})
    window = max(60.0, _burst_window_seconds())
    now = time.time()
    for bucket in ("bugs", "suggestions"):
        for meta in (state.get(bucket) or {}).values():
            if not isinstance(meta, dict):
                continue
            try:
                since = float(meta.get("since") or 0)
            except (TypeError, ValueError):
                continue
            if since and (now - since) < window:
                return True
    return False


def _effective_poll_interval_seconds(*, root=None):
    """45s steady-state, or ~15s while a fresh squashbug watch is pending."""
    if _watch_is_in_burst_window(root=root):
        return max(10.0, _burst_poll_seconds())
    return max(15.0, _poll_interval_seconds())


def _closed_lookback_seconds():
    """How far back to scan squash-merged PRs (open-only polls miss fast merges)."""
    try:
        return float(
            os.environ.get(
                "RIFTFORGE_KOKID_CLOSED_LOOKBACK_SECONDS",
                DEFAULT_CLOSED_LOOKBACK_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return float(DEFAULT_CLOSED_LOOKBACK_SECONDS)


def _parse_github_iso(value):
    """Parse GitHub ``2026-07-30T04:07:51Z`` timestamps to epoch seconds."""
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _github_token_present():
    """True when Kokid can authenticate GitHub pulls API requests."""
    return bool(
        os.environ.get("RIFTFORGE_DIAG_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RiftForge-kokid-wiznet",
    }
    token = (
        os.environ.get("RIFTFORGE_DIAG_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fix_ship_titles_from_pulls(pulls):
    """Summarize open PRs whose titles parse as Fix-bug or Ship-suggestion."""
    fix_rows = []
    ship_rows = []
    for pull in pulls or ():
        title = pull.get("title") or ""
        pr_number = pull.get("number")
        bug_ids, _ = bug_ids_from_pr_title(title)
        suggestion_ids, _ = suggestion_ids_from_pr_title(title)
        if bug_ids:
            fix_rows.append((pr_number, bug_ids, title))
        if suggestion_ids:
            ship_rows.append((pr_number, suggestion_ids, title))
    return fix_rows, ship_rows


def _log_poll_summary(
    *,
    watched_bugs,
    watched_suggestions,
    pulls,
    announced_prs,
    found_pr_numbers,
):
    """One container-log line per poll so staff can trace missed wiznet announces."""
    open_nums = [p.get("number") for p in (pulls or ()) if p.get("number") is not None]
    fix_rows, ship_rows = _fix_ship_titles_from_pulls(pulls)
    parts = [
        f"{_POLL_LOG_PREFIX} poll:",
        f"token={'yes' if _github_token_present() else 'NO'}",
        f"poll_prs={len(pulls or ())}",
    ]
    if open_nums:
        preview = ",".join(str(n) for n in open_nums[:12])
        if len(open_nums) > 12:
            preview += ",…"
        parts.append(f"pr_nums={preview}")
    if watched_bugs:
        parts.append(f"watched_bugs={format_id_list(sorted(watched_bugs))}")
    if watched_suggestions:
        parts.append(
            f"watched_suggestions={format_id_list(sorted(watched_suggestions))}"
        )
    if fix_rows:
        bits = [f"#{n}->{ids}" for n, ids, _t in fix_rows[:6]]
        parts.append(f"fix_titles=[{'; '.join(bits)}]")
    merged_rows = [p for p in (pulls or ()) if (p.get("state") or "") == "merged"]
    if merged_rows:
        bits = [f"#{p['number']}" for p in merged_rows[:6]]
        parts.append(f"recent_merged=[{', '.join(bits)}]")
    if ship_rows:
        bits = [f"#{n}->{ids}" for n, ids, _t in ship_rows[:6]]
        parts.append(f"ship_titles=[{'; '.join(bits)}]")
    if found_pr_numbers:
        parts.append(f"announced={found_pr_numbers}")
    else:
        waiting = []
        if watched_bugs:
            waiting.append(f"bugs {format_id_list(sorted(watched_bugs))}")
        if watched_suggestions:
            waiting.append(
                f"suggestions {format_id_list(sorted(watched_suggestions))}"
            )
        parts.append(f"no_match_yet ({', '.join(waiting)})")
    print(" ".join(parts), flush=True)


def diagnostic_lines(*, game=None, root=None, probe_github=False):
    """Plain lines for ``gm reports kokid`` (no secrets).

    probe_github=True hits GitHub once (same as poll) and shows whether open
    PR titles would match the current watch list.
    """
    root = root or _repo_root()
    bugs = sorted(watched_bug_ids(root=root))
    sugs = sorted(watched_suggestion_ids(root=root))
    lines = [
        "Kokid PR watcher — wiznet when Fix/Ship PR opens:",
    ]
    if bugs:
        lines.append(f"  Watching bugs: {format_id_list(bugs)}")
    if sugs:
        lines.append(f"  Watching suggestions: {format_id_list(sugs)}")
    if not bugs and not sugs:
        lines.append("  Watching: (nothing — squashbug/squashsuggest to queue)")
    lines.append(
        f"  GitHub token: {'set' if _github_token_present() else 'MISSING (401)'}",
    )
    lines.append(
        f"  Poll interval: {_effective_poll_interval_seconds():.0f}s "
        f"(steady {_poll_interval_seconds():.0f}s; "
        f"burst {_burst_poll_seconds():.0f}s for {_burst_window_seconds():.0f}s "
        "after squashbug; drafts count; immediate poll after pickup)",
    )
    if game is not None:
        last = float(getattr(game, "_kokid_pr_poll_at", 0) or 0)
        if last:
            lines.append(f"  Last poll this boot: {time.time() - last:.0f}s ago")
        else:
            lines.append("  Last poll this boot: (not yet)")
    announced = sorted(_load_announced(root=root))
    if announced:
        tail = announced[-8:]
        lines.append(
            "  Announced PRs (recent): "
            + ", ".join(str(n) for n in tail),
        )
    if probe_github:
        pulls = fetch_pulls_for_poll()
        open_count = sum(1 for p in pulls if (p.get("state") or "") != "merged")
        merged_count = sum(1 for p in pulls if (p.get("state") or "") == "merged")
        lines.append(
            f"  Live probe: {open_count} open + {merged_count} recently merged PR(s)",
        )
        fix_rows, ship_rows = _fix_ship_titles_from_pulls(pulls)
        if fix_rows:
            for pr_number, ids, title in fix_rows[:8]:
                short = (title or "")[:56]
                hit = [i for i in ids if i in bugs]
                flag = " MATCH" if hit else ""
                lines.append(f"    PR {pr_number} Fix bugs {ids}{flag}: {short}")
        else:
            lines.append("    No open PR titles parse as Fix bug #N")
        if ship_rows:
            for pr_number, ids, title in ship_rows[:8]:
                short = (title or "")[:56]
                hit = [i for i in ids if i in sugs]
                flag = " MATCH" if hit else ""
                lines.append(
                    f"    PR {pr_number} Ship suggestion {ids}{flag}: {short}",
                )
        would = []
        for pull in pulls:
            pr_number = pull.get("number")
            if pr_number in _load_announced(root=root):
                continue
            title = pull.get("title") or ""
            bug_ids, _ = bug_ids_from_pr_title(title)
            suggestion_ids, _ = suggestion_ids_from_pr_title(title)
            bug_hit = [b for b in bug_ids if b in bugs]
            sug_hit = [s for s in suggestion_ids if s in sugs]
            if bug_hit or sug_hit:
                would.append(f"PR {pr_number}")
        if would:
            lines.append(f"  Would wiznet now: {', '.join(would)}")
        elif bugs or sugs:
            lines.append(
                "  Would wiznet now: no — no open or recently merged PR matches watched ids",
            )
    lines.append(
        "  Container trace: docker compose logs | findstr kokid",
    )
    return lines


def _normalize_pull_row(row):
    """Shape one GitHub pulls API row for Kokid polling."""
    if not isinstance(row, dict):
        return None
    try:
        number = int(row.get("number"))
    except (TypeError, ValueError):
        return None
    return {
        "number": number,
        "title": row.get("title") or "",
        "html_url": row.get("html_url") or "",
        "draft": bool(row.get("draft")),
        "merged_at": row.get("merged_at") or "",
        "state": (row.get("state") or "").strip().lower(),
    }


def _fetch_pull_rows(*, url, timeout=12):
    """Low-level GET for a pulls listing URL; returns normalized rows or []."""
    req = urllib.request.Request(url, headers=_github_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"{_POLL_LOG_PREFIX} GitHub PR poll failed: {exc}", flush=True)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"{_POLL_LOG_PREFIX} GitHub PR poll failed: bad JSON", flush=True)
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        norm = _normalize_pull_row(row)
        if norm is not None:
            out.append(norm)
    return out


def fetch_open_pulls(*, url=None, timeout=12):
    """Return open PR rows (number, title, html_url, draft, merged_at, state)."""
    return _fetch_pull_rows(url=url or REPO_API_PULLS, timeout=timeout)


def fetch_recent_merged_pulls(*, url=None, timeout=12, lookback_seconds=None):
    """Recently squash-merged PRs — catches fixer runs that merge in under a minute."""
    lookback = (
        float(lookback_seconds)
        if lookback_seconds is not None
        else _closed_lookback_seconds()
    )
    cutoff = time.time() - max(300.0, lookback)
    out = []
    for row in _fetch_pull_rows(url=url or REPO_API_CLOSED_PULLS, timeout=timeout):
        merged_at = _parse_github_iso(row.get("merged_at"))
        if merged_at is None or merged_at < cutoff:
            continue
        row = dict(row)
        row["state"] = "merged"
        out.append(row)
    return out


def fetch_pulls_for_poll(*, lookback_seconds=None):
    """Open PRs plus recently merged (deduped by PR number)."""
    merged = {
        row["number"]: row
        for row in fetch_recent_merged_pulls(lookback_seconds=lookback_seconds)
    }
    for row in fetch_open_pulls():
        merged[row["number"]] = row
    return list(merged.values())


def poll_after_pickup(game, bug_ids=None, *, suggestion_ids=None):
    """Log pickup + run an immediate PR poll (after squashbug / squashsuggest)."""
    bugs = sorted({int(x) for x in (bug_ids or []) if int(x) > 0})
    sugs = sorted({int(x) for x in (suggestion_ids or []) if int(x) > 0})
    if bugs:
        print(
            f"{_POLL_LOG_PREFIX} pickup queued for bug report(s) "
            f"{format_id_list(bugs)} — forcing PR poll",
            flush=True,
        )
    if sugs:
        print(
            f"{_POLL_LOG_PREFIX} pickup queued for suggestion report(s) "
            f"{format_id_list(sugs)} — forcing PR poll",
            flush=True,
        )
    return poll_watched_prs(game, force=True)


def _watch_active(*, root=None):
    """True when Kokid has anything to poll GitHub for."""
    return bool(
        watched_bug_ids(root=root)
        or watched_suggestion_ids(root=root)
        or lag_diag_pending(root=root)
    )


def _poll_interval_elapsed(game, *, force=False, root=None):
    """True when enough time has passed since the last poll (or force).

    Uses the burst-aware interval from main (faster right after squashbug).
    """
    if force:
        return True
    now = time.time()
    last = float(getattr(game, "_kokid_pr_poll_at", 0) or 0)
    interval = _effective_poll_interval_seconds(root=root)
    if last and (now - last) < interval:
        return False
    return True


def _announce_watched_from_pulls(game, pulls, *, root=None):
    """Match watched bugs/suggestions against already-fetched PR rows."""
    root = root or _repo_root()
    watched_bugs = watched_bug_ids(root=root)
    watched_suggestions = watched_suggestion_ids(root=root)
    if not watched_bugs and not watched_suggestions:
        return []

    announced_prs = _load_announced(root=root)
    found = []
    for pull in pulls or ():
        pr_number = pull["number"]
        if pr_number in announced_prs:
            continue
        # Draft Fix/Ship PRs count — Cursor automations open drafts when the
        # work is done; skipping them left watched bugs silent forever until
        # undraft/merge (bug report 156 / PR 1190).
        title = pull.get("title") or ""
        bug_ids, bug_summary = bug_ids_from_pr_title(title)
        suggestion_ids, sug_summary = suggestion_ids_from_pr_title(title)
        bug_hit = [b for b in bug_ids if b in watched_bugs]
        sug_hit = [s for s in suggestion_ids if s in watched_suggestions]
        if not bug_hit and not sug_hit:
            continue
        merged = (pull.get("state") or "") == "merged" or bool(
            pull.get("merged_at")
        )
        draft = bool(pull.get("draft")) and not merged
        if bug_hit:
            msg = format_pr_ready_message(
                bug_hit,
                pr_number,
                pull.get("html_url") or None,
                summary=bug_summary,
                merged=merged,
            )
            announce_as_kokid(game, msg)
            clear_watched_bug_ids(bug_hit, root=root)
            watched_bugs -= set(bug_hit)
            flag = " (merged)" if merged else (" (draft)" if draft else "")
            print(
                f"{_POLL_LOG_PREFIX} wiznet announced PR {pr_number} for bug report(s) "
                f"{', '.join(str(b) for b in bug_hit)}{flag}",
                flush=True,
            )
        if sug_hit:
            msg = format_suggestion_pr_ready_message(
                sug_hit,
                pr_number,
                pull.get("html_url") or None,
                summary=sug_summary,
                merged=merged,
            )
            announce_as_kokid(game, msg)
            clear_watched_suggestion_ids(sug_hit, root=root)
            watched_suggestions -= set(sug_hit)
            flag = " (merged)" if merged else (" (draft)" if draft else "")
            print(
                f"{_POLL_LOG_PREFIX} wiznet announced PR {pr_number} for suggestion report(s) "
                f"{', '.join(str(s) for s in sug_hit)}{flag}",
                flush=True,
            )
        _mark_announced(pr_number, root=root)
        found.append(pr_number)
    _log_poll_summary(
        watched_bugs=watched_bugs,
        watched_suggestions=watched_suggestions,
        pulls=pulls,
        announced_prs=announced_prs,
        found_pr_numbers=found,
    )
    return found


def _announce_lag_from_pulls(game, pulls, *, root=None):
    """Announce the first new ``Fix lag:`` PR when a lag analyze is pending.

    Drafts count (same as Fix-bug watches) so the analyzer PR is announced
    as soon as it opens.
    """
    root = root or _repo_root()
    if not lag_diag_pending(root=root):
        return None

    announced_prs = _load_announced(root=root)
    for pull in pulls or ():
        pr_number = pull["number"]
        if pr_number in announced_prs:
            continue
        title = pull.get("title") or ""
        if not _FIX_LAG_RE.match(title):
            continue
        msg = format_lag_pr_ready_message(
            pr_number,
            pull.get("html_url") or None,
            summary=lag_summary_from_pr_title(title),
        )
        announce_as_kokid(game, msg)
        clear_lag_diag_pending(root=root)
        _mark_announced(pr_number, root=root)
        print(
            f"[kokid] wiznet announced lag-fix PR {pr_number}",
            flush=True,
        )
        return pr_number
    return None


def _announce_from_pulls(game, pulls, *, root=None):
    """Apply one fetched PR list to watched bugs/suggestions and lag watch."""
    root = root or _repo_root()
    found = list(_announce_watched_from_pulls(game, pulls, root=root) or [])
    lag_pr = _announce_lag_from_pulls(game, pulls, root=root)
    if lag_pr is not None:
        found.append(lag_pr)
    return found


def _run_poll_cycle(game, *, root=None, pulls=None):
    """Fetch (unless provided) then announce. Sync helper for smoke / GM force."""
    root = root or _repo_root()
    if pulls is None:
        pulls = fetch_pulls_for_poll()
    return _announce_from_pulls(game, pulls, root=root)


def poll_watched_prs(game, *, root=None, force=False, pulls=None):
    """If watched bugs/suggestions have an open Fix/Ship PR, Kokid-announce.

    Pass ``pulls`` to reuse an already-fetched listing (avoids a second
    GitHub round-trip when the tick also checks lag-diag PRs).
    """
    root = root or _repo_root()
    watched_bugs = watched_bug_ids(root=root)
    watched_suggestions = watched_suggestion_ids(root=root)
    if not watched_bugs and not watched_suggestions:
        return []

    if not _poll_interval_elapsed(game, force=force, root=root):
        return []
    game._kokid_pr_poll_at = time.time()

    if pulls is None:
        pulls = fetch_pulls_for_poll()
    return _announce_watched_from_pulls(game, pulls, root=root)


def poll_lag_diag_pr(game, *, root=None, force=False, pulls=None):
    """If a lag analyze is pending, announce the first new ``Fix lag:`` PR.

    Pass ``pulls`` to reuse the tick's fetch (same listing as bug/suggestion
    watches) instead of a second blocking urllib call.
    """
    root = root or _repo_root()
    if not lag_diag_pending(root=root):
        return None

    if not _poll_interval_elapsed(game, force=force, root=root):
        return None
    game._kokid_pr_poll_at = time.time()

    if pulls is None:
        pulls = fetch_pulls_for_poll()
    return _announce_lag_from_pulls(game, pulls, root=root)


def _log_kokid_poll_task_exception(task):
    """Done-callback: surface a background poll crash without killing the loop."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[kokid] background PR poll crashed (ignored): {exc}", flush=True)


async def _poll_async(game, *, root=None):
    """Fetch GitHub pulls off-thread, then announce on the game loop."""
    root = root or _repo_root()
    try:
        # urllib is blocking; to_thread keeps the asyncio heartbeat free so
        # players can still type while Kokid checks for Fix/Ship PRs.
        pulls = await asyncio.to_thread(fetch_pulls_for_poll)
        _announce_from_pulls(game, pulls, root=root)
    except Exception as exc:
        # Never let a GitHub blip abort the tick loop / background task.
        print(f"[kokid] PR poll crashed (ignored): {exc}", flush=True)


def maybe_poll_on_tick(game):
    """Cheap Game.on_tick hook -- no-op when nothing is watched.

    Production heartbeats schedule the GitHub HTTP in a background task
    (see ``maybe_poll_on_tick_async``) so sync urllib never blocks the
    ~3s tick. Smoke / no-loop callers still poll inline.
    """
    if not _watch_active():
        return []
    if not _poll_interval_elapsed(game, force=False):
        return []
    # One in-flight background poll at a time (overlap would double API load).
    existing = getattr(game, "_kokid_poll_task", None)
    if existing is not None and not existing.done():
        return []

    game._kokid_pr_poll_at = time.time()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync smoke / tools: no event loop — poll inline (rare + OK).
        try:
            return _run_poll_cycle(game)
        except Exception as exc:
            print(f"[kokid] PR poll crashed (ignored): {exc}", flush=True)
            return []

    task = loop.create_task(_poll_async(game))
    game._kokid_poll_task = task
    task.add_done_callback(_log_kokid_poll_task_exception)
    return []


async def maybe_poll_on_tick_async(game):
    """Production async tick entry: schedule background poll, return immediately.

    Registered as ``async_fn`` so ``run_ticks_async`` does not time a
    multi-second urllib wait against ``kokid_pr_poll``.
    """
    maybe_poll_on_tick(game)

