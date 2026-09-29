"""Run a saved job from the command line - for Windows Task Scheduler.

    python run_job.py "Daily sales report"
    python run_job.py "Daily sales report" --draft
    python run_job.py "Daily sales report" --date 2026-08-24
    python run_job.py --list

Gmail must already be authorised through the app once; after that this runs
unattended using the cached refresh token.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tomllib
from pathlib import Path

from tabmailer import gmail, pipeline
from tabmailer.config import find_job, load_settings
from tabmailer.tableau import TableauClient, TableauError

# Keys the app pulls from st.secrets; mirrored here so a scheduled run reads the
# same credentials instead of drifting from a stale .env.
_SECRET_KEYS = (
    "TABLEAU_SERVER_URL",
    "TABLEAU_SITE",
    "TABLEAU_TOKEN_NAME",
    "TABLEAU_TOKEN_SECRET",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_GIST_ID",
)


def _load_local_secrets() -> None:
    """Read .streamlit/secrets.toml into the environment, if it exists.

    Locally this is the single source of truth, so the app and scheduled runs
    cannot disagree. On a host that injects secrets some other way the file is
    simply absent and the existing environment wins.
    """
    path = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"
    if not path.exists():
        return
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"warning: could not read {path.name}: {exc}", file=sys.stderr)
        return
    for key in _SECRET_KEYS:
        value = data.get(key)
        if value not in (None, ""):
            os.environ[key] = str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a saved Tableau report job.")
    parser.add_argument("job", nargs="?", help="Name of the saved job")
    parser.add_argument("--list", action="store_true", help="List saved jobs and exit")
    parser.add_argument("--draft", action="store_true", help="Save as a Gmail draft instead of sending")
    parser.add_argument("--date", help="Override today's date (YYYY-MM-DD) for the {tokens}")
    parser.add_argument("--dry-run", action="store_true", help="Build the files but do not email")
    args = parser.parse_args()

    _load_local_secrets()
    settings = load_settings()

    if args.list or not args.job:
        if not settings.jobs:
            print("No saved jobs. Create one in the app first.")
            return 1
        print("Saved jobs:")
        for j in settings.jobs:
            print(f"  {j.name}  ->  {j.workbook_name} ({len(j.view_ids)} sheets, {len(j.to)} recipients)")
        return 0

    job = find_job(settings, args.job)
    if job is None:
        print(f"No saved job called '{args.job}'.", file=sys.stderr)
        return 1

    if not job.to and not args.dry_run:
        # Fail before spending a minute downloading crosstabs nobody receives.
        print(
            f"Job '{job.name}' has no recipients. Add them on the Compose tab and "
            "save the job, or use --dry-run to build the files only.",
            file=sys.stderr,
        )
        return 1

    today = dt.date.fromisoformat(args.date) if args.date else None

    try:
        views = TableauClient(settings.tableau).list_views(job.workbook_id)
    except TableauError as exc:
        print(f"Tableau error: {exc}", file=sys.stderr)
        return 2

    def progress(message: str, fraction: float) -> None:
        print(f"[{fraction * 100:3.0f}%] {message}")

    try:
        result = pipeline.build(job, settings, views, progress=progress, today=today)
    except TableauError as exc:
        print(f"Tableau error: {exc}", file=sys.stderr)
        return 2

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not result.has_content:
        print("Nothing to send - Tableau returned no data.", file=sys.stderr)
        return 3

    for path in result.saved_paths:
        print(f"wrote {path}")

    if args.dry_run:
        recipients = ", ".join(job.to) if job.to else "(nobody - this job has no recipients)"
        print(f"Dry run - would send '{result.subject}' to {recipients}")
        return 0

    try:
        outcome = pipeline.send(
            result, job, settings, job.to, job.cc, job.bcc, as_draft=args.draft
        )
    except gmail.GmailError as exc:
        print(f"Gmail error: {exc}", file=sys.stderr)
        return 4

    verb = "Drafted" if args.draft else "Sent"
    print(f"{verb}: '{result.subject}' to {', '.join(job.to)} (id {outcome.get('id', '')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
