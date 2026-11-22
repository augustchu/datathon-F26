import csv
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from facility_tool.equity import (
    EquityCandidate, load_paired_equity_evidence, rank_for_paired_equity, synthetic_equity_evidence,
)


class PairedEquityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 21)
        self.evidence = synthetic_equity_evidence("dialysis", "first", "near", as_of=self.day)
        self.candidates = (EquityCandidate("first", 1, 0.2), EquityCandidate("near", 1, 0.8))

    def rank(self, *, evidence=None, candidates=None, as_of=None, enabled=True, urgent=False):
        return rank_for_paired_equity(
            self.candidates if candidates is None else candidates, "dialysis", enabled,
            self.evidence if evidence is None else evidence, as_of=as_of or self.day, urgent=urgent,
        )

    def test_rerank_combines_outcomes_and_respect_for_comparable_peers(self):
        result = self.rank()
        self.assertEqual((result.order, result.status), ((1, 0), "reranked"))
        self.assertAlmostEqual(result.scores[0].value, 71)
        self.assertAlmostEqual(result.scores[1].value, 93)
        self.assertTrue(result.scores[1].evidence.fictional)

    def test_uncertain_difference_preserves_order(self):
        first, second = self.evidence
        wide = replace(second, outcome_lower=0.60, respect_lower=0.60)
        result = self.rank(evidence=(first, wide))
        self.assertEqual((result.order, result.status), ((0, 1), "matched"))
        self.assertEqual(set(result.scores), {0, 1})

    def test_off_urgent_or_no_evidence_preserves_order_and_hides_scores(self):
        self.assertEqual(load_paired_equity_evidence(), ())
        for status, options in (
            ("off", {"enabled": False}), ("urgent", {"urgent": True}), ("no_evidence", {"evidence": ()}),
            ("no_evidence", {"as_of": self.day + timedelta(days=1)}),
        ):
            with self.subTest(status=status, options=options):
                result = self.rank(**options)
                self.assertEqual((result.order, result.scores, result.status), ((0, 1), {}, status))

    def test_refuses_unmatched_service_window_population_or_insufficient_data(self):
        first, second = self.evidence
        for field, value in (
            ("facility_id", "another branch"), ("service", "mri"), ("comparison_id", "other study"),
            ("period_start", self.day - timedelta(days=130)), ("population", "Different case mix"),
            ("outcome_measure", "Other clinical outcome"), ("respect_measure", "Overall satisfaction"),
            ("adjustment_method", "Unadjusted"), ("outcome_n", 12), ("respect_n", 12), ("outcome_lower", 0.99),
        ):
            with self.subTest(field=field):
                result = self.rank(evidence=(first, replace(second, **{field: value})))
                self.assertEqual((result.order, result.scores, result.status), ((0, 1), {}, "incomparable"))

    def test_no_score_when_only_peers_differ_in_tier_or_are_too_far(self):
        first, second = [replace(candidate, route_km=4, minutes=15) for candidate in self.candidates]
        for field, value in (("tier", 2), ("distance_km", 1.3), ("route_km", 5.1), ("minutes", 17.1)):
            with self.subTest(field=field):
                result = self.rank(candidates=(first, replace(second, **{field: value})))
                self.assertEqual((result.order, result.scores, result.status), ((0, 1), {}, "incomparable"))

    def test_no_promotion_past_an_option_without_comparable_data(self):
        first, second = self.candidates
        result = self.rank(candidates=(first, EquityCandidate("unknown", 1, 0.4), second))
        self.assertEqual((result.order, result.scores, result.status), ((0, 1, 2), {}, "incomparable"))

    def test_ambiguous_branch_or_study_records_cannot_receive_a_score(self):
        first, second = self.candidates
        duplicate = self.rank(candidates=(first, second, second))
        conflicting = self.rank(evidence=(*self.evidence, self.evidence[1]))
        self.assertEqual((duplicate.order, duplicate.scores), ((0, 1, 2), {}))
        self.assertEqual((conflicting.order, conflicting.scores), ((0, 1), {}))

    def test_load_reviewed_csv_and_reject_duplicate_branch_service(self):
        record = replace(self.evidence[0], source_url="https://example.invalid/report", fictional=False)
        values = asdict(record)
        values.pop("fictional")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "paired.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(values))
                writer.writeheader()
                writer.writerow(values)
            self.assertEqual(load_paired_equity_evidence(path), (record,))
            with path.open("a", encoding="utf-8", newline="") as file:
                csv.DictWriter(file, fieldnames=list(values)).writerow(values)
            with self.assertRaisesRegex(ValueError, "one reviewed record per branch and service"):
                load_paired_equity_evidence(path)
