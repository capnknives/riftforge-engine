"""
diag_export.py -- push lag / tick diagnosis logs to GitHub + Cursor.

Live hosts are awkward to SSH into from every agent session. GM
``gm diaglog push`` POSTs the NDJSON dump (plus a tick summary) to:

  1. A secret GitHub Gist (full body; returns a shareable URL)
  2. A sticky GitHub Issue comment with the Gist link + a short summary
     (default hub: RiftForge issue #195)

``gm diaglog analyze`` does push, turns NDJSON writers **off**, then POSTs
a dedicated Cursor automation webhook (``RIFTFORGE_DIAG_WEBHOOK_*``) so an
agent can diagnose the dump. Separate from the bug-fixer webhook
(``CURSOR_BUG_WEBHOOK_*`` / ``squashbugs``).

Networking is deliberate and narrow: stdlib ``urllib`` only, same spirit
as ``engine/bug_webhook.py``. The play loop never blocks --
``schedule_push`` / ``schedule_analyze`` use ``asyncio.to_thread``.

Configure (live ``.env`` -- never commit tokens):

  RIFTFORGE_DIAG_GITHUB_TOKEN   -- PAT with gist + issues:write (or classic
                                   repo + gist). Also accepts GITHUB_TOKEN.
  RIFTFORGE_DIAG_GITHUB_REPO    -- default ``capnknives/RiftForge``
  RIFTFORGE_DIAG_GITHUB_ISSUE   -- sticky issue number (default ``195``)
  RIFTFORGE_DIAG_LOG            -- optional override path for the NDJSON file
  RIFTFORGE_DIAG_WEBHOOK_URL    -- Cursor Automations webhook (lag analyzer)
  RIFTFORGE_DIAG_WEBHOOK_AUTH   -- Bearer token from that automation
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import urllib.error
import urllib.request


# Env names -- single source so .env.example / help / tests agree.
TOKEN_ENV = "RIFTFORGE_DIAG_GITHUB_TOKEN"
TOKEN_FALLBACK_ENV = "GITHUB_TOKEN"
REPO_ENV = "RIFTFORGE_DIAG_GITHUB_REPO"
ISSUE_ENV = "RIFTFORGE_DIAG_GITHUB_ISSUE"
LOG_ENV = "RIFTFORGE_DIAG_LOG"
WEBHOOK_URL_ENV = "RIFTFORGE_DIAG_WEBHOOK_URL"
WEBHOOK_AUTH_ENV = "RIFTFORGE_DIAG_WEBHOOK_AUTH"
# Opt-in NDJSON writers (cadence / tick / …). Default OFF so a verified
# live host does not grow multi-MB debug logs every heartbeat. Toggle
# in-game with ``gm diaglog on|off`` (writes OVERRIDE_NAME) or set
# ENABLED_ENV=1 in .env, then ``gm diaglog push``.
ENABLED_ENV = "RIFTFORGE_DIAG_ENABLED"
# GM toggle file (gitignored), same idea as ``.auto_deploy_override``.
OVERRIDE_NAME = ".diag_enabled_override"

DEFAULT_REPO = "capnknives/RiftForge"
# Sticky hub created for live lag dumps (agents: gh issue view 195 --comments).
DEFAULT_ISSUE = 195
# NDJSON filename written by lag instrumentation (repo root).
DEFAULT_LOG_NAME = "debug-e4b2fd.log"
# Session id stamped into every append_event line (Gist / issue hub).
DEFAULT_SESSION_ID = "e4b2fd"
# Webhook payload kind -- Cursor automation filters on this.
WEBHOOK_KIND = "lag_diag"

# Values accepted in the override file / GM command.
_OVERRIDE_ON = "on"
_OVERRIDE_OFF = "off"

# GitHub issue comments cap at 65536; keep the sticky comment short and
# put the bulk in the Gist. Truncate the Gist body so a runaway log cannot
# blow the request.
_MAX_GIST_CHARS = 900_000
_POST_TIMEOUT_SECONDS = 30
_WEBHOOK_TIMEOUT_SECONDS = 15
_API_VERSION = "2022-11-28"
# Cap how many NDJSON lines we parse for the inline stats summary.
_SUMMARY_MAX_LINES = 5000

# Cadence phase keys surfaced in ``summarize_ndjson`` (lag diag analyze).
_CADENCE_PHASE_STAT_KEYS = (
    "echo_ms",
    "echo_other_ms",
    "echo_path_ms",
    "echo_npc_ms",
    "echo_origin_ms",
    "echo_prep_ms",
    "echo_decay_ms",
    "echo_priority_ms",
    "echo_priority_chain_ms",
    "echo_lifestyle_ms",
    "town_ms",
    "town_prep_ms",
    "town_lite_ms",
    "town_offload_ms",
    "town_off_schedule_ms",
    "town_act_preamble_ms",
    "town_act_ms",
    "town_path_ms",
    "town_npc_ms",
    "town_masquerade_ms",
    "town_other_ms",
    "lifestyle_save_busy_ms",
    "motel_hunters_ms",
    "plane_soldiers_ms",
    "hunters_ms",
    "hostiles_misc_ms",
    "vampires_ms",
    "maint_ms",
    "zone_snaps_ms",
)

# Always-on tick overrun burst capture (bug diagnosis kit slice C).
# Independent of ``diag_enabled()`` -- rate-limited so lag storms do not
# fill the disk when staff forgot ``gm diaglog on``.
AUTO_CAPTURE_RATE_LIMIT_S = 45.0
AUTO_CAPTURE_MESSAGE = "auto_tick_overrun"

# Lag P11: watcher-process deploy events share this log with game autosave
# rows so the next ``gm diaglog analyze`` can prove/falsify I/O contention
# without a manual GitHub commit-log cross-reference.
DEPLOY_RESET_MESSAGE = "auto_deploy_reset"
COPYOVER_ABORT_MESSAGE = "copyover_abort"
# Autosave pulses within this many seconds of a deploy reset/overlay count
# as "near" in tick_summary / summarize_ndjson.
DEPLOY_AUTOSAVE_WINDOW_S = 120.0

# Print the missing-auth warning at most once per process.
_warned_missing_webhook_auth = False


def repo_root():
    """Checkout root (parent of ``engine/``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_path():
    """Absolute path to the NDJSON diag log (env override or default name)."""
    override = os.environ.get(LOG_ENV, "").strip()
    if override:
        return override
    return os.path.join(repo_root(), DEFAULT_LOG_NAME)


def override_path(root=None):
    """Absolute path to the GM diag on/off override file."""
    return os.path.join(root or repo_root(), OVERRIDE_NAME)


def read_override(root=None):
    """Return ``'on'``, ``'off'``, or ``None`` if no override / junk file."""
    path = override_path(root)
    try:
        raw = open(path, encoding="utf-8").read().strip().lower()
    except OSError:
        return None
    if raw in (_OVERRIDE_ON, "1", "true", "yes"):
        return _OVERRIDE_ON
    if raw in (_OVERRIDE_OFF, "0", "false", "no"):
        return _OVERRIDE_OFF
    return None


def set_override(value, root=None):
    """Write the GM override to ``on`` or ``off``. Returns the path written."""
    normalized = (value or "").strip().lower()
    if normalized in ("1", "true", "yes"):
        normalized = _OVERRIDE_ON
    if normalized in ("0", "false", "no"):
        normalized = _OVERRIDE_OFF
    if normalized not in (_OVERRIDE_ON, _OVERRIDE_OFF):
        raise ValueError(f"override must be on or off, got {value!r}")
    path = override_path(root)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(normalized + "\n")
    return path


def clear_override(root=None):
    """Delete the GM override file if present. Returns True when removed."""
    path = override_path(root)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def env_enabled():
    """True when ``ENABLED_ENV`` alone would turn writers on (no file check)."""
    raw = os.environ.get(ENABLED_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def diag_enabled(root=None):
    """True when in-process NDJSON tick writers should append.

    Priority: GM override file (``gm diaglog on|off``) wins over
    ``RIFTFORGE_DIAG_ENABLED``. With neither set, writers stay off.
    """
    override = read_override(root)
    if override == _OVERRIDE_ON:
        return True
    if override == _OVERRIDE_OFF:
        return False
    return env_enabled()


def _append_ndjson_line(payload):
    """Write one NDJSON row; returns True on success."""
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
        return True
    except Exception as exc:
        from engine import log_util

        log_util.ops("diag_export", "ndjson append failed", exc=exc)
        return False


def auto_capture_allowed(game, *, now=None):
    """True when the rate limit allows another always-on overrun sample."""
    if game is None:
        return True
    stamp = now if now is not None else time.monotonic()
    last = float(getattr(game, "_diag_auto_capture_last_at", 0.0) or 0.0)
    return (stamp - last) >= AUTO_CAPTURE_RATE_LIMIT_S


def append_auto_capture_event(game, data, *, reason="tick_overrun"):
    """Always-on NDJSON burst when tick/Cadence budget overruns (rate limited).

    Unlike ``append_event``, this does **not** require ``diag_enabled()``.
    Staff still use ``gm diaglog analyze`` to push and inspect the same log.
    """
    if game is not None and not auto_capture_allowed(game):
        return False
    now = time.monotonic()
    if game is not None:
        game._diag_auto_capture_last_at = now
    payload = {
        "sessionId": DEFAULT_SESSION_ID,
        "runId": "auto-capture",
        "hypothesisId": "AUTO",
        "location": "diag_export.py:auto_capture",
        "message": AUTO_CAPTURE_MESSAGE,
        "data": {
            "auto": True,
            "reason": reason,
            **(data or {}),
        },
        "timestamp": int(time.time() * 1000),
    }
    return _append_ndjson_line(payload)


def maybe_notify_auto_capture(game, total_ms):
    """[GM] ping when an auto-capture row lands (staff in GM form only).

    Uses ``ping_gms`` so account-rank GMs still hear it, but
    ``gm_form_only`` skips occupied / gm-off bodies (suggestion 488).
    ``gm staffnotify off`` still silences it for that staffer.
    """
    if game is None:
        return
    try:
        from engine import gm_notify
    except Exception:
        return
    msg = (
        f"auto-diag captured (tick {total_ms:.0f}ms) -- "
        "see gm diaglog analyze"
    )
    try:
        gm_notify.ping_gms(game, msg, gm_form_only=True)
    except Exception as exc:
        print(f"[diag_export] auto-capture ping failed: {exc!r}", flush=True)


def append_copyover_abort_event(payload):
    """Always-on NDJSON row when a Veil rewrite cancels after MSG_BEFORE.

    ``gm copyover last`` reads the sidecar JSON; this row lets
    ``gm diaglog analyze`` see the same abort without a second log.
    """
    data = dict(payload or {})
    row = {
        "sessionId": DEFAULT_SESSION_ID,
        "runId": "copyover-abort",
        "hypothesisId": "COPYOVER",
        "location": "copyover.py:abort",
        "message": COPYOVER_ABORT_MESSAGE,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        return _append_ndjson_line(row)
    except Exception:
        return False


def append_deploy_event(
    *,
    kind="reset",
    from_sha=None,
    to_sha=None,
    n_files_changed=None,
    poll_gap_s=None,
    since_prev_deploy_s=None,
    extra=None,
):
    """Always-on NDJSON row when auto-deploy resets or overlays the tree.

    Independent of ``diag_enabled()`` -- deploy events are rare (one per
    merge) and lag captures need them even when continuous tick writers
    were off. Failures never raise; the watcher must not skip a deploy
    because the diag log could not be written.
    """
    data = {
        "kind": kind,
        "from_sha": from_sha,
        "to_sha": to_sha,
        "n_files_changed": n_files_changed,
        "poll_gap_s": poll_gap_s,
    }
    if since_prev_deploy_s is not None:
        data["since_prev_deploy_s"] = since_prev_deploy_s
    if extra:
        data.update(extra)
    payload = {
        "sessionId": DEFAULT_SESSION_ID,
        "runId": "auto-deploy",
        "hypothesisId": "P11",
        "location": "auto_deploy.py",
        "message": DEPLOY_RESET_MESSAGE,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        return _append_ndjson_line(payload)
    except Exception:
        return False


def correlate_deploy_autosave(text, *, window_s=DEPLOY_AUTOSAVE_WINDOW_S):
    """Pair ``autosave_ms`` rows with nearby ``auto_deploy_reset`` timestamps.

    Returns a dict consumed by ``summarize_ndjson`` and the tick-summary
    line. ``dt_s`` is autosave timestamp minus deploy timestamp: positive
    means the save ran after the reset.
    """
    deploys = []
    autosaves = []
    window_ms = float(window_s) * 1000.0
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("("):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        msg = row.get("message") or ""
        data = row.get("data") or {}
        try:
            ts = int(row.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0
        if not ts:
            continue
        if msg == DEPLOY_RESET_MESSAGE:
            deploys.append((ts, data))
        elif msg == "autosave_ms":
            autosaves.append((ts, data))

    near = []
    for ts, data in autosaves:
        nearest = None
        nearest_dt = None
        for dts, ddata in deploys:
            dt = ts - dts
            if abs(dt) > window_ms:
                continue
            if nearest is None or abs(dt) < abs(nearest_dt):
                nearest = ddata
                nearest_dt = dt
        if nearest is None:
            continue
        near.append({
            "save_ms": data.get("save_ms"),
            "game_meta_ms": data.get("game_meta_ms"),
            "dt_s": round(nearest_dt / 1000.0, 1),
            "deploy_kind": nearest.get("kind"),
            "n_files": nearest.get("n_files_changed"),
        })

    worst = None
    if near:
        vals = []
        for item in near:
            try:
                vals.append(float(item.get("save_ms") or 0))
            except (TypeError, ValueError):
                continue
        if vals:
            worst = round(max(vals), 2)

    return {
        "n_deploys": len(deploys),
        "n_autosaves": len(autosaves),
        "n_autosaves_near_deploy": len(near),
        "window_s": window_s,
        "near": near[:12],
        "worst_near_save_ms": worst,
    }


def format_deploy_autosave_summary(corr):
    """One tick_summary line from ``correlate_deploy_autosave``."""
    if not corr:
        return "deploy_resets=0"
    n_deploys = int(corr.get("n_deploys") or 0)
    if n_deploys <= 0:
        return "deploy_resets=0"
    parts = [
        f"deploy_resets={n_deploys}",
        f"autosaves_near_deploy={corr.get('n_autosaves_near_deploy') or 0}",
        f"window_s={int(corr.get('window_s') or DEPLOY_AUTOSAVE_WINDOW_S)}",
    ]
    worst = corr.get("worst_near_save_ms")
    if worst is not None:
        parts.append(f"worst_near_save_ms={worst}")
    dts = [
        item.get("dt_s")
        for item in (corr.get("near") or [])
        if item.get("dt_s") is not None
    ]
    if dts:
        parts.append("dt_s=" + ",".join(f"{d:+.1f}" for d in dts[:6]))
    return " ".join(str(p) for p in parts)


def append_event(
    hypothesis_id,
    location,
    message,
    data,
    *,
    run_id="post-fix",
    session_id=None,
):
    """Append one NDJSON line when ``diag_enabled()``; otherwise no-op.

    Shared by cadence / tick_registry / fuel / dominion / autosave so
    agents do not scatter open(debug-e4b2fd.log) blocks.
    """
    if not diag_enabled():
        return False
    try:
        payload = {
            "sessionId": session_id or DEFAULT_SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        return _append_ndjson_line(payload)
    except Exception:
        return False


def github_token():
    """Return the PAT, or '' if unset."""
    raw = (
        os.environ.get(TOKEN_ENV, "").strip()
        or os.environ.get(TOKEN_FALLBACK_ENV, "").strip()
    )
    if not raw:
        return ""
    # Accept a pasted ``Authorization: Bearer …`` line.
    lower = raw.lower()
    if lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        lower = raw.lower()
    if lower.startswith("bearer "):
        return raw[7:].strip()
    return raw


def github_repo():
    """``owner/name`` for the sticky issue / API paths."""
    return os.environ.get(REPO_ENV, DEFAULT_REPO).strip() or DEFAULT_REPO


def github_issue_number():
    """Sticky issue number, or None if explicitly disabled (``0`` / empty)."""
    raw = os.environ.get(ISSUE_ENV, str(DEFAULT_ISSUE)).strip()
    if not raw or raw in ("0", "none", "off"):
        return None
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_ISSUE


def status_lines():
    """Human-readable config + log size for ``gm diaglog`` / ``status``."""
    path = log_path()
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    lines = 0
    if exists and size:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            lines = -1
    issue = github_issue_number()
    override = read_override()
    if override == _OVERRIDE_ON:
        writers = f"ON (gm diaglog on → {OVERRIDE_NAME})"
    elif override == _OVERRIDE_OFF:
        writers = f"off (gm diaglog off → {OVERRIDE_NAME})"
    elif diag_enabled():
        writers = f"ON ({ENABLED_ENV}=1)"
    else:
        writers = (
            f"off (gm diaglog on, or set {ENABLED_ENV}=1)"
        )
    return [
        f"Log: {path}",
        (
            f"Exists: {'yes' if exists else 'no'}  "
            f"bytes={size}  lines={lines if lines >= 0 else '?'}"
        ),
        f"NDJSON writers: {writers}",
        (
            f"Analyze webhook: "
            f"{'set' if webhook_url() else 'MISSING (' + WEBHOOK_URL_ENV + ')'}"
            + (
                ""
                if webhook_auth_token() or not webhook_url()
                else f"  auth=MISSING ({WEBHOOK_AUTH_ENV})"
            )
        ),
        f"Token: {'set' if github_token() else 'MISSING (' + TOKEN_ENV + ')'}",
        f"Repo: {github_repo()}",
        (
            f"Issue: #{issue} (https://github.com/{github_repo()}/issues/{issue})"
            if issue
            else "Issue: (disabled -- gist only)"
        ),
    ]


def read_log_text(*, max_chars=_MAX_GIST_CHARS):
    """Return log contents, truncated from the start if oversized."""
    path = log_path()
    if not os.path.isfile(path):
        return "", path, False
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return f"(read failed: {exc})", path, False
    if len(text) > max_chars:
        text = (
            f"(truncated: kept last {max_chars} of {len(text)} chars)\n"
            + text[-max_chars:]
        )
    return text, path, True


def clear_log():
    """Delete the NDJSON file. Returns (ok, message)."""
    path = log_path()
    if not os.path.isfile(path):
        return True, f"No log file at {path}."
    try:
        os.remove(path)
    except OSError as exc:
        return False, f"Could not delete {path}: {exc}"
    return True, f"Cleared {path}."


def _api_headers(token):
    """GitHub REST headers for Gist + Issues."""
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": _API_VERSION,
        "Content-Type": "application/json",
        "User-Agent": "riftforge-diag-export",
    }


def _http_json(method, url, payload, token):
    """Blocking JSON request; returns (status, parsed_body_or_None)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=_api_headers(token),
        method=method,
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
        body = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, body


def tick_hitch_payload(game):
    """Scalars + subphases for auto-capture / tick_summary (lag P23c telemetry)."""
    if game is None:
        return {}
    out = {}
    sub = getattr(game, "_lag_hitch_subphases", None)
    if isinstance(sub, dict) and sub:
        out["hitch_subphases"] = dict(sub)
    for key, attr in (
        ("fighter_n", "fighter_n"),
        ("fighters_prep_ms", "_fighters_prep_ms"),
        ("combat_swing_deferred", "_combat_swing_deferred"),
        ("orphan_slot", "_orphan_reap_slot"),
        ("orphan_reap_ms", "_orphan_reap_ms"),
        ("helper_seek_ms", "_helper_seek_ms"),
    ):
        val = getattr(game, attr, None)
        if val is not None:
            out[key] = val
    autosave = getattr(game, "_last_autosave_stats", None) or {}
    if autosave.get("wall_budget_hit"):
        out["wall_budget_hit"] = True
    slow_detail = autosave.get("slow_char_detail")
    if slow_detail:
        out["slow_char_detail"] = list(slow_detail)[:8]
    fuel_bd = getattr(game, "_last_fuel_phase_breakdown", None) or {}
    fuel_phases = fuel_bd.get("phases") or {}
    if fuel_phases.get("fuel_companions_ms") is not None:
        out["fuel_companions_ms"] = fuel_phases.get("fuel_companions_ms")
    humanity_bd = getattr(game, "_last_humanity_phase_breakdown", None) or {}
    hum_phases = humanity_bd.get("phases") or {}
    if hum_phases.get("roster_ms") is not None:
        out["humanity_roster_ms"] = hum_phases.get("roster_ms")
    return out


def build_tick_summary(game, *, log_text=None):
    """Compact text from ``game._tick_stats`` + Cadence budget + autosave.

    ``log_text`` is the same NDJSON body the Gist ships. When omitted,
    this helper reads the live log so ``gm diaglog`` / ``gm tick`` still
    get the P11 deploy/autosave correlation line.
    """
    ring = list(getattr(game, "_tick_stats", ()) or ())
    meta = getattr(game, "_cadence_budget_meta", None) or {}
    lines = [
        f"game_time_ticks={getattr(game, 'game_time_ticks', None)}",
        f"sessions={len(getattr(game, 'sessions', None) or [])}",
        f"characters={len(getattr(game, 'characters', ()) or ())}",
        f"rooms={len(getattr(game, 'rooms', {}) or {})}",
    ]
    if meta:
        lines.append(
            f"cadence budget used={meta.get('used')}/{meta.get('limit')} "
            f"cast={meta.get('population')} "
            f"(town={meta.get('town_npcs')} echoes={meta.get('echoes')})"
        )
    if not ring:
        lines.append("tick samples: (none yet)")
    else:
        totals = [s.get("total_ms", 0) for s in ring]
        last = ring[-1]
        slow = last.get("slow") or []
        slow_txt = ", ".join(f"{n}={ms:.1f}ms" for n, ms in slow[:8]) or "(none)"
        lines.append(
            f"tick last={last.get('total_ms', 0):.1f}ms "
            f"avg={sum(totals) / len(totals):.1f} "
            f"worst={max(totals):.1f} (n={len(ring)})"
        )
        lines.append(f"last slow=[{slow_txt}]")
        fuel_in_slow = any(name == "fuel" for name, _ms in slow)
        fuel_bd = getattr(game, "_last_fuel_phase_breakdown", None) or {}
        if fuel_in_slow and fuel_bd:
            from engine import hooks
            phase_txt = hooks.fuel_phase_summary_line(fuel_bd)
            if phase_txt:
                lines.append(f"fuel_phases={phase_txt}")
    # Autosave is outside run_ticks -- surface it so lag dumps cannot hide
    # multi-second SQLite freezes behind a healthy tick avg.
    autosave = getattr(game, "_last_autosave_stats", None) or {}
    if autosave:
        parts = [f"save_ms={autosave.get('save_ms')}"]
        if autosave.get("collect_ms") is not None:
            parts.append(f"collect={autosave.get('collect_ms')}")
        if autosave.get("apply_ms") is not None:
            parts.append(f"apply={autosave.get('apply_ms')}")
        if autosave.get("force_full") is not None:
            parts.append(f"force_full={autosave.get('force_full')}")
        if autosave.get("skipped"):
            parts.append("skipped=1")
        if autosave.get("n_changed_chars") is not None:
            parts.append(f"chars={autosave.get('n_changed_chars')}")
        if autosave.get("n_deferred_chars"):
            parts.append(f"deferred_chars={autosave.get('n_deferred_chars')}")
        if autosave.get("wall_budget_hit"):
            parts.append("wall_budget_hit=1")
        slow_detail = autosave.get("slow_char_detail") or []
        if slow_detail:
            top = slow_detail[0]
            frag = ""
            keys = top.get("blob_top_keys") or []
            if keys:
                frag = " top=" + ",".join(
                    f"{row.get('key')}:{row.get('approx_bytes')}"
                    for row in keys[:3]
                )
            parts.append(
                f"slow_char={top.get('name')}"
                f" blob={top.get('blob_ms')}ms"
                f" dumps={top.get('dumps_ms')}ms"
                f" bytes={top.get('blob_bytes')}"
                f"{frag}"
            )
        if autosave.get("game_meta_slices_written") is not None:
            parts.append(f"meta_wrote={autosave.get('game_meta_slices_written')}")
        if autosave.get("game_meta_slices_skipped") is not None:
            parts.append(f"meta_skip={autosave.get('game_meta_slices_skipped')}")
        if autosave.get("game_meta_slices_throttled"):
            parts.append(f"meta_throttle={autosave.get('game_meta_slices_throttled')}")
        if autosave.get("game_meta_skip_tags"):
            parts.append(f"meta_cold_skip={('meta_cold' in str(autosave.get('game_meta_skip_tags')))}")
        meta_ms = float(autosave.get("game_meta_ms") or 0.0)
        meta_tags = autosave.get("game_meta_wrote")
        if meta_tags and meta_ms > 500.0:
            parts.append(f"meta_tags={meta_tags}")
        for slice_key in (
            "accounts_ms", "game_meta_ms", "homesteads_ms", "gather_ms",
            "player_shops_ms", "township_ms", "personal_realms_ms",
            "demesnes_ms",
        ):
            if autosave.get(slice_key) is not None:
                short = slice_key.replace("_ms", "")
                parts.append(f"{short}={autosave.get(slice_key)}")
        if autosave.get("wal_checkpoint_ms") is not None:
            parts.append(f"wal_ckpt={autosave.get('wal_checkpoint_ms')}")
            parts.append(
                f"wal_bytes={autosave.get('wal_file_bytes_before')}"
                f"->{autosave.get('wal_file_bytes_after')}"
            )
        if autosave.get("wall_budget_hit"):
            parts.append("wall_budget_hit=1")
        slow_detail = autosave.get("slow_char_detail")
        if slow_detail:
            names = ", ".join(
                f"{row.get('name')}={row.get('total_ms')}ms"
                for row in slow_detail[:4]
                if isinstance(row, dict)
            )
            if names:
                parts.append(f"slow_char_detail=[{names}]")
        lines.append("autosave last=" + " ".join(str(p) for p in parts))
        running = bool(getattr(game, "_autosave_running", False))
        if running:
            lines.append("autosave running=1")
    else:
        lines.append("autosave last=(none yet)")
    lag_parking_ms = getattr(game, "_lag_parking_write_ms", None)
    if lag_parking_ms is not None:
        lines.append(
            "parking_write last="
            f"write_ms={lag_parking_ms} "
            f"vehicles={getattr(game, '_lag_parking_vehicles', '?')} "
            f"bytes={getattr(game, '_lag_parking_bytes', '?')} "
            f"skipped={getattr(game, '_lag_parking_skipped', 0)}"
        )
    hitch = tick_hitch_payload(game)
    if hitch:
        scalar_bits = []
        for key in (
            "fighter_n",
            "fighters_prep_ms",
            "combat_swing_deferred",
            "orphan_slot",
            "orphan_reap_ms",
            "helper_seek_ms",
            "wall_budget_hit",
            "fuel_companions_ms",
            "humanity_roster_ms",
        ):
            if hitch.get(key) is not None:
                scalar_bits.append(f"{key}={hitch.get(key)}")
        if scalar_bits:
            lines.append("hitch " + " ".join(scalar_bits))
        sub = hitch.get("hitch_subphases") or {}
        if sub:
            parts = []
            for name, row in sorted(sub.items()):
                if isinstance(row, dict):
                    parts.append(f"{name}={row.get('ms')}ms")
            if parts:
                lines.append("hitch_subphases " + " ".join(parts[:12]))
    overlap = tick_save_overlap_hint(game)
    if overlap:
        lines.append(overlap)
    if log_text is None:
        try:
            log_text, _path, found = read_log_text()
            if not found:
                log_text = ""
        except Exception:
            log_text = ""
    lines.append(
        format_deploy_autosave_summary(correlate_deploy_autosave(log_text))
    )
    last_pass = getattr(game, "_cadence_last_pass", None) or {}
    if last_pass:
        lines.append(
            "cadence last_pass "
            f"cpu={last_pass.get('cpu_ms')}ms "
            f"wall={last_pass.get('wall_ms')}ms "
            f"paused={last_pass.get('paused_ms')}ms "
            f"budget={last_pass.get('budget_used')}/"
            f"{last_pass.get('budget_limit')}"
        )
    try:
        stats = summarize_ndjson(log_text)
        summary = stats.get("analyze_summary")
        if summary:
            lines.append("analyze_summary:")
            lines.extend(summary.splitlines())
    except Exception as exc:
        from engine import log_util

        log_util.ops("diag_export", "summarize failed", exc=exc)
    return "\n".join(lines)


def tick_save_overlap_hint(game, slow_handlers=None):
    """Hint when cadence handler wall time likely includes save loop blocking."""
    from engine import tick_registry as tick_reg

    slow = slow_handlers
    if slow is None:
        ring = list(getattr(game, "_tick_stats", ()) or ())
        if ring:
            slow = ring[-1].get("slow") or []
    cadence_slow = any(
        name == "cadence" and float(ms or 0) >= tick_reg.HANDLER_WARN_MS
        for name, ms in (slow or [])
    )
    if not cadence_slow:
        return None
    # Lag U3: only flag overlap when a save is in-flight on *this* tick,
    # not merely because the last autosave was slow (mislabels Cadence).
    running = bool(getattr(game, "_autosave_running", False))
    if running:
        return "cadence_wall_includes_save_overlap=1"
    return None


def push_sync(game=None, *, reporter="?"):
    """Blocking: create Gist (+ optional issue comment). Returns result dict.

    result keys: ok, message, gist_url, issue_url, error
    """
    token = github_token()
    if not token:
        return {
            "ok": False,
            "message": (
                f"No GitHub token. Set {TOKEN_ENV} (or {TOKEN_FALLBACK_ENV}) "
                "in the live .env and restart / copyover."
            ),
            "gist_url": None,
            "issue_url": None,
            "error": "missing_token",
        }

    log_text, path, found = read_log_text()
    if game is not None:
        tick_summary = build_tick_summary(game, log_text=log_text)
    else:
        tick_summary = "(no game)\n" + format_deploy_autosave_summary(
            correlate_deploy_autosave(log_text)
        )
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    filename = f"riftforge-diag-{int(time.time())}.ndjson"
    if not found or not log_text.strip():
        # Still push a stub so ops get the tick summary + config status.
        log_text = (
            f"(no NDJSON at {path} -- tick summary only)\n"
            + "\n".join(status_lines())
        )

    gist_body = {
        "description": (
            f"Riftforge lag diag from {reporter} @ {stamp}"
        ),
        "public": False,
        "files": {
            filename: {"content": log_text},
            "tick_summary.txt": {"content": tick_summary},
        },
    }
    try:
        status, gist = _http_json(
            "POST", "https://api.github.com/gists", gist_body, token
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}"
            except Exception:
                detail = f"HTTP {exc.code}"
        return {
            "ok": False,
            "message": f"Gist create failed: {detail}",
            "gist_url": None,
            "issue_url": None,
            "error": "gist_failed",
        }

    gist_url = (gist or {}).get("html_url") if status and gist else None
    if not gist_url:
        return {
            "ok": False,
            "message": f"Gist create returned HTTP {status} without html_url",
            "gist_url": None,
            "issue_url": None,
            "error": "gist_bad_response",
        }

    issue_n = github_issue_number()
    issue_url = None
    if issue_n:
        repo = github_repo()
        comment = (
            f"### Lag diag push — `{reporter}` — {stamp}\n\n"
            f"**Gist (full NDJSON):** {gist_url}\n\n"
            f"```\n{tick_summary}\n```\n\n"
            f"Log path on host: `{path}`\n"
        )
        api = f"https://api.github.com/repos/{repo}/issues/{issue_n}/comments"
        try:
            _http_json("POST", api, {"body": comment}, token)
            issue_url = f"https://github.com/{repo}/issues/{issue_n}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            # Gist already landed -- report partial success.
            return {
                "ok": True,
                "message": (
                    f"Gist ok, but issue #{issue_n} comment failed: {exc}. "
                    f"Gist: {gist_url}"
                ),
                "gist_url": gist_url,
                "issue_url": f"https://github.com/{repo}/issues/{issue_n}",
                "error": "issue_comment_failed",
            }

    hub = issue_url or gist_url
    return {
        "ok": True,
        "message": (
            f"Pushed diag log. Hub: {hub}"
            + (f"  Gist: {gist_url}" if issue_url else "")
        ),
        "gist_url": gist_url,
        "issue_url": issue_url,
        "error": None,
    }


def _log_task_exception(task):
    """Done-callback: surface escaped task crashes."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[diag_export] background task crashed: {exc}", flush=True)


async def _push_async(game, reporter, session):
    """Run push_sync off-loop; tell the GM the result."""
    from engine import gm_notify

    try:
        result = await asyncio.to_thread(push_sync, game, reporter=reporter)
        msg = result.get("message") or "(no message)"
        if session is not None:
            gm_notify.send_gm_session(session, f"diaglog: {msg}")
            if result.get("gist_url"):
                gm_notify.send_gm_session(
                    session, f"diaglog: gist {result['gist_url']}",
                )
            if result.get("issue_url"):
                gm_notify.send_gm_session(
                    session, f"diaglog: issue {result['issue_url']}",
                )
        print(f"[diag_export] {msg}", flush=True)
    except Exception as exc:
        print(f"[diag_export] unexpected error: {exc}", flush=True)
        if session is not None:
            gm_notify.send_gm_session(
                session, f"diaglog: unexpected error: {exc}",
            )


def schedule_push(game, *, reporter="?", session=None):
    """Fire-and-forget push. Returns True if a task was scheduled."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync smoke / no loop: run blocking so tests can assert.
        result = push_sync(game, reporter=reporter)
        if session is not None:
            from engine import gm_notify

            gm_notify.send_gm_session(
                session,
                "diaglog: " + (result.get("message") or ""),
            )
        return result.get("ok", False)

    task = loop.create_task(_push_async(game, reporter, session))
    task.add_done_callback(_log_task_exception)
    return True


# --- Cursor lag-analyzer webhook (gm diaglog analyze) ----------------------


def webhook_url():
    """Return the configured Cursor lag-diag webhook URL, or '' if unset."""
    return os.environ.get(WEBHOOK_URL_ENV, "").strip()


def webhook_auth_token():
    """Bearer token for the lag-diag webhook (no ``Bearer `` prefix)."""
    raw = os.environ.get(WEBHOOK_AUTH_ENV, "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        lower = raw.lower()
    if lower.startswith("bearer "):
        return raw[7:].strip()
    return raw


def webhook_request_headers():
    """HTTP headers for the outbound Cursor automation POST."""
    headers = {"Content-Type": "application/json"}
    token = webhook_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _maybe_warn_missing_webhook_auth():
    """Log once when a URL is configured but auth is not."""
    global _warned_missing_webhook_auth
    if _warned_missing_webhook_auth or not webhook_url() or webhook_auth_token():
        return
    _warned_missing_webhook_auth = True
    print(
        f"[diag_export] {WEBHOOK_URL_ENV} is set but {WEBHOOK_AUTH_ENV} "
        "is missing -- Cursor will reject POSTs with HTTP 401. "
        "See .env.example / .cursor/automations/lag-diag-analyzer.",
        flush=True,
    )


def summarize_ndjson(text):
    """Cheap stats from NDJSON for the webhook body (no Gist fetch needed).

    Returns a dict with counts and ms averages for cadence / slow ticks,
    echo sub-phases (``cadence_echo_diag``), autosave pulses, and deploy
    correlation (lag P11).
    """
    from collections import defaultdict

    messages = defaultdict(int)
    echo_ms = []
    town_ms = []
    fuel_total = []
    fuel_loop_ms = []
    cadence_total = []
    cadence_cpu = []
    cadence_paused = []
    cadence_unaccounted = []
    phase_ms = {key: [] for key in _CADENCE_PHASE_STAT_KEYS}
    slow_total = []
    autosave_ms = []
    autosave_lock_wait = []
    parking_write_ms = []
    hitch_subphase_ms = defaultdict(list)
    wall_budget_hits = 0
    handler_ms = defaultdict(list)
    worst_slow = None
    save_busy_ticks = 0
    lines_seen = 0
    for line in (text or "").splitlines():
        if lines_seen >= _SUMMARY_MAX_LINES:
            break
        line = line.strip()
        if not line or line.startswith("("):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        lines_seen += 1
        msg = row.get("message") or "?"
        messages[msg] += 1
        data = row.get("data") or {}
        if msg == "cadence_phase_breakdown":
            cadence_total.append(float(data.get("total_ms") or data.get("wall_ms") or 0))
            cadence_cpu.append(float(data.get("cpu_ms") or 0))
            cadence_paused.append(float(data.get("paused_ms") or 0))
            if data.get("unaccounted_ms") is not None:
                cadence_unaccounted.append(float(data.get("unaccounted_ms") or 0))
            phases = data.get("phases") or {}
            echo_ms.append(float(phases.get("echo_ms") or 0))
            town_ms.append(float(phases.get("town_ms") or 0))
            for key in _CADENCE_PHASE_STAT_KEYS:
                val = phases.get(key)
                if val:
                    phase_ms[key].append(float(val))
        elif msg in ("slow_or_spike_tick", AUTO_CAPTURE_MESSAGE) or data.get("auto"):
            total = float(data.get("total_ms") or 0)
            slow_total.append(total)
            if worst_slow is None or total > float(worst_slow.get("total_ms") or 0):
                worst_slow = {
                    "total_ms": round(total, 1),
                    "game_time_ticks": data.get("game_time_ticks"),
                    "top_handlers": (data.get("top_handlers") or [])[:6],
                    "autosave_running": bool(data.get("autosave_running")),
                    "cadence_budget": data.get("cadence_budget"),
                    "hitch_subphases": data.get("hitch_subphases"),
                    "fighter_n": data.get("fighter_n"),
                    "fighters_prep_ms": data.get("fighters_prep_ms"),
                    "orphan_slot": data.get("orphan_slot"),
                    "helper_seek_ms": data.get("helper_seek_ms"),
                    "wall_budget_hit": data.get("wall_budget_hit"),
                    "slow_char_detail": (data.get("slow_char_detail") or [])[:4],
                }
            if total >= 2000.0 and (
                data.get("autosave_running")
                or float(data.get("last_autosave_save_ms") or 0) >= 500.0
            ):
                save_busy_ticks += 1
            for h in data.get("top_handlers") or []:
                name = h.get("name")
                if name:
                    handler_ms[name].append(float(h.get("ms") or 0))
        elif msg == "fuel_phase_breakdown":
            fuel_total.append(float(data.get("total_ms") or 0))
            phases = data.get("phases") or {}
            fuel_loop_ms.append(float(phases.get("fuel_loop_ms") or 0))
        elif msg == "autosave_ms":
            save = float(data.get("save_ms") or 0)
            autosave_ms.append(save)
            lock = data.get("lock_wait_ms")
            if lock is not None:
                autosave_lock_wait.append(float(lock))
            if data.get("wall_budget_hit"):
                wall_budget_hits += 1
        elif msg == "parking_write_ms":
            parking_write_ms.append(float(data.get("write_ms") or 0))
        elif msg == "hitch_subphase":
            sub_name = data.get("name") or "?"
            hitch_subphase_ms[sub_name].append(float(data.get("ms") or 0))

    deploy_corr = correlate_deploy_autosave(text)

    def _stat(xs):
        if not xs:
            return None
        return {
            "n": len(xs),
            "avg": round(statistics.mean(xs), 1),
            "p50": round(statistics.median(xs), 1),
            "max": round(max(xs), 1),
        }

    phase_stats = {
        key: _stat(xs) for key, xs in phase_ms.items() if xs
    }
    top_phases = sorted(
        (
            {"phase": key, **_stat(xs)}
            for key, xs in phase_ms.items()
            if xs and statistics.mean(xs) >= 5.0
        ),
        key=lambda row: -row["avg"],
    )[:10]

    top_handlers = sorted(
        (
            {"name": n, "avg_ms": round(statistics.mean(xs), 1), "n": len(xs)}
            for n, xs in handler_ms.items()
        ),
        key=lambda row: -row["avg_ms"],
    )[:12]
    stats = {
        "ndjson_lines_parsed": lines_seen,
        "messages": dict(messages),
        "cadence_total_ms": _stat(cadence_total),
        "cadence_cpu_ms": _stat(cadence_cpu),
        "cadence_paused_ms": _stat(cadence_paused),
        "cadence_unaccounted_ms": _stat(cadence_unaccounted),
        "echo_ms": _stat(echo_ms),
        "town_ms": _stat(town_ms),
        "cadence_phases": phase_stats,
        "top_cadence_phases_by_avg": top_phases,
        "fuel_total_ms": _stat(fuel_total),
        "fuel_loop_ms": _stat(fuel_loop_ms),
        "slow_tick_total_ms": _stat(slow_total),
        "autosave_ms": _stat(autosave_ms),
        "autosave_lock_wait_ms": _stat(autosave_lock_wait),
        "parking_write_ms": _stat(parking_write_ms),
        "wall_budget_hit_ticks": wall_budget_hits,
        "hitch_subphases": {
            name: _stat(xs) for name, xs in hitch_subphase_ms.items() if xs
        },
        "save_busy_cadence_spikes": save_busy_ticks,
        "worst_slow_tick": worst_slow,
        "top_handlers_by_avg": top_handlers,
        "deploy_autosave": deploy_corr,
        "deploy_autosave_line": format_deploy_autosave_summary(deploy_corr),
    }
    stats["analyze_summary"] = format_analyze_summary(stats)
    return stats


def format_analyze_summary(stats):
    """Plain-text hotspot block for Gist ``tick_summary`` + webhook skimmers."""
    if not stats:
        return "(no stats)"
    lines = []
    worst = stats.get("worst_slow_tick") or {}
    if worst.get("total_ms"):
        tops = ", ".join(
            f"{h.get('name')}={h.get('ms')}ms"
            for h in (worst.get("top_handlers") or [])[:5]
        )
        lines.append(
            f"worst_tick={worst.get('total_ms')}ms "
            f"game_ticks={worst.get('game_time_ticks')} "
            f"autosave_running={worst.get('autosave_running')} "
            f"[{tops}]"
        )
    cadence_cpu = stats.get("cadence_cpu_ms") or {}
    cadence_wall = stats.get("cadence_total_ms") or {}
    cadence_pause = stats.get("cadence_paused_ms") or {}
    if cadence_wall.get("avg") is not None:
        lines.append(
            "cadence wall_avg={wall}ms cpu_avg={cpu}ms paused_avg={pause}ms".format(
                wall=cadence_wall.get("avg"),
                cpu=cadence_cpu.get("avg"),
                pause=cadence_pause.get("avg"),
            )
        )
    cadence_hole = stats.get("cadence_unaccounted_ms") or {}
    if cadence_hole.get("avg") is not None:
        lines.append(
            "cadence unaccounted_avg={avg}ms max={mx}ms".format(
                avg=cadence_hole.get("avg"),
                mx=cadence_hole.get("max"),
            )
        )
    for row in (stats.get("top_cadence_phases_by_avg") or [])[:5]:
        lines.append(
            f"phase {row.get('phase')} avg={row.get('avg')}ms max={row.get('max')}ms"
        )
    town_wall = stats.get("town_ms") or {}
    town_phases = stats.get("cadence_phases") or {}
    if town_wall.get("avg") is not None and float(town_wall.get("avg") or 0) >= 100.0:
        act_avg = (town_phases.get("town_act_ms") or {}).get("avg")
        preamble_avg = (town_phases.get("town_act_preamble_ms") or {}).get("avg")
        path_avg = (town_phases.get("town_path_ms") or {}).get("avg")
        save_busy_avg = (town_phases.get("lifestyle_save_busy_ms") or {}).get("avg")
        cadence_cpu = stats.get("cadence_cpu_ms") or {}
        lines.append(
            "town wall_avg={wall}ms cadence_cpu_avg={cpu}ms "
            "act_avg={act}ms preamble_avg={pre}ms path_avg={path}ms "
            "save_busy_yield_avg={busy}ms".format(
                wall=town_wall.get("avg"),
                cpu=cadence_cpu.get("avg"),
                act=act_avg if act_avg is not None else "-",
                pre=preamble_avg if preamble_avg is not None else "-",
                path=path_avg if path_avg is not None else "-",
                busy=save_busy_avg if save_busy_avg is not None else "-",
            )
        )
    for row in (stats.get("top_handlers_by_avg") or [])[:6]:
        lines.append(
            f"handler {row.get('name')} avg={row.get('avg_ms')}ms n={row.get('n')}"
        )
    autosave = stats.get("autosave_ms") or {}
    if autosave.get("avg") is not None:
        lines.append(
            f"autosave avg={autosave.get('avg')}ms max={autosave.get('max')}ms"
        )
    parking = stats.get("parking_write_ms") or {}
    if parking.get("avg") is not None:
        lines.append(
            f"parking_write avg={parking.get('avg')}ms max={parking.get('max')}ms"
        )
    if stats.get("wall_budget_hit_ticks"):
        lines.append(f"wall_budget_hit_ticks={stats.get('wall_budget_hit_ticks')}")
    hitch = stats.get("hitch_subphases") or {}
    top_hitch = sorted(
        hitch.items(),
        key=lambda pair: -float((pair[1] or {}).get("avg") or 0),
    )[:6]
    for name, row in top_hitch:
        if (row or {}).get("avg") is not None:
            lines.append(
                f"hitch {name} avg={row.get('avg')}ms max={row.get('max')}ms"
            )
    if stats.get("save_busy_cadence_spikes"):
        lines.append(
            f"save_busy_cadence_spikes={stats.get('save_busy_cadence_spikes')} "
            "(slow tick >=2s during autosave -- overlap, not Cadence CPU)"
        )
    deploy_line = stats.get("deploy_autosave_line")
    if deploy_line:
        lines.append(deploy_line)
    return "\n".join(lines) if lines else "(no hotspots in sample)"


def build_analyze_payload(push_result, game=None, *, reporter="?"):
    """JSON body for the Cursor lag-diag automation."""
    log_text, path, _found = read_log_text()
    tick_summary = (
        build_tick_summary(game, log_text=log_text)
        if game is not None
        else "(no game)"
    )
    return {
        "kind": WEBHOOK_KIND,
        "reporter": reporter,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gist_url": (push_result or {}).get("gist_url"),
        "issue_url": (push_result or {}).get("issue_url"),
        "push_ok": bool((push_result or {}).get("ok")),
        "push_message": (push_result or {}).get("message"),
        "log_path": path,
        "tick_summary": tick_summary,
        "status_lines": status_lines(),
        "stats": summarize_ndjson(log_text),
        "hub_issue": (
            f"https://github.com/{github_repo()}/issues/{github_issue_number()}"
            if github_issue_number()
            else None
        ),
        "instructions_hint": (
            "Diagnose live tick lag from this dump. Read stats.analyze_summary "
            "and gist NDJSON. Echo sub-phases: echo_other_ms / echo_path_ms / "
            "echo_npc_ms (cadence_echo_diag). Compare cadence_cpu_ms vs "
            "cadence_total_ms + cadence_paused_ms -- multi-second total tick "
            "with autosave_running is usually overlap, not Cadence CPU. "
            "Check stats.deploy_autosave and save_busy_cadence_spikes."
        ),
    }


def post_webhook_sync(url, payload, *, headers=None):
    """Blocking HTTPS POST of one lag-diag JSON body."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=dict(
            headers if headers is not None else webhook_request_headers()
        ),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT_SECONDS) as resp:
        resp.read()
        return resp.status


def analyze_sync(game=None, *, reporter="?"):
    """Push Gist, turn writers off, POST Cursor webhook. Returns result dict.

    Always attempts ``set_override('off')`` after the push so a successful
    analyze cannot leave the host writing forever. Webhook skip (no URL)
    still turns writers off and returns ok=False with a clear message.
    """
    push_result = push_sync(game, reporter=reporter)
    # Stop NDJSON writers regardless of push success -- analyze ends capture.
    try:
        off_path = set_override(_OVERRIDE_OFF)
        writers_off = True
        writers_message = f"NDJSON writers off ({off_path})."
    except Exception as exc:
        writers_off = False
        writers_message = f"Could not turn writers off: {exc}"

    target = webhook_url()
    if not target:
        push_bit = (
            "Push ok"
            if push_result.get("ok")
            else f"Push incomplete ({push_result.get('error') or 'failed'})"
        )
        return {
            "ok": False,
            "message": (
                f"{push_bit}; writers off. No {WEBHOOK_URL_ENV} configured "
                "-- cannot queue Cursor analyzer. "
                + (push_result.get("message") or "")
            ),
            "gist_url": push_result.get("gist_url"),
            "issue_url": push_result.get("issue_url"),
            "webhook_status": None,
            "writers_off": writers_off,
            "error": "missing_webhook",
        }

    _maybe_warn_missing_webhook_auth()
    payload = build_analyze_payload(
        push_result, game, reporter=reporter
    )
    try:
        status = post_webhook_sync(target, payload)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "message": (
                f"Push ok-ish; writers off; webhook POST failed: {exc}. "
                f"Gist: {push_result.get('gist_url')}"
            ),
            "gist_url": push_result.get("gist_url"),
            "issue_url": push_result.get("issue_url"),
            "webhook_status": None,
            "writers_off": writers_off,
            "error": "webhook_failed",
        }

    hub = push_result.get("issue_url") or push_result.get("gist_url")
    return {
        "ok": True,
        "message": (
            f"Analyze queued (HTTP {status}). {writers_message} "
            f"Hub: {hub}"
        ),
        "gist_url": push_result.get("gist_url"),
        "issue_url": push_result.get("issue_url"),
        "webhook_status": status,
        "writers_off": writers_off,
        "error": None,
    }


def _should_announce_lag_queued(result):
    """Wiznet when we have a hub link, even if the Cursor webhook POST failed."""
    if not result:
        return False
    return bool(result.get("gist_url") or result.get("issue_url"))


def _announce_lag_queued_if_ready(game, result, reporter):
    if not _should_announce_lag_queued(result):
        return
    try:
        from engine import kokid_notify

        kokid_notify.announce_lag_queued(
            game,
            result.get("gist_url"),
            result.get("issue_url"),
            reporter,
        )
    except Exception as exc:
        print(
            f"[diag_export] kokid lag queued announce skipped: {exc}",
            flush=True,
        )


async def _analyze_async(game, reporter, session):
    """Run analyze_sync off-loop; tell the GM the result."""
    from engine import gm_notify

    try:
        result = await asyncio.to_thread(
            analyze_sync, game, reporter=reporter
        )
        msg = result.get("message") or "(no message)"
        if session is not None:
            gm_notify.send_gm_session(session, f"diaglog: {msg}")
            if result.get("gist_url"):
                gm_notify.send_gm_session(
                    session, f"diaglog: gist {result['gist_url']}",
                )
            if result.get("issue_url"):
                gm_notify.send_gm_session(
                    session, f"diaglog: issue {result['issue_url']}",
                )
            if result.get("writers_off"):
                gm_notify.send_gm_session(
                    session,
                    "diaglog: NDJSON writers are OFF "
                    "(re-enable with `gm diaglog on` to capture again).",
                )
        print(f"[diag_export] analyze: {msg}", flush=True)
        _announce_lag_queued_if_ready(game, result, reporter)
    except Exception as exc:
        print(f"[diag_export] analyze unexpected error: {exc}", flush=True)
        if session is not None:
            gm_notify.send_gm_session(
                session, f"diaglog: analyze error: {exc}",
            )


def schedule_analyze(game, *, reporter="?", session=None):
    """Fire-and-forget analyze (push + webhook + writers off)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        result = analyze_sync(game, reporter=reporter)
        if session is not None:
            from engine import gm_notify
            gm_notify.send_gm_session(
                session,
                "diaglog: " + (result.get("message") or ""),
            )
        _announce_lag_queued_if_ready(game, result, reporter)
        return result.get("ok", False)

    task = loop.create_task(_analyze_async(game, reporter, session))
    task.add_done_callback(_log_task_exception)
    return True
