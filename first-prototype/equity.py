"""Opt-in ranking from reviewed, branch- and service-specific evidence.

The scoring rules mirror non-ml/src/facility_tool/equity.py. This copy and its
reviewed evidence CSV ship with the self-contained Databricks app source folder.
"""

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from typing import Hashable, Sequence
from urllib.parse import urlsplit


MAX_EXTRA_KM = 1.0
MAX_EXTRA_MINUTES = 2.0
PAIRED_EQUITY_PATH = Path(__file__).with_name("paired_equity_evidence.csv")
MIN_EQUITY_DENOMINATOR = 30


@dataclass(frozen=True)
class EquityCandidate:
    facility_id: str
    tier: Hashable
    distance_km: float
    route_km: float | None = None
    minutes: float | None = None
    route_context: Hashable | None = None


def _within_detour(candidate: EquityCandidate, previous: EquityCandidate) -> bool:
    if not all(isfinite(km) and km >= 0 for km in (candidate.distance_km, previous.distance_km)):
        return False
    if candidate.distance_km - previous.distance_km > MAX_EXTRA_KM:
        return False
    if candidate.route_context != previous.route_context:
        return True
    for field, cap in (("route_km", MAX_EXTRA_KM), ("minutes", MAX_EXTRA_MINUTES)):
        new, old = getattr(candidate, field), getattr(previous, field)
        if new is not None and old is not None and new - old > cap:
            return False
    return True


@dataclass(frozen=True)
class PairedEquityEvidence:
    """Two favorable rates from a common eligible cohort and a reviewed source."""

    facility_id: str
    service: str
    comparison_id: str
    period_start: date
    period_end: date
    population: str
    outcome_measure: str
    respect_measure: str
    adjustment_method: str
    outcome_rate: float
    outcome_lower: float
    outcome_upper: float
    outcome_n: int
    respect_rate: float
    respect_lower: float
    respect_upper: float
    respect_n: int
    source_title: str
    source_url: str
    checked_on: date
    valid_until: date
    fictional: bool = False

    @property
    def comparison_key(self) -> tuple:
        return (
            self.comparison_id, self.period_start, self.period_end, self.population,
            self.outcome_measure, self.respect_measure, self.adjustment_method, self.fictional,
        )


@dataclass(frozen=True)
class EquityScore:
    evidence: PairedEquityEvidence
    value: float
    lower: float
    upper: float


@dataclass(frozen=True)
class PairedEquityRanking:
    order: tuple[int, ...]
    scores: dict[int, EquityScore]
    status: str


def _validate_paired_evidence(record: PairedEquityEvidence) -> None:
    if not all((record.facility_id, record.service, record.comparison_id, record.population,
                record.outcome_measure, record.respect_measure, record.adjustment_method, record.source_title)):
        raise ValueError("the identity, population, measurements and source need a value")
    if not record.period_start <= record.period_end <= record.checked_on <= record.valid_until:
        raise ValueError("the cohort dates, source check and review deadline must be in chronological order")
    adjustment = record.adjustment_method.strip().casefold()
    if adjustment in {"", "none", "unknown", "n/a", "unadjusted"}:
        raise ValueError("the outcome needs a shared clinical risk-adjustment method")
    if not record.fictional:
        source = urlsplit(record.source_url)
        if source.scheme != "https" or not source.hostname:
            raise ValueError("a reviewed public HTTPS source is required")
    for rate, lower, upper, denominator in (
        (record.outcome_rate, record.outcome_lower, record.outcome_upper, record.outcome_n),
        (record.respect_rate, record.respect_lower, record.respect_upper, record.respect_n),
    ):
        if not 0 <= lower <= rate <= upper <= 1 or lower == upper:
            raise ValueError("favorable outcome and respect rates need uncertainty bounds between 0 and 1")
        if type(denominator) is not int or denominator < 1:
            raise ValueError("each measurement needs a positive integer denominator")


def load_paired_equity_evidence(path: str | Path | None = None) -> tuple[PairedEquityEvidence, ...]:
    """Read curated cohort data. Each branch has at most one record per service."""
    path = PAIRED_EQUITY_PATH if path is None else Path(path)
    columns = tuple(name for name in PairedEquityEvidence.__dataclass_fields__ if name != "fictional")
    records = []
    keys = set()
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or set(reader.fieldnames) != set(columns) or len(reader.fieldnames) != len(columns):
            raise ValueError(f"Paired equity evidence CSV must have these columns: {', '.join(columns)}.")
        for line, row in enumerate(reader, 2):
            try:
                if None in row or not all(row[field] and row[field].strip() for field in columns):
                    raise ValueError("all columns need a value")
                values = {field: row[field].strip() for field in columns}
                for field in ("period_start", "period_end", "checked_on", "valid_until"):
                    values[field] = date.fromisoformat(values[field])
                for field in ("outcome_rate", "outcome_lower", "outcome_upper", "respect_rate", "respect_lower", "respect_upper"):
                    values[field] = float(values[field])
                for field in ("outcome_n", "respect_n"):
                    values[field] = int(values[field])
                record = PairedEquityEvidence(**values)
                _validate_paired_evidence(record)
                key = record.facility_id, record.service
                if key in keys:
                    raise ValueError("expected one reviewed record per branch and service")
            except ValueError as exc:
                raise ValueError(f"Paired equity evidence CSV row {line}: {exc}.") from exc
            keys.add(key)
            records.append(record)
    return tuple(records)


