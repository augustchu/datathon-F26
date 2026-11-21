"""Store a newest-first moderation stack and append-only decisions in SQLite."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from numbers import Integral
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4


REPORT_REASONS = ("service_not_confirmed", "poor_fit")
REPORT_STATUSES = (
    "open", "not_offered", "temporarily_unavailable", "wrong_details", "no_answer", "dismissed",
)
_MAX_ID = 2**63 - 1
_FACILITY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_SERVICE_KEY = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_MODERATOR_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")


class ModerationConflict(ValueError):
    """The requested change has a stale revision or conflicts with a retry token."""


@dataclass(frozen=True, slots=True)
class Report:
    report_id: int
    facility_id: str
    service: str
    reason: str
    status: str
    version: int
    submitted_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Decision:
    report_id: int
    moderator: str
    previous_status: str
    status: str
    version: int
    reviewed_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS facility_reports (
    report_id INTEGER PRIMARY KEY,
    submission_id TEXT NOT NULL UNIQUE,
    facility_id TEXT NOT NULL,
    service TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('service_not_confirmed', 'poor_fit')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'not_offered', 'temporarily_unavailable', 'wrong_details', 'no_answer', 'dismissed')),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS report_status_page ON facility_reports(status, report_id);
CREATE INDEX IF NOT EXISTS report_facility_page ON facility_reports(facility_id, service, report_id);

CREATE TABLE IF NOT EXISTS report_decisions (
    report_id INTEGER NOT NULL REFERENCES facility_reports(report_id),
    decision_id TEXT NOT NULL UNIQUE,
    moderator TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    reviewed_at TEXT NOT NULL,
    PRIMARY KEY(report_id, version)
);

CREATE TRIGGER IF NOT EXISTS decision_no_update
BEFORE UPDATE ON report_decisions
BEGIN SELECT RAISE(ABORT, 'Moderator decisions are append-only'); END;

CREATE TRIGGER IF NOT EXISTS decision_no_delete
BEFORE DELETE ON report_decisions
BEGIN SELECT RAISE(ABORT, 'Moderator decisions are append-only'); END;

CREATE TRIGGER IF NOT EXISTS decision_no_replace
BEFORE INSERT ON report_decisions
WHEN EXISTS (
    SELECT 1 FROM report_decisions
    WHERE decision_id = NEW.decision_id OR (report_id = NEW.report_id AND version = NEW.version)
)
BEGIN SELECT RAISE(ABORT, 'Moderator decisions are append-only'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _identifier(value, pattern, label, max_length):
    if not isinstance(value, str) or len(value) > max_length or pattern.fullmatch(value) is None:
        raise ValueError(f"Provide a valid {label}.")
    return value


def _integer(value, label, minimum=1, maximum=_MAX_ID):
    if isinstance(value, bool) or not isinstance(value, Integral) or not minimum <= value <= maximum:
        raise ValueError(f"Provide a valid {label}.")
    return int(value)


def _request_id(value):
    if value is None:
        return str(uuid4())
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Use a randomly generated UUID version 4 for the retry ID.") from None
    if parsed.version != 4:
        raise ValueError("Use a randomly generated UUID version 4 for the retry ID.")
    return str(parsed)


def _report(row):
    return Report(**{key: row[key] for key in Report.__dataclass_fields__})


def _decision(row):
    return Decision(**{key: row[key] for key in Decision.__dataclass_fields__})


class ModerationStack:
    """A file-backed moderation stack for one deployment on a shared local disk.

    The caller supplies a canonical lookup service and a facility ID from its
    results. Reasons and outcomes are fixed options. Access control belongs to
    the host application or local file permissions. A moderator uses an
    internal account key. Decisions have no effect on ranking or training.
    """

    def __init__(self, path: str | Path, *, allowed_services: Iterable[str] | None = None):
        """Open a persistent SQLite file and optionally restrict service keys."""
        if str(path) in ("", ":memory:"):
            raise ValueError("Provide a path to a persistent moderation database.")
        self.path = Path(path).expanduser().resolve()
        self.allowed_services = None if allowed_services is None else frozenset(
            _identifier(service, _SERVICE_KEY, "canonical service key", 80) for service in allowed_services
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _service(self, value):
        service = _identifier(value, _SERVICE_KEY, "canonical service key", 80)
        if self.allowed_services is not None and service not in self.allowed_services:
            raise ValueError("Choose a service from the configured catalog.")
        return service

    @staticmethod
    def _report_by_id(conn, report_id):
        row = conn.execute("SELECT * FROM facility_reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            raise KeyError(f"Report {report_id} was not found.")
        return _report(row)

    def submit_report(
        self, facility_id: str, service: str, *, reason: str = "service_not_confirmed",
        submission_id: str | UUID | None = None,
    ) -> Report:
        """Save one observation. Supply the same random UUID on retry."""
        facility_id = _identifier(facility_id, _FACILITY_ID, "facility ID", 256)
        service = self._service(service)
        if reason not in REPORT_REASONS:
            raise ValueError(f"Choose a report reason from {', '.join(REPORT_REASONS)}.")
        request_id = _request_id(submission_id)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            conn.execute(
                """
                INSERT INTO facility_reports (submission_id, facility_id, service, reason, submitted_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(submission_id) DO NOTHING
                """,
                (request_id, facility_id, service, reason, now, now),
            )
            row = conn.execute("SELECT * FROM facility_reports WHERE submission_id = ?", (request_id,)).fetchone()
            report = _report(row)
            if (report.facility_id, report.service, report.reason) != (facility_id, service, reason):
                raise ModerationConflict("This submission ID already belongs to a different report.")
        return report

    def get_report(self, report_id: int) -> Report:
        report_id = _integer(report_id, "report ID")
        with closing(self._connect()) as conn:
            return self._report_by_id(conn, report_id)

    def list_reports(
        self, *, status: str | None = "open", facility_id: str | None = None,
        service: str | None = None, before_id: int | None = None, limit: int = 100,
    ) -> list[Report]:
        """Read open reports newest first. Pass status=None for all reports.

        A retry retains its original position. Use the lowest report ID on the
        returned page as the next cursor, with the same filters.
        """
        limit = _integer(limit, "page size (1 through 500)", maximum=500)
        where, params = [], []
        if status is not None:
            if status not in REPORT_STATUSES:
                raise ValueError(f"Choose a report status from {', '.join(REPORT_STATUSES)}.")
            where.append("status = ?")
            params.append(status)
        if facility_id is not None:
            where.append("facility_id = ?")
            params.append(_identifier(facility_id, _FACILITY_ID, "facility ID", 256))
        if service is not None:
            where.append("service = ?")
            params.append(_identifier(service, _SERVICE_KEY, "canonical service key", 80))
        if before_id is not None:
            where.append("report_id < ?")
            params.append(_integer(before_id, "report cursor"))
        filter_sql = " WHERE " + " AND ".join(where) if where else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM facility_reports{filter_sql} ORDER BY report_id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_report(row) for row in rows]

    def review_report(
        self, report_id: int, *, moderator: str, status: str, expected_version: int,
        decision_id: str | UUID | None = None,
    ) -> Report:
        """Apply a decision to the version reviewed and append the change to history.

        Keep the decision UUID and original arguments to retry the same review.
        It returns the revision applied by that decision. Use get_report for the
        latest state after later reviews.
        """
        report_id = _integer(report_id, "report ID")
        expected_version = _integer(expected_version, "expected report version", minimum=0)
        moderator = _identifier(moderator, _MODERATOR_KEY, "moderator account key", 80)
        if status not in REPORT_STATUSES:
            raise ValueError(f"Choose a report status from {', '.join(REPORT_STATUSES)}.")
        request_id = _request_id(decision_id)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            report = self._report_by_id(conn, report_id)
            prior = conn.execute("SELECT * FROM report_decisions WHERE decision_id = ?", (request_id,)).fetchone()
            if prior is not None:
                decision = _decision(prior)
                existing = (decision.report_id, decision.moderator, decision.status, decision.version)
                if existing != (report_id, moderator, status, expected_version + 1):
                    raise ModerationConflict("This decision ID already belongs to a different review.")
                return replace(report, status=decision.status, version=decision.version, updated_at=decision.reviewed_at)
            if report.version != expected_version:
                raise ModerationConflict(f"Report {report_id} is at version {report.version}. Reload it before reviewing.")
            if report.status == status:
                raise ModerationConflict("Choose an outcome different from the current status.")
            now, version = _now(), report.version + 1
            conn.execute(
                "UPDATE facility_reports SET status = ?, version = ?, updated_at = ? WHERE report_id = ?",
                (status, version, now, report_id),
            )
            conn.execute(
                """
                INSERT INTO report_decisions (report_id, decision_id, moderator, previous_status, status, version, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (report_id, request_id, moderator, report.status, status, version, now),
            )
        return replace(report, status=status, version=version, updated_at=now)

    def list_decisions(self, report_id: int) -> list[Decision]:
        """Return the chronological decision history for a report."""
        report_id = _integer(report_id, "report ID")
        with closing(self._connect()) as conn:
            self._report_by_id(conn, report_id)
            rows = conn.execute(
                "SELECT * FROM report_decisions WHERE report_id = ? ORDER BY version", (report_id,),
            ).fetchall()
        return [_decision(row) for row in rows]
