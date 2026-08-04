"""
deploy_guard.py -- post-overlay sanity checks after auto_deploy lands a fix.

Overlays can silently strip pipeline wiring (e.g. an old commands.py without
squashbugs). These checks print warnings to docker logs; they never block a
deploy that already squash-merged on GitHub.
"""


def check_integrations():
    """Return a list of human-readable warning strings (empty = all good).

    Also prints info lines when webhook URLs are unset -- not a failure,
    just useful in docker logs. Covers all three Cursor automations
    (bug fixer, suggestion implementer, lag diag).
    """
    warnings = []

    try:
        import commands
        # squashbug (one) + squashbugs (bulk); fixbug / fixbugs are aliases.
        for key in ("squashbug", "squashbugs"):
            if key not in commands.COMMANDS:
                warnings.append(
                    f"commands.COMMANDS is missing '{key}' -- "
                    "overlay may have reverted commands.py"
                )
    except Exception as exc:
        warnings.append(f"could not import commands for squashbugs check: {exc}")

    try:
        from engine import bug_webhook
        # Webhook is GM-on-demand (squashbugs), not an after-record hook.
        if not callable(getattr(bug_webhook, "schedule_open_bugs", None)):
            warnings.append(
                "bug_webhook.schedule_open_bugs missing -- "
                "webhook module may be broken"
            )
    except Exception as exc:
        warnings.append(f"could not verify bug_webhook module: {exc}")

    try:
        from engine import bug_webhook
        if not bug_webhook.webhook_url():
            print(
                "[auto_deploy] post-overlay info: CURSOR_BUG_WEBHOOK_URL unset",
                flush=True,
            )
    except Exception:
        # URL probe itself failed -- still log so a broken bug_webhook
        # import does not look like a clean "URL unset" info line.
        import traceback
        print(
            "[auto_deploy] post-overlay: webhook URL probe failed:",
            flush=True,
        )
        traceback.print_exc()

    try:
        import commands
        for key in ("sendsuggest", "squashsuggest"):
            if key not in commands.COMMANDS:
                warnings.append(
                    f"commands.COMMANDS is missing '{key}' -- "
                    "overlay may have reverted commands.py"
                )
    except Exception as exc:
        warnings.append(
            f"could not import commands for squashsuggest check: {exc}"
        )

    try:
        from engine import suggestion_webhook
        if not callable(getattr(suggestion_webhook, "schedule_open_suggestions", None)):
            warnings.append(
                "suggestion_webhook.schedule_open_suggestions missing -- "
                "webhook module may be broken"
            )
    except Exception as exc:
        warnings.append(f"could not verify suggestion_webhook module: {exc}")

    try:
        from engine import suggestion_webhook
        if not suggestion_webhook.webhook_url():
            print(
                "[auto_deploy] post-overlay info: CURSOR_SUGGESTION_WEBHOOK_URL unset",
                flush=True,
            )
    except Exception:
        import traceback
        print(
            "[auto_deploy] post-overlay: suggestion webhook URL probe failed:",
            flush=True,
        )
        traceback.print_exc()

    # Lag diag: GM verb is nested under gm diaglog (not a top-level COMMANDS
    # key). Still verify the module + env so overlays cannot silently strip it.
    try:
        from engine import diag_export
        if not callable(getattr(diag_export, "schedule_analyze", None)):
            warnings.append(
                "diag_export.schedule_analyze missing -- "
                "lag-diag webhook module may be broken"
            )
    except Exception as exc:
        warnings.append(f"could not verify diag_export module: {exc}")

    try:
        from engine import diag_export
        if not diag_export.webhook_url():
            print(
                "[auto_deploy] post-overlay info: "
                "RIFTFORGE_DIAG_WEBHOOK_URL unset",
                flush=True,
            )
    except Exception:
        import traceback
        print(
            "[auto_deploy] post-overlay: diag webhook URL probe failed:",
            flush=True,
        )
        traceback.print_exc()

    # Catalog sanity -- if cursor_automations drifts from env module names,
    # surface it once in logs.
    try:
        from engine import cursor_automations
        for line in cursor_automations.status_lines():
            print(f"[auto_deploy] {line}", flush=True)
    except Exception as exc:
        warnings.append(f"could not load cursor_automations catalog: {exc}")

    return warnings


def run_post_overlay_checks():
    """Print any post-overlay warnings; never raises."""
    for message in check_integrations():
        print(f"[auto_deploy] post-overlay warning: {message}", flush=True)
