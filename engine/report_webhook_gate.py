"""
report_webhook_gate.py -- filter squashbugs / squashsuggest before webhooks fire.

GM bulk queue should not POST one Cursor agent per row when the ticket is
already fixed on the deployed tree or Kokid is already watching for a PR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import reports

SKIP_WATCHED = "watched"
SKIP_DEPLOYED_OPEN = "fixed_on_deploy"


@dataclass
class WebhookPlan:
    """Which open reports to POST vs skip, after optional reconcile."""

    kind: str
    to_queue: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    reconciled_bugs: list[int] = field(default_factory=list)
    reconciled_suggestions: list[int] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return len(self.to_queue) + len(self.skipped)

    @property
    def scheduled_count(self) -> int:
        return len(self.to_queue)


def reconcile_tickets(game) -> dict[str, list[int]]:
    """Mark open tickets resolved when Fix/Ship subjects are already deployed."""
    if game is None:
        return {"bugs": [], "suggestions": []}
    from engine import auto_deploy
    from engine import deploy_notify

    directory = game.report_dir
    open_bugs_before = set(auto_deploy.open_bug_ids(directory))
    open_sug_before = set(auto_deploy.unresolved_suggestion_ids(directory))
    deploy_notify.reconcile_deployed_ticket_heals(game)
    open_bugs_after = set(auto_deploy.open_bug_ids(directory))
    open_sug_after = set(auto_deploy.unresolved_suggestion_ids(directory))
    return {
        "bugs": sorted(open_bugs_before - open_bugs_after),
        "suggestions": sorted(open_sug_before - open_sug_after),
    }


def _entries(kind, directory, *, report_ids=None, statuses=None):
    wanted_status = tuple(statuses) if statuses is not None else ("open",)
    entries = [
        entry
        for entry in reports.recent(kind, None, directory=directory)
        if (entry.get("status", "open") in wanted_status)
    ]
    if report_ids is not None:
        wanted = set(report_ids)
        entries = [entry for entry in entries if entry.get("id") in wanted]
    return entries


def _watched_ids(kind, *, root=None):
    from engine import kokid_notify

    if kind == reports.BUG:
        return kokid_notify.watched_bug_ids(root=root)
    return kokid_notify.watched_suggestion_ids(root=root)


def _deployed_open_ids(kind, directory):
    """Open tickets that still match deployed Fix/Ship ids (reconcile missed)."""
    from engine import auto_deploy

    git_root = auto_deploy.git_root_for(directory)
    open_ids = set(
        auto_deploy.open_bug_ids(directory)
        if kind == reports.BUG
        else auto_deploy.unresolved_suggestion_ids(directory)
    )
    if not open_ids:
        return set()
    if kind == reports.BUG:
        deployed = set(auto_deploy.deployed_fix_bug_ids(git_root, directory))
    else:
        deployed = set(auto_deploy.deployed_fix_suggestion_ids(git_root, directory))
    return open_ids & deployed


def plan_bugs(directory, game=None, *, bug_ids=None, reconcile=True):
    """Build a webhook plan for open bug reports."""
    reconciled = (
        reconcile_tickets(game) if reconcile and game is not None
        else {"bugs": [], "suggestions": []}
    )
    watched = _watched_ids(reports.BUG, root=directory)
    still_deployed = _deployed_open_ids(reports.BUG, directory)
    plan = WebhookPlan(
        kind="bug",
        reconciled_bugs=list(reconciled.get("bugs") or []),
        reconciled_suggestions=list(reconciled.get("suggestions") or []),
    )
    for entry in _entries(reports.BUG, directory, report_ids=bug_ids):
        bid = entry.get("id")
        if bid in watched:
            plan.skipped.append({"id": bid, "reason": SKIP_WATCHED})
            continue
        if bid in still_deployed:
            plan.skipped.append({"id": bid, "reason": SKIP_DEPLOYED_OPEN})
            continue
        plan.to_queue.append(entry)
    return plan


def plan_suggestions(directory, game=None, *, suggestion_ids=None, reconcile=True):
    """Build a webhook plan for suggestion reports.

    Bulk ``squashsuggest`` (no ids) queues **approved** ideas only.
    A specific id still matches open or approved so ``sendsuggest`` can
    pick one from the inbox (and the GM verb auto-approves it first).
    """
    reconciled = (
        reconcile_tickets(game) if reconcile and game is not None
        else {"bugs": [], "suggestions": []}
    )
    watched = _watched_ids(reports.SUGGEST, root=directory)
    still_deployed = _deployed_open_ids(reports.SUGGEST, directory)
    plan = WebhookPlan(
        kind="suggest",
        reconciled_bugs=list(reconciled.get("bugs") or []),
        reconciled_suggestions=list(reconciled.get("suggestions") or []),
    )
    statuses = (
        ("open", "approved") if suggestion_ids is not None else ("approved",)
    )
    for entry in _entries(
        reports.SUGGEST, directory, report_ids=suggestion_ids,
        statuses=statuses,
    ):
        sid = entry.get("id")
        if sid in watched:
            plan.skipped.append({"id": sid, "reason": SKIP_WATCHED})
            continue
        if sid in still_deployed:
            plan.skipped.append({"id": sid, "reason": SKIP_DEPLOYED_OPEN})
            continue
        plan.to_queue.append(entry)
    return plan


_REASON_LABELS = {
    SKIP_WATCHED: "already watching for PR",
    SKIP_DEPLOYED_OPEN: "fix already on deployed tree",
}


def format_plan_summary(plan: WebhookPlan, *, preview: bool = False) -> str:
    """Staff-facing one-liner + skip/reconcile detail for GM squash verbs."""
    noun = "bug" if plan.kind == "bug" else "suggestion"
    if preview:
        head = (
            f"Preview: would queue {plan.scheduled_count}/"
            f"{plan.matched_count} open {noun}(s)."
        )
    else:
        head = (
            f"Queued {plan.scheduled_count}/{plan.matched_count} "
            f"open {noun}(s) for the webhook."
        )
    lines = [head]
    if plan.reconciled_bugs:
        sample = ", ".join(str(n) for n in plan.reconciled_bugs[:10])
        extra = ""
        if len(plan.reconciled_bugs) > 10:
            extra = f" (+{len(plan.reconciled_bugs) - 10} more)"
        lines.append(
            f"Reconciled {len(plan.reconciled_bugs)} bug(s) already on "
            f"deployed tree: {sample}{extra}."
        )
    if plan.reconciled_suggestions:
        sample = ", ".join(str(n) for n in plan.reconciled_suggestions[:10])
        extra = ""
        if len(plan.reconciled_suggestions) > 10:
            extra = f" (+{len(plan.reconciled_suggestions) - 10} more)"
        lines.append(
            f"Reconciled {len(plan.reconciled_suggestions)} suggestion(s) "
            f"already on deployed tree: {sample}{extra}."
        )
    if plan.skipped:
        by_reason: dict[str, list[int]] = {}
        for row in plan.skipped:
            by_reason.setdefault(row["reason"], []).append(int(row["id"]))
        for reason, ids in sorted(by_reason.items()):
            label = _REASON_LABELS.get(reason, reason)
            sorted_ids = sorted(ids)
            sample = ", ".join(str(n) for n in sorted_ids[:10])
            extra = f" (+{len(sorted_ids) - 10} more)" if len(sorted_ids) > 10 else ""
            lines.append(
                f"Skipped {len(sorted_ids)} ({label}): {sample}{extra}."
            )
    if preview and plan.to_queue:
        queue_ids = sorted(int(e.get("id")) for e in plan.to_queue if e.get("id"))
        sample = ", ".join(str(n) for n in queue_ids[:15])
        extra = f" (+{len(queue_ids) - 15} more)" if len(queue_ids) > 15 else ""
        lines.append(f"Would queue: {sample}{extra}.")
    return " ".join(lines)


def parse_squash_mode_args(text: str) -> tuple[str, bool, bool]:
    """Return ``(remainder, preview, no_reconcile)`` for squash GM args."""
    raw = (text or "").strip()
    preview = False
    no_reconcile = False
    while raw:
        head, _, tail = raw.partition(" ")
        token = head.lower()
        if token in ("preview", "dry-run", "dryrun"):
            preview = True
            raw = tail.strip()
            continue
        if token == "noreconcile":
            no_reconcile = True
            raw = tail.strip()
            continue
        break
    return raw, preview, no_reconcile
