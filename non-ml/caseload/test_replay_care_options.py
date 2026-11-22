import unittest

from replay_care_options import aligned_caseloads, attach_throughput_capacity, replay_groups, run_order


class ReplayCareOptionsTests(unittest.TestCase):
    def setUp(self):
        self.source = {"title": "Published workload", "url": "https://example.org/workload", "locator": "Table 1"}
        self.release = {
            ident: [{"facility_id": ident, "name": ident, "address": "Test address", "city": "Test city",
                     "district": "Test district", "state": "Test state", "pin": "110001",
                     "latitude": "28.0", "longitude": str(lon), "routing_eligible": "true"}]
            for ident, lon in (("a", 77.0), ("b", 77.01))
        }

    def test_alignment_keeps_observation_and_flags_same_site_forecast(self):
        def series(ident, site, period, values):
            return {"id": ident, "site": site, "service": "MRI", "scope": "Hospital MRI",
                    "unit": "scans", "grain": "month",
                    "observations": [{"source": "report", "start": "2025-01", "counts": values}],
                    "targets": [{"period": period, "basis": "Reviewed report"}]}

        manifest = {
            "sources": {"report": self.source},
            "sites": {ident: {"facility": ident, "facility_id": ident, "release_pin": "110001",
                              "state": "Test state", "identity_url": self.source["url"],
                              "identity_basis": "Exact branch"} for ident in ("a", "b")},
            "series": [series("sgrd_mri", "a", "2025-10", [10] * 9 + [30]),
                       series("unmehta_mri_monthly", "b", "2025-09", [20] * 9)],
        }
        rows = aligned_caseloads([manifest], self.release, "2026-01")
        october = {row["facility_id"]: row for row in rows if row["period_start"] == "2025-10"}
        self.assertEqual((october["a"]["caseload"], october["a"]["caseload_predicted"]), (30, False))
        self.assertEqual((october["b"]["caseload"], october["b"]["caseload_predicted"]), (20, True))
        self.assertTrue(october["b"]["aligned_for_replay"])
        self.assertIsNone(october["b"]["capacity_predicted"])
        self.assertEqual(replay_groups(rows, self.release)[0]["status"], "no_comparable_capacity")

    def test_real_replay_paths_record_moves_and_flags(self):
        sites = [dict(values[0], lat=28.0, lon=float(values[0]["longitude"]),
                      location_quality="source_coordinates_unverified") for values in self.release.values()]
        rows = [dict(facility_id=ident, series_id=ident, scope="Hospital MRI exams", caseload_source=self.source,
                     unit="scans", caseload=count, caseload_predicted=predicted, capacity=None,
                     capacity_predicted=None, period_start="2025-04", period_end="2026-03")
                for ident, predicted, count in (("a", False, 950), ("b", True, 200))]
        capacities = [dict(facility_id=ident, series_id=ident, unit="scans", period_start="2025-04",
                           period_end="2026-03", capacity=1000, capacity_predicted=predicted,
                           capacity_source=self.source, capacity_method="Reviewed throughput model" if predicted else None)
                      for ident, predicted in (("a", False), ("b", True))]
        with self.assertRaisesRegex(ValueError, "must match"):
            attach_throughput_capacity(rows, [dict(capacities[0], unit="beds")])
        attach_throughput_capacity(rows, capacities)
        result = run_order(rows, sites, {"lat": 28.0, "lon": 77.0}, "mri", "Hospital MRI")
        self.assertTrue(result["compared"])
        self.assertEqual([(row["facility_id"], row["before"], row["after"]) for row in result["rank_changes"]],
                         [("b", 2, 1), ("a", 1, 2)])
        self.assertTrue(result["planning_order"][0]["caseload_predicted"])
        self.assertTrue(result["planning_order"][0]["capacity_predicted"])
        self.assertFalse(result["planning_order"][1]["capacity_predicted"])
