"""
deploy_guard.py -- post-overlay sanity checks after auto_deploy lands a fix.

Overlays can silently strip pipeline wiring (e.g. an old commands.py without
squashbugs). These checks print warnings to docker logs; they never block a
deploy that already squash-merged on GitHub.
"""


def check_integrations():
    """Return a list of human-readable warning strings (empty = all good).

    Also prints an info line when the webhook URL is unset -- not a failure,
    just useful in docker logs.
    """
    warnings = []

    try:
        import commands
        # squashbugs is the primary GM webhook verb; fixbugs is kept as alias.
        if "squashbugs" not in commands.COMMANDS:
            warnings.append(
                "commands.COMMANDS is missing 'squashbugs' -- "
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
        pass

    return warnings


def run_post_overlay_checks():
    """Print any post-overlay warnings; never raises."""
    for message in check_integrations():
        print(f"[auto_deploy] post-overlay warning: {message}", flush=True)
