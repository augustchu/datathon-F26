"""Regression checks for baseline coverage and the exact branch join."""

import unittest

from baseline_coverage import index_release, read_json, run_audit


class BaselineCoverageTests(unittest.TestCase):
    def setUp(self):
        self.hp = read_json("hp_dialysis_model_data.json")
        self.tn = read_json("tn_maternity_model_data.json")
        self.comparisons = read_json("xgboost_results.json")
        self.targets = read_json("observations.json")["targets"]
        self.facilities = [
            self.row(link["fdr_id"], link["pin"]) for link in self.hp["facility_links"].values()
        ]

    @staticmethod
    def row(facility_id, pin, *, route=True, services="", status="empty"):
        return {
            "facility_id": facility_id, "pin": pin, "state": "Himachal Pradesh",
            "name": facility_id, "routing_eligible": str(route), "documented_services": services,
            "services_evidence_status": status,
        }

    def audit(self):
        return run_audit(self.facilities, self.targets, self.hp, self.tn, self.comparisons)

    def test_counts_distinct_canonical_ids_and_uses_separate_programme_evidence(self):
        self.facilities.extend([self.facilities[0].copy(), self.row("", "174001")])
        result = self.audit()
        self.assertEqual(result["dataset"]["source_rows"], 7)
        self.assertEqual(result["dataset"]["unique_facility_ids"], 5)
        self.assertEqual(result["dataset"]["additional_rows_with_repeated_id"], 1)
        self.assertEqual(result["dataset"]["rows_without_facility_id"], 1)
        self.assertEqual(result["unique_project_facilities_with_fills"], 5)
        self.assertEqual(len(result["summary"]), 18)
        self.assertEqual(sum(item["baseline_fills"] for item in result["summary"]), 5)
        self.assertEqual(result["summary"][0]["release_documented_sites"], 0)
        self.assertEqual(result["validation"]["Maternity / delivery"]["programme_sites_with_observed_target"], 94)
        bilaspur = next(row for row in result["estimates"] if row["programme_site_id"] == "hp_pmndp:bilaspur")
        self.assertEqual(bilaspur["estimated_typical_month"], 256.50)
        self.assertEqual(bilaspur["historical_error_band"], [144.25, 368.75])

    def test_requires_same_pin_and_routing_eligible_site(self):
        self.facilities[0]["pin"] = "000000"
        self.facilities[1]["routing_eligible"] = "False"
        result = self.audit()
        self.assertEqual(result["unique_project_facilities_with_fills"], 3)
        reasons = {row["reason"] for row in result["programme_dialysis_candidates_withheld"]}
        self.assertIn("project_identity_or_pin_mismatch", reasons)
        self.assertIn("project_location_hold", reasons)

    def test_a_reported_target_is_excluded_from_the_missing_export(self):
        target = self.hp["applications"][0].copy()
        target[self.hp["columns"].index("total")] = 1234
        self.hp["observations"].append(target)
        result = self.audit()
        self.assertEqual(result["unique_project_facilities_with_fills"], 4)
        self.assertTrue(any(row["reason"] == "target_already_observed"
                            for row in result["programme_dialysis_candidates_withheld"]))

    def test_documented_services_use_exact_codes_and_distinct_ids(self):
        rows = [
            self.row("fdr:a", "123456", services="nicu | dialysis", status="verified_unchanged"),
            self.row("fdr:a", "123456", services="nicu"),
            self.row("fdr:b", "123456", services="orthopedic surgery department"),
            self.row("fdr:c", "123456", services="mri", route=False),
        ]
        _, documented, verified, _ = index_release(rows, self.targets)
        self.assertEqual(documented, {"NICU": {"fdr:a"}, "Dialysis": {"fdr:a"}})
        self.assertEqual(verified, documented)


if __name__ == "__main__":
    unittest.main()
