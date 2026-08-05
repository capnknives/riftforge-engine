"""Generic engine help pages for basegame (no SUPERS lore).

``cmd_help``, ``hedit``, ``help_db``, and the bug/suggest pipeline all live
in ``engine/``; topic *text* still ships with the game via
``hooks.set_help()`` (AGENTS.md rule 11). This module holds the pages for
verbs every Riftforge install shares through ``ENGINE_COMMANDS`` so
``RIFTFORGE_GAME=basegame`` players and GMs get ``help <topic>`` without
importing ``supers/``.

Merged into ``basegame.help_topics.HELP_TOPICS`` at import time.
See ``docs/ENGINE_CONSUMER.md`` ("Help files").
"""

# Topic id -> page body. Keep these game-agnostic -- no show names or
# SUPERS-only mechanics (Cadence, deploy SHA credit, etc.).
HELP_ENGINE_TOPICS = {
    "bug": """
bug -- file a bug report

  bug <short description>     file about yourself in one line
  bug                         paste mode for a longer report

Paste mode ends with a lone '.' on its own line, or type cancel to back out.
Recent commands from your session are attached automatically.
Staff triage with reports; close tickets with resolve.

See: help suggest | help helpsubmit | help changes
""",
    "suggest": """
suggest -- file a suggestion / idea

Same shape as bug: one-line argument, or bare suggest for multi-line paste.
Paste mode ends with a lone '.' on its own line, or type cancel to back out.

See: help bug | help helpsubmit
""",
    "helpsubmit": """
helpsubmit -- propose new help content for staff review

  helpsubmit <keyword> <one-line body>   file immediately
  helpsubmit <keyword>                   paste mode for a multi-line page

Same paste rules as bug/suggest. Logged for staff review; online GMs are
pinged. When a proposal is accepted, staff write the live page with
hedit <keyword>, then close the ticket with resolve help <id>.

See: help bug | help hedit | help reports
""",
    "changes": """
changes -- recent changelog entries

  changes           last few entries
  changes <n>       last n entries
  changes detail <n>  full text for entry #n

Each line is a short player-facing summary of what shipped. The [n] stamp
is the changelog id (higher numbers are newer).

See: help bug
""",
    "hedit": """
hedit -- GM: hot-edit a help page live (no deploy)

  hedit <keyword>     open the modal line editor for that page

Loads an existing hot-edited page to revise, or -- for a keyword that does
not have one yet -- pre-fills a new draft from the current static help page
of that name (if there is one). A brand-new keyword with no static page
starts blank. A saved hedit page overrides the static page of the same name
the instant it is saved. It does NOT change help_topics.py itself; pages
worth keeping long-term should still land in a real PR eventually.

While editing, any plain line you type is appended to the page body.
Editor commands (each starts with /):

  /list                 show the page so far, with line numbers
  /i <line> <text>      insert a line at that position
  /d <line>             delete a line
  /clear                wipe the whole body (start over)
  /r <pattern> <repl>   regex find/replace across the whole body
  /syntax <text>        add a line to the syntax/usage block
  /category <name>      set the page category
  /alias <name>         add a lookup alias (repeatable)
  /gm                   toggle GM-only visibility
  /ic                   toggle the in-character tag
  /preview              see exactly what a player would see
  /save                 write the page live
  /cancel               discard everything, no changes made

See: help helpsubmit | help reports | help resolve
""",
    "reports": """
reports -- GM: triage bug / suggestion / help-proposal logs

  reports [n] [all]                    list open tickets (default last 5 each)
  reports show <bug|suggest|help> <id>   full dump for one ticket

Lists bugs, ideas, and help proposals in separate sections so the kinds do
not interleave. Add all to include resolved/rejected entries.

See: help resolve | help hedit | help helpsubmit
""",
    "resolve": """
resolve -- GM: update a report ticket status

  resolve <bug|suggest|help> <id> [open|resolved|rejected]

Omit the status to mark resolved. Use resolve help <id> after hedit has
published a helpsubmit proposal.

See: help reports | help hedit
""",
}

# Bare ``help`` index entries for engine-generic topics.
HELP_ENGINE_CATEGORIES = [
    (
        "Feedback",
        ["bug", "suggest", "helpsubmit", "changes"],
    ),
    (
        "Staff",
        ["hedit", "reports", "resolve"],
    ),
]
