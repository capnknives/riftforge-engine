"""
discord_bridge.py -- tagged outbound Discord posts (stdlib urllib).

Mortals and Monsters can mirror in-game "radio" surfaces to Discord
channels without pulling discord.py into the game.

Design
------
Callers pass a **tag** (stable routing key) plus plain text::

    schedule_discord("hunt_tip", "[HUNT TIP | lookout_shout]\\n…")
    schedule_discord("angel_radio", "[ANGEL RADIO]\\n…")

Env maps each tag to a Discord **channel snowflake**. A shared bot token
posts ``POST /channels/{id}/messages``. Optional per-tag **webhook** URLs
override the bot path (safer for live: channel webhook cannot manage the
guild).

Networking stays here (same pattern as ``bug_webhook.py``): never block the
asyncio play loop -- ``asyncio.to_thread`` + fire-and-forget task. Unset
token / unknown tag = silent no-op.

Environment
-----------
DISCORD_BRIDGE_BOT_TOKEN   Bot token (or fall back to DISCORD_BOT_TOKEN)
DISCORD_BRIDGE_CHANNELS    ``tag:channel_id,tag:channel_id``
                           Example: hunt_tip:1528…,angel_radio:1528…
DISCORD_BRIDGE_WEBHOOK_<TAG>  Optional; if set for a tag, POST to that
                           Discord webhook instead of the bot API
                           (TAG uppercased, e.g. DISCORD_BRIDGE_WEBHOOK_HUNT_TIP)
DISCORD_BRIDGE_MIN_INTERVAL_SEC  Optional per-tag cooldown (default 3)

Known tags (v1)
---------------
hunt_tip      -- hunter tip-line opens (haunt / elemental / board accepts …)
              Ambient kinds (lookout_shout) stay in-game only.
angel_radio   -- pray / angel radio broadcasts
ooc           -- global ``ooc`` channel (Town Square ``#ooc``)
wknz          -- WKNZ Discord radio: host talk, weather/warnings,
                 rare music-flow line (never lyrics / ads / fluff),
                 gateway outage down/up ([WKNZ] Wits)
bug_report    -- brief player ``bug`` filing to Discord #staff (no debug).
              Use the **bot** channel map (not a webhook) when you want
              resolved tickets to get a checkmark reaction on the post.
suggestion    -- brief player ``suggest`` filing to Discord #staff
wiznet        -- two-way staff chat with in-game ``wiznet`` (#staff)
patch_notes   -- short player changelog summaries after auto-deploy (#news)

Add a Discord channel + env mapping when you wire a new tag; keep the
in-game call site one line: ``schedule_discord(tag, text)``.

Runtime mutes (GM ``discord ooc|weather|crash|wiznet on|off``)
------------------------------------------------------
File ``.discord_bridge_toggles`` (gitignored JSON) can mute subsets
without tearing down env webhooks. Gateway reloads this module and
re-reads the file on every outage schedule so ``crash`` covers outage
down/up from the parent process without a container bounce. Planned
game-only reloads (copyover / watcher SIGUSR1) send IPC
``planned_restart`` and never post outage pairs. Missing file / missing
key = ON (mirror allowed).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

API_BASE = "https://discord.com/api/v10"

# Primary token env; DISCORD_BOT_TOKEN is the setup-script name -- accept both
# so one token can drive layout apply and live bridge during pre-alpha.
BOT_TOKEN_ENV = "DISCORD_BRIDGE_BOT_TOKEN"
BOT_TOKEN_FALLBACK_ENV = "DISCORD_BOT_TOKEN"
CHANNELS_ENV = "DISCORD_BRIDGE_CHANNELS"
MIN_INTERVAL_ENV = "DISCORD_BRIDGE_MIN_INTERVAL_SEC"
WEBHOOK_ENV_PREFIX = "DISCORD_BRIDGE_WEBHOOK_"
# Real-seconds between "*Music flows through the radio*" Discord posts.
MUSIC_FLOW_INTERVAL_ENV = "DISCORD_BRIDGE_WKNZ_MUSIC_INTERVAL_SEC"

# GM mute file (same idea as ``.auto_deploy_override``) -- game + gateway.
TOGGLES_NAME = ".discord_bridge_toggles"
# Keys staff can flip with ``discord <key> on|off``.
TOGGLE_KEYS = ("ooc", "weather", "crash", "wiznet")
_DEFAULT_TOGGLES = {key: True for key in TOGGLE_KEYS}

_POST_TIMEOUT_SECONDS = 15
_DEFAULT_MIN_INTERVAL = 3.0
# Default: one music-flow Discord line per half hour of wall time.
_DEFAULT_MUSIC_FLOW_INTERVAL = 1800.0

# Canned Discord music tell -- never send copyrighted lyrics over the bridge.
MUSIC_FLOW_LINE = "*Music flows through the radio*"

# WKNZ Discord outage tells (gateway hold). Plain [WKNZ] header so
# format_tagged_message does not double-wrap. Voice is Wits (station DJ).
WKNZ_OUTAGE_DOWN = (
    "[WKNZ] Wits: Sorry, Lebanon -- brief station break. If you're "
    "stuck in the Veil on hold, hang tight. We'll be back on the air "
    "shortly."
)
WKNZ_OUTAGE_UP = (
    "[WKNZ] Wits: And we're back. Thanks for holding -- that brief "
    "downtime is cleared and the dial's live again."
)

# Last successful schedule monotonic time per tag (process-local cooldown).
_last_sent_mono: dict[str, float] = {}
# Separate timer for the rare music-flow line (longer than tip spam brake).
_last_music_flow_mono: float = 0.0

# Discord message content hard limit.
_MAX_CONTENT = 1900


def _repo_root() -> Path:
    """Checkout root (parent of ``engine/``)."""
    return Path(__file__).resolve().parent.parent


def toggles_path(root=None) -> Path:
    """Absolute path to the GM Discord mute file."""
    base = Path(root) if root is not None else _repo_root()
    return base / TOGGLES_NAME


def default_toggles() -> dict:
    """Fresh mute map -- every known key ON (mirroring allowed)."""
    return dict(_DEFAULT_TOGGLES)


def read_toggles(root=None) -> dict:
    """Load mute flags from disk. Missing / corrupt file → all ON.

    Re-read on every schedule so a GM flip is visible to the game child
    and the gateway parent without a restart.
    """
    path = toggles_path(root)
    out = default_toggles()
    if not path.is_file():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return out
    if not isinstance(raw, dict):
        return out
    for key in TOGGLE_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, bool):
            out[key] = val
        elif isinstance(val, str):
            low = val.strip().lower()
            if low in ("on", "true", "1", "yes"):
                out[key] = True
            elif low in ("off", "false", "0", "no"):
                out[key] = False
    return out


def write_toggles(toggles, root=None) -> Path:
    """Persist a full toggle map. Returns the path written."""
    path = toggles_path(root)
    clean = default_toggles()
    if isinstance(toggles, dict):
        for key in TOGGLE_KEYS:
            if key in toggles:
                clean[key] = bool(toggles[key])
    path.write_text(
        json.dumps(clean, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def set_toggle(key: str, enabled: bool, root=None) -> tuple[bool, str, Path | None]:
    """Set one mute key. Returns (ok, message, path_or_None)."""
    key_s = str(key or "").strip().lower()
    if key_s not in TOGGLE_KEYS:
        return False, (
            "Unknown Discord toggle. Use: ooc | weather | crash"
        ), None
    current = read_toggles(root)
    current[key_s] = bool(enabled)
    path = write_toggles(current, root)
    state = "on" if enabled else "off"
    return True, f"Discord {key_s} mirror set to {state}.", path


def is_toggle_on(key: str, root=None) -> bool:
    """True when the named mirror is allowed (default ON)."""
    key_s = str(key or "").strip().lower()
    if key_s not in TOGGLE_KEYS:
        return True
    return bool(read_toggles(root).get(key_s, True))


def status_text(root=None) -> str:
    """Multi-line status for bare ``discord`` / ``discord status``."""
    toggles = read_toggles(root)
    path = toggles_path(root)
    lines = [
        "Discord bridge mirrors (GM toggles -- env webhooks still required):",
    ]
    for key in TOGGLE_KEYS:
        state = "on" if toggles.get(key, True) else "off"
        lines.append(f"  {key}: {state}")
    lines.append(f"File: {path}")
    if not path.is_file():
        lines.append("(no override file yet -- defaults are all on)")
    lines.append(
        "Usage: discord  |  discord <ooc|weather|crash|wiznet> <on|off>  |  "
        "discord status"
    )
    return "\r\n".join(lines)


def _mirror_allowed(tag: str, kind: str | None) -> bool:
    """False when a GM mute blocks this tag/kind pair.

    Mapping:
      ooc      -- tag ``ooc``
      wiznet   -- tag ``wiznet`` (Discord #staff two-way staff chat)
      weather  -- tag ``wknz`` + kind weather|warning
      crash    -- tag ``wknz`` + kind outage (gateway down/up)
    Hunt tips / angel radio / WKNZ talk+music are not covered by these
    keys (add a new toggle if staff need those muted too).
    """
    tag_s = str(tag or "").strip().lower()
    kind_s = str(kind or "").strip().lower()
    if tag_s == "ooc":
        return is_toggle_on("ooc")
    if tag_s == "wiznet":
        return is_toggle_on("wiznet")
    if tag_s == "wknz":
        if kind_s in ("weather", "warning"):
            return is_toggle_on("weather")
        if kind_s == "outage":
            return is_toggle_on("crash")
    return True


def bot_token() -> str:
    """Return the configured bot token, or '' if unset."""
    primary = os.environ.get(BOT_TOKEN_ENV, "").strip()
    if primary:
        return primary
    return os.environ.get(BOT_TOKEN_FALLBACK_ENV, "").strip()


def min_interval_seconds() -> float:
    """Minimum seconds between posts for the same tag (spam brake)."""
    raw = os.environ.get(MIN_INTERVAL_ENV, "").strip()
    if not raw:
        return _DEFAULT_MIN_INTERVAL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MIN_INTERVAL


def music_flow_interval_seconds() -> float:
    """Wall seconds between WKNZ Discord music-flow posts (default 30 min)."""
    raw = os.environ.get(MUSIC_FLOW_INTERVAL_ENV, "").strip()
    if not raw:
        return _DEFAULT_MUSIC_FLOW_INTERVAL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MUSIC_FLOW_INTERVAL


def parse_channel_map(raw: str | None = None) -> dict[str, str]:
    """Parse ``tag:id,tag:id`` into a dict. Bad pairs are skipped."""
    text = raw if raw is not None else os.environ.get(CHANNELS_ENV, "")
    text = (text or "").strip()
    out: dict[str, str] = {}
    if not text:
        return out
    for part in text.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tag, channel_id = part.split(":", 1)
        tag = tag.strip().lower()
        channel_id = channel_id.strip()
        if tag and channel_id.isdigit():
            out[tag] = channel_id
    return out


def webhook_url_for_tag(tag: str) -> str:
    """Optional Discord incoming-webhook URL for one tag."""
    key = f"{WEBHOOK_ENV_PREFIX}{str(tag or '').strip().upper()}"
    return os.environ.get(key, "").strip()


def channel_id_for_tag(tag: str) -> str:
    """Channel snowflake for tag, or '' if unmapped."""
    return parse_channel_map().get(str(tag or "").strip().lower(), "")


def format_tagged_message(tag: str, kind: str | None, body: str) -> str:
    """Build a player-readable Discord body with a plain-language tag line.

    Meaning stays in the words (accessibility): never color-only. Kind is
    optional sub-label (e.g. lookout_shout under hunt_tip).
    """
    tag_s = str(tag or "signal").strip() or "signal"
    kind_s = str(kind or "").strip()
    body_s = str(body or "").strip()
    # If the game already stamped a plain header (angel radio / OOC),
    # keep that line as the whole Discord content -- no double wrap.
    if body_s.startswith("[") or body_s.startswith("((OOC))"):
        text = body_s
    else:
        if kind_s:
            header = f"[{tag_s.upper().replace('_', ' ')} | {kind_s}]"
        else:
            header = f"[{tag_s.upper().replace('_', ' ')}]"
        text = header if not body_s else f"{header}\n{body_s}"
    if len(text) > _MAX_CONTENT:
        text = text[: _MAX_CONTENT - 3] + "..."
    return text


def _cooldown_ok(tag: str) -> bool:
    """True if this tag may post now (updates timer only on True).

    Tags ``ooc`` and ``wknz`` skip the tip-line spam brake -- they are
    player talk / host broadcasts and dropping lines under a 3s gate
    feels broken. Discord's own webhook rate limits still apply; failed
    POSTs stay fail-soft. The rare music-flow line uses a separate
    30-minute gate in ``schedule_wknz_music_flow`` before calling here.
    """
    now = time.monotonic()
    if tag in ("ooc", "wknz", "bug_report", "wiznet"):
        _last_sent_mono[tag] = now
        return True
    interval = min_interval_seconds()
    last = _last_sent_mono.get(tag, 0.0)
    if interval > 0 and (now - last) < interval:
        return False
    _last_sent_mono[tag] = now
    return True


def post_channel_message_sync(token: str, channel_id: str, content: str) -> int:
    """Blocking bot POST of one channel message. Returns HTTP status."""
    status, _message_id = post_channel_message_with_id_sync(
        token, channel_id, content,
    )
    return status


def post_channel_message_with_id_sync(
    token: str, channel_id: str, content: str,
) -> tuple[int, str | None]:
    """Blocking bot POST; return ``(http_status, message_id_or_none)``."""
    url = f"{API_BASE}/channels/{channel_id}/messages"
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "RiftforgeDiscordBridge (MortalsAndMonsters; stdlib)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
        status = resp.status
    message_id = None
    try:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and payload.get("id") is not None:
            message_id = str(payload["id"])
    except (UnicodeDecodeError, ValueError, TypeError):
        message_id = None
    return status, message_id


def add_message_reaction_sync(
    token: str, channel_id: str, message_id: str, emoji: str = "\u2705",
) -> int:
    """Blocking bot PUT to add one reaction (default checkmark)."""
    encoded = urllib.parse.quote(str(emoji or "\u2705"))
    url = (
        f"{API_BASE}/channels/{channel_id}/messages/{message_id}"
        f"/reactions/{encoded}/@me"
    )
    req = urllib.request.Request(
        url,
        data=b"",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "RiftforgeDiscordBridge (MortalsAndMonsters; stdlib)",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        resp.read()
        return resp.status


def post_webhook_sync(url: str, content: str) -> int:
    """Blocking Discord incoming-webhook POST. Returns HTTP status."""
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RiftforgeDiscordBridge (MortalsAndMonsters; stdlib)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_SECONDS) as resp:
        resp.read()
        return resp.status


async def _post_async(tag: str, content: str) -> None:
    """Worker: prefer per-tag webhook, else bot+channel; log failures soft."""
    try:
        hook = webhook_url_for_tag(tag)
        if hook:
            status = await asyncio.to_thread(post_webhook_sync, hook, content)
            print(
                f"[discord_bridge] webhook ok tag={tag} HTTP {status}",
                flush=True,
            )
            return
        token = bot_token()
        channel_id = channel_id_for_tag(tag)
        if not token or not channel_id:
            return
        status = await asyncio.to_thread(
            post_channel_message_sync, token, channel_id, content
        )
        print(
            f"[discord_bridge] bot ok tag={tag} channel={channel_id} "
            f"HTTP {status}",
            flush=True,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"[discord_bridge] POST failed tag={tag}: {exc}", flush=True)
    except Exception as exc:
        print(f"[discord_bridge] unexpected error tag={tag}: {exc}", flush=True)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback: surface a task crash that escaped _post_async."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        print(f"[discord_bridge] background task crashed: {exc}", flush=True)


def schedule_discord(tag: str, body: str, *, kind: str | None = None) -> bool:
    """Queue one tagged Discord post; return True if a task was scheduled.

    Silent no-op when the tag has neither a webhook nor (token + channel),
    when body is empty after format, when a GM mute blocks the tag/kind,
    or when the per-tag cooldown blocks.
    """
    tag_s = str(tag or "").strip().lower()
    if not tag_s:
        return False
    if not _mirror_allowed(tag_s, kind):
        return False
    content = format_tagged_message(tag_s, kind, body)
    if not str(body or "").strip() and kind is None:
        # Allow header-only only when kind provided a useful label.
        pass
    if not str(body or "").strip():
        return False

    hook = webhook_url_for_tag(tag_s)
    token = bot_token()
    channel_id = channel_id_for_tag(tag_s)
    if not hook and not (token and channel_id):
        return False
    if not _cooldown_ok(tag_s):
        return False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop (offline tools / some smokes) -- sync best-effort.
        try:
            if hook:
                post_webhook_sync(hook, content)
            elif token and channel_id:
                post_channel_message_sync(token, channel_id, content)
            return True
        except Exception as exc:
            print(f"[discord_bridge] sync POST failed tag={tag_s}: {exc}", flush=True)
            return False

    task = loop.create_task(_post_async(tag_s, content))
    task.add_done_callback(_log_task_exception)
    return True


async def _bot_post_with_id_async(
    tag: str,
    content: str,
    on_posted: Callable[[str, str], None] | None,
) -> None:
    """Worker: bot-only POST that returns a message id for later reactions."""
    try:
        token = bot_token()
        channel_id = channel_id_for_tag(tag)
        if not token or not channel_id:
            return
        status, message_id = await asyncio.to_thread(
            post_channel_message_with_id_sync, token, channel_id, content,
        )
        print(
            f"[discord_bridge] bot tracked ok tag={tag} channel={channel_id} "
            f"HTTP {status} message_id={message_id or '(none)'}",
            flush=True,
        )
        if message_id and on_posted is not None:
            on_posted(channel_id, message_id)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        # Bot token revoked or #staff overwrite missing — fall back to the
        # per-tag webhook when configured so bug briefs still reach Discord.
        hook = webhook_url_for_tag(tag)
        if hook:
            try:
                status = await asyncio.to_thread(post_webhook_sync, hook, content)
                print(
                    f"[discord_bridge] webhook fallback ok tag={tag} HTTP {status} "
                    f"(bot failed: {exc})",
                    flush=True,
                )
                return
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                OSError,
            ) as hook_exc:
                print(
                    f"[discord_bridge] tracked POST failed tag={tag}: {exc}; "
                    f"webhook fallback also failed: {hook_exc}",
                    flush=True,
                )
                return
        print(f"[discord_bridge] tracked POST failed tag={tag}: {exc}", flush=True)
    except Exception as exc:
        print(f"[discord_bridge] tracked POST unexpected error tag={tag}: {exc}", flush=True)


def schedule_bot_post_with_id(
    tag: str,
    content: str,
    *,
    on_posted: Callable[[str, str], None] | None = None,
) -> bool:
    """Queue a bot-channel post and call ``on_posted(channel_id, message_id)``.

    Ignores per-tag webhooks — reactions need the bot API. Silent no-op when
    bot token or channel map is missing, or when there is no running loop.
    """
    tag_s = str(tag or "").strip().lower()
    if not tag_s or not str(content or "").strip():
        return False
    token = bot_token()
    channel_id = channel_id_for_tag(tag_s)
    if not token or not channel_id:
        return False
    if not _cooldown_ok(tag_s):
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            status, message_id = post_channel_message_with_id_sync(
                token, channel_id, content,
            )
            if message_id and on_posted is not None:
                on_posted(channel_id, message_id)
            return bool(status)
        except Exception as exc:
            print(
                f"[discord_bridge] sync tracked POST failed tag={tag_s}: {exc}",
                flush=True,
            )
            return False
    task = loop.create_task(_bot_post_with_id_async(tag_s, content, on_posted))
    task.add_done_callback(_log_task_exception)
    return True


async def _add_reaction_async(channel_id: str, message_id: str, emoji: str) -> None:
    """Worker: add one reaction via the bot token."""
    try:
        token = bot_token()
        if not token:
            return
        status = await asyncio.to_thread(
            add_message_reaction_sync, token, channel_id, message_id, emoji,
        )
        print(
            f"[discord_bridge] reaction ok channel={channel_id} "
            f"message={message_id} HTTP {status}",
            flush=True,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(
            f"[discord_bridge] reaction failed message={message_id}: {exc}",
            flush=True,
        )
    except Exception as exc:
        print(f"[discord_bridge] reaction unexpected error: {exc}", flush=True)


def schedule_message_reaction(
    channel_id: str, message_id: str, emoji: str = "\u2705",
) -> bool:
    """Queue a checkmark (or other emoji) on an existing bot message."""
    if not str(channel_id or "").strip() or not str(message_id or "").strip():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            token = bot_token()
            if not token:
                return False
            add_message_reaction_sync(token, channel_id, message_id, emoji)
            return True
        except Exception as exc:
            print(f"[discord_bridge] sync reaction failed: {exc}", flush=True)
            return False
    task = loop.create_task(_add_reaction_async(channel_id, message_id, emoji))
    task.add_done_callback(_log_task_exception)
    return True


def schedule_hunt_tip(summary: str, *, kind: str | None = None) -> bool:
    """Convenience: tip-line → tag ``hunt_tip`` (Discord #hunter-radio)."""
    return schedule_discord("hunt_tip", summary, kind=kind or None)


def schedule_angel_radio(message: str) -> bool:
    """Convenience: angel radio → tag ``angel_radio`` (#angel-radio)."""
    return schedule_discord("angel_radio", message, kind=None)


def schedule_ooc(plain_line: str) -> bool:
    """Convenience: global OOC line → tag ``ooc`` (Discord #ooc).

    Pass the same plain ``((OOC)) [Name]: …`` string players see in-game
    so Discord stays in lockstep (no second header layer).
    """
    return schedule_discord("ooc", plain_line, kind=None)


def schedule_wiznet(plain_line: str) -> bool:
    """Convenience: staff wiznet → tag ``wiznet`` (Discord #staff).

    Pass the same plain ``[WIZ] Name(GM): …`` string staff see in-game.
    """
    return schedule_discord("wiznet", plain_line, kind=None)


def schedule_wknz(body: str, *, kind: str | None = None) -> bool:
    """Convenience: WKNZ Discord radio → tag ``wknz``.

    Allowed kinds (callers enforce the filter; this just labels the post):
      talk     -- on-air host / guest character broadcasts
      weather  -- scheduled town WX bulletin (top of hour every 4 game-hours)
      warning  -- severe / advisory weather
      music    -- rare canned music-flow line (use schedule_wknz_music_flow)
    Never mirror lyrics, ads, news, CB, Nightside, or song titles here.
    """
    return schedule_discord("wknz", body, kind=kind or "talk")


def schedule_wknz_music_flow() -> bool:
    """Post the canned music-flow line at most once per music interval.

    Returns False when the 30-minute (default) gate blocks, when the
    webhook/channel is unset, or when Discord schedule fails soft.
    Discord webhooks are free -- this gate is channel hygiene, not cost.
    """
    global _last_music_flow_mono
    now = time.monotonic()
    interval = music_flow_interval_seconds()
    if interval > 0 and (now - _last_music_flow_mono) < interval:
        return False
    # Stamp before schedule so a failed POST still counts as "tried" and
    # does not hammer Discord every tick while misconfigured.
    _last_music_flow_mono = now
    return schedule_wknz(MUSIC_FLOW_LINE, kind="music")


def schedule_wknz_outage_down() -> bool:
    """Discord: game is down / clients on gateway hold. Fail-soft."""
    return schedule_wknz(WKNZ_OUTAGE_DOWN, kind="outage")


def schedule_wknz_outage_up() -> bool:
    """Discord: game IPC is back after an announced outage. Fail-soft."""
    return schedule_wknz(WKNZ_OUTAGE_UP, kind="outage")


def schedule_patch_notes(body: str) -> bool:
    """Convenience: deploy changelog brief → tag ``patch_notes`` (#patch-notes)."""
    return schedule_discord("patch_notes", body, kind=None)
