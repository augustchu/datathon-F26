"""Review reports in an existing local moderation stack."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from facility_tool.moderation import ModerationStack, REPORT_STATUSES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Path to the existing SQLite moderation stack.")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Read the newest reports first. Defaults to open reports.")
    listing.add_argument("--status", default="open", choices=("all", *REPORT_STATUSES))
    listing.add_argument("--facility-id")
    listing.add_argument("--service", help="Canonical service key, such as dialysis.")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--before-id", type=int, help="Lowest report ID from the preceding page.")
    history = commands.add_parser("history", help="Read the chronological decisions for one report.")
    history.add_argument("report_id", type=int)
    review = commands.add_parser("review", help="Record a moderator decision or reopen a report.")
    review.add_argument("report_id", type=int)
    review.add_argument("status", choices=REPORT_STATUSES)
    review.add_argument("--moderator", required=True, help="Internal moderator account key.")
    review.add_argument("--expected-version", type=int, required=True, help="Current version from the list command.")
    review.add_argument("--decision-id", help="Optional random UUID v4. Keep it to retry the same decision.")
    args = parser.parse_args(argv)
    path = args.db.expanduser()
    if not path.is_file():
        parser.error("The moderation stack file does not exist.")
    try:
        stack = ModerationStack(path)
        if args.command == "list":
            reports = stack.list_reports(
                status=None if args.status == "all" else args.status,
                facility_id=args.facility_id, service=args.service, limit=args.limit, before_id=args.before_id,
            )
            data = [asdict(report) for report in reports]
        elif args.command == "history":
            data = [asdict(decision) for decision in stack.list_decisions(args.report_id)]
        else:
            data = asdict(stack.review_report(
                args.report_id, status=args.status, moderator=args.moderator,
                expected_version=args.expected_version, decision_id=args.decision_id,
            ))
    except (KeyError, ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"{error.args[0]}\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
