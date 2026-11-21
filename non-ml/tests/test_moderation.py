from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from uuid import uuid4

from facility_tool.moderation import ModerationConflict, ModerationStack


class ModerationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "admin" / "reports.sqlite3"
        self.stack = ModerationStack(self.path, allowed_services={"dialysis", "mri"})

    def test_submission_survives_new_stack_instances_and_uses_stable_paging(self):
        first = self.stack.submit_report("fdr:001", "dialysis")
        second = self.stack.submit_report("fdr:002", "mri", reason="poor_fit")
        third = self.stack.submit_report("fdr:001", "dialysis", reason="poor_fit")
        reopened = ModerationStack(self.path)
        self.assertEqual(reopened.get_report(first.report_id), first)
        self.assertEqual(first.status, "open")
        self.assertEqual(first.version, 0)
        self.assertEqual(first.updated_at, first.submitted_at)
        self.assertEqual(datetime.fromisoformat(first.submitted_at).utcoffset(), timedelta())
        self.assertEqual(reopened.list_reports(status="open", limit=2), [third, second])
        self.assertEqual(reopened.list_reports(before_id=second.report_id), [first])
        self.assertEqual(reopened.list_reports(facility_id="fdr:001", service="dialysis"), [third, first])

    def test_concurrent_retries_share_one_submission_and_cannot_change_it(self):
        token = uuid4()
        start = Barrier(4)

        def submit():
            start.wait(timeout=5)
            return self.stack.submit_report("fdr:007", "dialysis", submission_id=token)

        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(submit) for _ in range(4)]
            reports = [result.result(timeout=15) for result in pending]
        self.assertEqual(len(set(reports)), 1)
        self.assertEqual(self.stack.list_reports(), reports[:1])
        with self.assertRaises(ModerationConflict):
            self.stack.submit_report("fdr:008", "dialysis", submission_id=token)

    def test_latest_open_reports_stay_first_after_an_older_submission_retries(self):
        token = uuid4()
        older = self.stack.submit_report("fdr:001", "dialysis", submission_id=token)
        closed = self.stack.submit_report("fdr:002", "mri")
        newest = self.stack.submit_report("fdr:003", "mri")
        self.stack.review_report(closed.report_id, moderator="reviewer", status="dismissed", expected_version=0)
        retry = self.stack.submit_report("fdr:001", "dialysis", submission_id=token)
        self.assertEqual(retry, older)
        self.assertEqual(self.stack.list_reports(limit=1), [newest])
        self.assertEqual(self.stack.list_reports(status="open", before_id=newest.report_id), [older])
        self.assertEqual(
            [report.report_id for report in self.stack.list_reports(status=None)],
            [newest.report_id, closed.report_id, older.report_id],
        )

    def test_review_distinguishes_outcomes_and_replays_original_revision_by_token(self):
        first = self.stack.submit_report("nqas:001", "dialysis")
        token = str(uuid4())
        reviewed = self.stack.review_report(
            first.report_id, moderator="reviewer-1", status="no_answer", expected_version=0, decision_id=token,
        )
        self.assertEqual(reviewed.status, "no_answer")
        self.assertEqual(reviewed.version, 1)
        temporary = self.stack.review_report(
            first.report_id, moderator="reviewer-2", status="temporarily_unavailable", expected_version=1,
        )
        self.assertEqual(temporary.version, 2)
        self.assertEqual(temporary.status, "temporarily_unavailable")
        self.assertEqual(self.stack.list_reports(status="open"), [])
        self.assertEqual(self.stack.list_reports(status="temporarily_unavailable"), [temporary])
        self.assertEqual(
            self.stack.review_report(
                first.report_id, moderator="reviewer-1", status="no_answer", expected_version=0, decision_id=token,
            ),
            reviewed,
        )
        with self.assertRaises(ModerationConflict):
            self.stack.review_report(
                first.report_id, moderator="reviewer-1", status="wrong_details", expected_version=0, decision_id=token,
            )
        self.assertEqual(self.stack.get_report(first.report_id), temporary)
        latest = self.stack.review_report(
            first.report_id, moderator="reviewer-2", status="open", expected_version=2,
        )
        self.assertEqual(latest.status, "open")
        history = self.stack.list_decisions(first.report_id)
        self.assertEqual(
            [(row.previous_status, row.status, row.version) for row in history],
            [("open", "no_answer", 1), ("no_answer", "temporarily_unavailable", 2),
             ("temporarily_unavailable", "open", 3)],
        )

    def test_simultaneous_moderators_cannot_overwrite_each_others_changes(self):
        report = self.stack.submit_report("fdr:024", "dialysis")
        barrier = Barrier(2)

        def review(status):
            barrier.wait(timeout=5)
            try:
                return self.stack.review_report(
                    report.report_id, moderator=status, status=status, expected_version=report.version,
                )
            except ModerationConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, ("not_offered", "wrong_details")))
        winners = [row for row in results if row is not None]
        self.assertEqual(winners, [self.stack.get_report(report.report_id)])
        self.assertEqual(len(self.stack.list_decisions(report.report_id)), 1)

    def test_saved_decisions_are_append_only_even_when_sql_requests_replacement(self):
        report = self.stack.submit_report("fdr:024", "mri")
        self.stack.review_report(report.report_id, moderator="reviewer", status="dismissed", expected_version=0)
        with closing(sqlite3.connect(self.path)) as conn:
            for sql in (
                "UPDATE report_decisions SET moderator = 'other'",
                "DELETE FROM report_decisions",
                "INSERT OR REPLACE INTO report_decisions SELECT * FROM report_decisions",
            ):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    conn.execute(sql)
        self.assertEqual(self.stack.list_decisions(report.report_id)[0].moderator, "reviewer")

    def test_report_only_accepts_catalog_identifiers_and_fixed_reasons(self):
        for facility, service, reason, token in (
            ("a caller's address", "dialysis", "poor_fit", None),
            ("a@example.org", "dialysis", "poor_fit", None),
            ("fdr:001", "another service", "poor_fit", None),
            ("fdr:001", "uncatalogued_service", "poor_fit", None),
            ("fdr:001", "dialysis", "comment from caller", None),
            ("fdr:001", "dialysis", "poor_fit", "personal-contact"),
        ):
            with self.subTest(facility=facility, service=service, reason=reason), self.assertRaises(ValueError):
                self.stack.submit_report(facility, service, reason=reason, submission_id=token)
        with self.assertRaises(KeyError):
            self.stack.list_decisions(42)
        self.assertEqual(self.stack.list_reports(), [])

    def test_operator_cli_reads_open_stack_and_records_decision_history(self):
        older = self.stack.submit_report("fdr:001", "dialysis")
        newest = self.stack.submit_report("fdr:024", "mri")
        script = Path(__file__).resolve().parents[1] / "moderate_reports.py"

        def command(*arguments):
            result = subprocess.run(
                [sys.executable, str(script), "--db", str(self.path), *arguments],
                check=True, capture_output=True, text=True, timeout=15,
            )
            return json.loads(result.stdout)

        self.assertEqual([r["report_id"] for r in command("list")], [newest.report_id, older.report_id])
        review = command(
            "review", str(newest.report_id), "wrong_details", "--moderator", "reviewer",
            "--expected-version", str(newest.version),
        )
        self.assertEqual(review["status"], "wrong_details")
        self.assertEqual([r["report_id"] for r in command("list")], [older.report_id])
        self.assertEqual(command("history", str(newest.report_id))[0]["version"], 1)


if __name__ == "__main__":
    unittest.main()
