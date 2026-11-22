import unittest

from build import month_index
from expand_coverage import assess_target, backtest, read_series, run_expansion


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.source = {"title": "Facility census", "url": "https://example.org/census", "locator": "MRI"}
        self.sources = {"report": self.source}
        self.site = {
            "facility_id": "fdr:one", "release_pin": "000001", "state": "State",
            "identity_url": "https://example.org/campus", "identity_basis": "Exact campus",
        }
        self.release = {"fdr:one": [{"pin": "000001", "state": "State", "routing_eligible": "True"}]}
        self.series = {"id": "one_mri", "site": "one", "service": "MRI", "scope": "MRI scans",
                       "unit": "scans", "grain": "month"}
        self.cutoff = month_index("2026-01")

    def assess(self, counts, period, **target_fields):
        self.series["observations"] = [{"start": "2025-01", "source": "report", "counts": counts}]
        observed = read_series(self.series, self.sources)
        return assess_target(self.series, self.site, observed, self.sources,
                             {"period": period, "basis": "Reviewed archive", **target_fields},
                             self.cutoff, self.release)

    def test_reported_zero_takes_priority_over_working_baseline(self):
        result = self.assess([10] * 9 + [0], "2025-10")
        self.assertEqual(result["status"], "reported")
        self.assertEqual(result["reported_count"], 0)
        self.assertEqual(result["source"], self.source)
        self.assertEqual(result["backtest"]["scored_periods"], 6)

    def test_backtest_uses_same_horizon_and_prior_anchor_only(self):
        start = month_index("2025-01")
        values = {start + i: x for i, x in enumerate([10, 40, 20, 99, 199, 299, 399])}
        results = backtest(values, start + 6, 2, "month")
        self.assertEqual(results["rows"], [
            {"period": "2025-05", "actual": 199, "last_observed": 20, "recent_median": 20},
            {"period": "2025-06", "actual": 299, "last_observed": 99, "recent_median": 30},
        ])

    def test_missing_target_ignores_later_report_and_selects_lower_mae(self):
        result = self.assess([12, 12, 12, 12, 50, 12, 12, 50, 12, 12, None, 999], "2025-11")
        self.assertEqual(result["status"], "baseline_estimate")
        self.assertEqual(result["method"], "recent_median")
        self.assertEqual(result["typical_month"], 12)
        self.assertEqual(result["forecast_horizon_periods"], 1)
        self.assertEqual(result["backtest"]["rows"][-1]["period"], "2025-10")

    def test_data_with_too_few_backtests_is_withheld(self):
        result = self.assess([12] * 6, "2025-07")
        self.assertEqual(result["reason"], "insufficient_matched_horizon_tests")
        self.assertEqual(result["backtest"]["scored_periods"], 3)

    def test_listed_unreadable_report_retains_its_source_on_the_estimate(self):
        result = self.assess([10] * 8, "2025-09", gap_source="report")
        self.assertEqual(result["status"], "baseline_estimate")
        self.assertEqual(result["gap_kind"], "listed_report_unreadable")
        self.assertEqual(result["gap_source"], self.source)

    def test_annual_count_left_in_source_units_and_converted_to_monthly_rate(self):
        self.series["grain"] = "calendar_year"
        result = self.assess([240], "2025-01")
        self.assertEqual(result["reported_count"], 240)
        self.assertEqual(result["typical_month"], 20)
        self.assertEqual(result["period_end"], "2025-12")
        self.series["grain"] = "fiscal_year"
        with self.assertRaisesRegex(ValueError, "start in April"):
            self.assess([240], "2025-01")

    def test_inexact_release_link_and_open_period_are_held(self):
        self.site["release_pin"] = "000002"
        result = self.assess([240], "2025-01")
        self.assertEqual(result["reason"], "project_identity_or_pin_mismatch")
        self.site["release_pin"] = "000001"
        result = self.assess([240], "2026-01")
        self.assertEqual(result["reason"], "period_not_closed")

    def test_duplicate_project_service_period_is_rejected(self):
        self.series["observations"] = [{"start": "2025-01", "source": "report", "counts": [10]}]
        self.series["targets"] = [{"period": "2025-01", "basis": "Observed"}]
        manifest = {"sources": self.sources, "sites": {"one": self.site}, "series": [
            self.series, {**self.series, "id": "other_mri"},
        ]}
        with self.assertRaisesRegex(ValueError, "Repeated linked facility-service-period"):
            run_expansion([manifest], self.release, {"estimates": []}, ["MRI"], "2026-01")
