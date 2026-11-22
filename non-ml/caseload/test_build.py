"""Regression checks for the caseload dataset and snapshot builder."""

import ast
import json
import unittest
from pathlib import Path

from build import DATA, backtest, build, estimate, month_index, monthly_counts


class CaseloadTests(unittest.TestCase):
    def test_monthly_cutoff_preserves_zero_and_excludes_gaps(self):
        series = {
            "id": "example",
            "monthly": [{"start": "2025-01", "counts": [0, None, 20, 40, 500]}],
        }
        result = estimate(series, month_index("2025-05"))
        self.assertEqual(result["typical_month"], 20)
        self.assertEqual(result["observed_monthly_min"], 0)
        self.assertEqual(result["observed_monthly_max"], 40)
        self.assertEqual(result["reported_months"], 3)
        self.assertEqual(result["months_since_last_reported_period"], 0)

    def test_estimate_uses_a_twelve_calendar_month_window(self):
        series = {
            "id": "example",
            "monthly": [
                {"start": "2024-01", "counts": [500]},
                {"start": "2025-01", "counts": [2, 4, 6]},
            ],
        }
        result = estimate(series, month_index("2025-10"))
        self.assertEqual(result["first_month"], "2025-01")
        self.assertEqual(result["typical_month"], 4)
        self.assertEqual(result["months_used"], 3)
        self.assertEqual(result["reported_months"], 4)
        self.assertEqual(result["months_since_last_reported_period"], 6)

    def test_period_total_uses_documented_denominator_after_close(self):
        series = {
            "id": "example",
            "period": {"start": "2022-12", "end": "2024-12", "total": 2182, "exposure_months": 24},
        }
        self.assertIsNone(estimate(series, month_index("2024-12"))["typical_month"])
        result = estimate(series, month_index("2025-01"))
        self.assertEqual(result["typical_month"], 90.92)
        self.assertEqual(result["period_months"], 24)
        self.assertEqual(result["reported_period_total"], 2182)
        self.assertIsNone(result["observed_monthly_max"])
        self.assertIsNone(result["backtest"])

    def test_publisher_mean_does_not_invent_denominator(self):
        series = {
            "id": "example",
            "period": {"start": "2024-02", "end": "2025-03", "monthly_average": 71.28},
        }
        result = estimate(series, month_index("2025-04"))
        self.assertEqual(result["method"], "publisher_reported_monthly_average")
        self.assertEqual(result["typical_month"], 71.28)
        self.assertIsNone(result["period_months"])
        self.assertEqual(result["months_used"], 0)

    def test_backtest_uses_past_data_and_shared_origins(self):
        values = monthly_counts([{"start": "2025-01", "counts": [2, 2, 2, 2, 2, 4, 100, None, 300]}])
        result = backtest(values)
        self.assertEqual(result["scored_months"], 1)
        self.assertEqual(result["first_scored_month"], "2025-07")
        self.assertEqual(result["rolling_median_mae"], 98)
        self.assertEqual(result["previous_month_mae"], 96)

    def test_duplicate_months_rejected_even_when_one_is_missing(self):
        with self.assertRaisesRegex(ValueError, "Duplicate observation"):
            monthly_counts([{"start": "2025-01", "counts": [None]}, {"start": "2025-01", "counts": [0]}])

    def test_checked_in_snapshot_matches_the_dashboard_and_manifest(self):
        data = json.loads(DATA.read_text(encoding="utf-8"))
        output = build(data, data["as_of_month"])
        saved = DATA.with_name("typical_caseload.json")
        self.assertEqual(output, json.loads(saved.read_text(encoding="utf-8")))

        dashboard = Path(__file__).resolve().parents[2] / "dashboard" / "dashboard.py"
        module = ast.parse(dashboard.read_text(encoding="utf-8"))
        services = next(
            node.value for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SERVICES" for target in node.targets)
        )
        self.assertEqual(list(output["targets"]), [ast.literal_eval(key) for key in services.keys])
        self.assertTrue(all(target["benchmarks"] for target in output["targets"].values()))

    def test_transcribed_component_sums_match_monthly_series(self):
        data = json.loads(DATA.read_text(encoding="utf-8"))
        for series in data["series"]:
            if "transformation" in series:
                with self.subTest(series=series["id"]):
                    components = series["transformation"]["components"]
                    summed = list(map(sum, zip(*(row["counts"] for row in components), strict=True)))
                    self.assertEqual(summed, series["monthly"][0]["counts"])


if __name__ == "__main__":
    unittest.main()
