"""Checks for scoped peer scenarios and transparent validation of source cohorts."""

import math
import unittest

from network_model import ROOT, leave_one_site_out, load_profiles, nearest_rank_p90, profile_predictions, read_json


class NetworkModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()

    def test_profiles_cover_the_current_care_options(self):
        targets = read_json(ROOT / "observations.json")["targets"]
        self.assertEqual(set(targets), set(self.profiles))
        self.assertEqual(len(self.profiles), 18)
        for service, profile in self.profiles.items():
            with self.subTest(service=service):
                self.assertTrue(profile["scope"] and profile["scope_limit"] and profile["unit"])
                self.assertTrue(0 < profile["peer_median_month"] <= profile["observed_p90_month"])
                self.assertTrue(math.isfinite(profile["observed_p90_month"]))
                for row in profile["benchmark_rows"]:
                    self.assertTrue(row["facility"] and row["region"] and row["series_id"])
                    self.assertTrue(row["source"]["url"] and row["source_observations"])
                    self.assertLess(row["period_end"], profile["as_of_month"])
                    self.assertLessEqual(row["period_start"], row["period_end"])

    def test_nonmatching_anchor_definitions_are_excluded(self):
        incompatible = {
            "Emergency C-section": {"gims_emergency_cs"},
            "NICU": {"bilaspur_nicu"},
            "Dialysis": {"igims_dialysis", "ipgmer_dialysis"},
            "Cardiac surgery": {"unmehta_ayushman_surgery", "sctimst_cardiac_ot"},
            "Cardiology consultation": {"lok_nayak_pediatric_cardiology"},
            "Burn care": {"safdarjung_burn"},
            "Oral surgery": {"index_oral"},
            "Orthopedic surgery": {"bilaspur_ortho"},
            "Stroke care": {"faridkot_stroke", "sctimst_stroke_thrombectomy"},
            "Chemotherapy": {"bhubaneswar_chemo", "cochin_chemo"},
            "Trauma / emergency": {"bilaspur_trauma", "aiims_nagpur_emergency_monthly"},
        }
        for service, excluded in incompatible.items():
            with self.subTest(service=service):
                profile = self.profiles[service]
                included = {row["series_id"] for row in profile["benchmark_rows"]}
                self.assertFalse(excluded & (included | set(profile["replay_series_ids"])))

    def test_leave_one_site_out_uses_other_sites_and_reports_singleton_limit(self):
        rows = [
            {"series_id": site, "facility": site, "typical_month": count}
            for site, count in [("A", 10), ("B", 20), ("C", 100)]
        ]
        validation = leave_one_site_out(rows)
        self.assertEqual(validation["scored_sites"], 3)
        self.assertEqual(validation["peer_count_per_fold"], 2)
        self.assertEqual([row["prediction"] for row in validation["rows"]], [60, 55, 15])
        self.assertAlmostEqual(validation["mae"], (50 + 35 + 85) / 3)
        self.assertEqual(leave_one_site_out(rows[:1])["scored_sites"], 0)
        self.assertIsNone(leave_one_site_out(rows[:1])["mae"])

    def test_compact_benchmarks_and_nearest_rank(self):
        compact = profile_predictions(self.profiles)
        self.assertEqual(set(compact), set(self.profiles))
        self.assertEqual(nearest_rank_p90(range(1, 11)), 9)
        self.assertEqual(nearest_rank_p90([7]), 7)
        self.assertIsNone(nearest_rank_p90([]))
        self.assertTrue(compact["Stroke care"]["singleton_anchor"])
        self.assertNotIn("benchmark_rows", compact["Stroke care"])
        self.assertEqual(compact["Stroke care"]["peer_median_month"], compact["Stroke care"]["observed_p90_month"])
        for service, profile in self.profiles.items():
            self.assertEqual(compact[service]["unit"], profile["unit"])

    def test_programme_inventory_and_better_baselines_are_kept_separate(self):
        for service, expected_sites, resource in [("Dialysis", 24, "machines"), ("Maternity / delivery", 94, "beds")]:
            with self.subTest(service=service):
                profile = self.profiles[service]
                self.assertEqual(len(profile["benchmark_rows"]), expected_sites)
                self.assertTrue(all(row["reported_resource"]["field"] == resource for row in profile["benchmark_rows"]))
                prior = profile["diagnostics"]["prior_resource_baselines"]
                fold = "later_period_unseen_district" if service == "Dialysis" else "unseen_district"
                metrics = prior["evaluation"][fold]["metrics"]
                self.assertLess(metrics["capacity_scaled_median"]["mae"], metrics["xgboost"]["mae"])


if __name__ == "__main__":
    unittest.main()