def rank_for_paired_equity(
    candidates: Sequence[EquityCandidate],
    service: str,
    enabled: bool = False,
    evidence: Sequence[PairedEquityEvidence] = (),
    *,
    as_of: date | None = None,
    urgent: bool = False,
) -> PairedEquityRanking:
    """Break close ranking ties only with a comparable and clearly higher equity score."""
    order = list(range(len(candidates)))
    if not enabled or urgent:
        return PairedEquityRanking(tuple(order), {}, "urgent" if enabled else "off")

    as_of = as_of or date.today()
    by_facility = defaultdict(list)
    for record in evidence:
        if record.service == service:
            by_facility[record.facility_id].append(record)

    id_counts = Counter(candidate.facility_id for candidate in candidates)
    available = {}
    for index, candidate in enumerate(candidates):
        source_records = by_facility.get(candidate.facility_id, ())
        if not candidate.facility_id or candidate.tier is None or id_counts[candidate.facility_id] != 1 or len(source_records) != 1:
            continue
        record = source_records[0]
        try:
            _validate_paired_evidence(record)
        except ValueError:
            continue
        if not record.checked_on <= as_of <= record.valid_until:
            continue
        if min(record.outcome_n, record.respect_n) < MIN_EQUITY_DENOMINATOR:
            continue
        available[index] = EquityScore(
            record, 50 * (record.outcome_rate + record.respect_rate),
            50 * (record.outcome_lower + record.respect_lower), 50 * (record.outcome_upper + record.respect_upper),
        )
    if not available:
        return PairedEquityRanking(tuple(order), {}, "no_evidence")

    scores = {}
    changed = False
    for position, index in enumerate(order):
        if index not in available:
            continue
        candidate, current_score = candidates[index], available[index]
        target = position
        while target:
            prior_index = order[target - 1]
            prior = candidates[prior_index]
            prior_score = available.get(prior_index)
            if (prior_score is None or prior.tier != candidate.tier
                    or current_score.evidence.comparison_key != prior_score.evidence.comparison_key
                    or not _within_detour(candidate, prior)):
                break
            scores[index] = current_score
            scores[prior_index] = prior_score
            if current_score.lower <= prior_score.upper:
                break
            target -= 1
        if target != position:
            order.insert(target, order.pop(position))
            changed = True
    if not scores:
        return PairedEquityRanking(tuple(order), {}, "incomparable")
    return PairedEquityRanking(tuple(order), scores, "reranked" if changed else "matched")


def synthetic_equity_evidence(
    service: str, baseline_id: str, higher_score_id: str, *, as_of: date | None = None,
) -> tuple[PairedEquityEvidence, PairedEquityEvidence]:
    """Two clearly fictional records for trying the feature in demo mode."""
    as_of = as_of or date.today()
    outcome_measure = {
        "maternity_delivery": "No serious maternal complication within 30 days of delivery",
        "dialysis": "No unplanned admission within 30 days of starting dialysis",
    }.get(service, "No serious complication within 30 days of treatment")
    common = dict(
        service=service, comparison_id="fictional-equity-demo", period_start=as_of - timedelta(days=180),
        period_end=as_of - timedelta(days=35), population="Fictional adults receiving the requested service",
        outcome_measure=outcome_measure, respect_measure="Reported always being treated with dignity and respect",
        adjustment_method="Fictional risk standardization to a common case mix", outcome_n=500, respect_n=500,
        source_title="Fictional demonstration record", source_url="", checked_on=as_of,
        valid_until=as_of, fictional=True,
    )
    baseline = PairedEquityEvidence(
        facility_id=baseline_id, outcome_rate=0.82, outcome_lower=0.79, outcome_upper=0.85,
        respect_rate=0.60, respect_lower=0.55, respect_upper=0.65, **common,
    )
    higher = PairedEquityEvidence(
        facility_id=higher_score_id, outcome_rate=0.95, outcome_lower=0.92, outcome_upper=0.98,
        respect_rate=0.91, respect_lower=0.88, respect_upper=0.94, **common,
    )
    return baseline, higher
