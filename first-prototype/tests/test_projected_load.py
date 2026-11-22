from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from classic_confidence import assess_service_confidence
from classic_data import apply_projected_load
from projected_load import load_projections


MANDI = "c259a369-d940-4fdc-a51b-1784d5966941"
KULLU = "9e64d63e-9421-4b29-ac72-dc73447a1db4"


def candidate(source_id, pin, lat, lon, distance, minutes):
    row = {
        "facility_id": f"fdr:{source_id}", "source_id": source_id, "pin": pin,
        "lat": lat, "lon": lon,
    }
    return {
        "row": row, "dist": distance, "route_minutes": minutes,
        "service_confidence": assess_service_confidence(row, "Dialysis", ("dialysis",)),
    }


class ProjectedLoadTest(TestCase):
    def setUp(self):
        self.mandi = candidate(MANDI, "175001", 31.71145, 76.92702, 16.020, 40)
        self.kullu = candidate(KULLU, "175101", 31.95667, 77.11154, 16.343, 40.5)
        self.baseline = [self.mandi, self.kullu]

    def test_reviewed_branches_can_use_the_published_projection_without_keyword_matches(self):
        for routed in (False, True):
            with self.subTest(routed=routed):
                results, count = apply_projected_load(self.baseline, "Dialysis", uses_routes=routed)
                self.assertEqual(count, 2)
                self.assertEqual(results[0]["row"]["source_id"], KULLU)
                projection = results[0]["projected_load"]
                self.assertEqual(projection["baseline_rank"], 2)
                self.assertAlmostEqual(projection["pressure"], 275.92 / (275.92 + 377.60))
                detour = 0.5 / 15 if routed else 0.323 / 10
                self.assertAlmostEqual(projection["comparison_score"], (detour + projection["pressure"]) / 2)
                self.assertNotIn("projected_load", self.baseline[0])

    def test_opt_out_urgent_and_time_critical_searches_skip_projection_reads(self):
        with patch("classic_data.prefer_projected_load") as compare:
            self.assertEqual(apply_projected_load(self.baseline, "Dialysis", uses_routes=False, enabled=False),
                             (self.baseline, 0))
            for severity in ("Urgent", "Emergency"):
                self.assertEqual(apply_projected_load(self.baseline, "Dialysis", uses_routes=False, severity=severity),
                                 (self.baseline, 0))
            for service in ("NICU", "Stroke care"):
                self.assertEqual(apply_projected_load(self.baseline, service, uses_routes=False), (self.baseline, 0))
            compare.assert_not_called()

    def test_missing_baseline_duplicate_branch_and_extra_travel_keep_baseline_order(self):
        for baseline, routed in (
            ([{**self.mandi, "row": {**self.mandi["row"], "equity_facility_id": ""}}, self.kullu], False),
            ([self.mandi, self.kullu, self.kullu.copy()], False),
            ([self.mandi, {**self.kullu, "dist": 27}], False),
            ([self.mandi, {**self.kullu, "route_minutes": 56}], True),
        ):
            with self.subTest(baseline=baseline, routed=routed):
                results, count = apply_projected_load(baseline, "Dialysis", uses_routes=routed)
                self.assertEqual(count, 0)
                self.assertEqual([item["row"] for item in results], [item["row"] for item in baseline])

    def test_different_strength_or_passing_mention_cannot_displace_the_first_recommendation(self):
        for kind, match_score in (("hospital_site", 100), ("mention", 40)):
            with self.subTest(kind=kind):
                altered = {**self.kullu, "service_confidence": replace(
                    self.kullu["service_confidence"], kind=kind, match_score=match_score,
                )}
                results, count = apply_projected_load([self.mandi, altered], "Dialysis", uses_routes=False)
                self.assertEqual(count, 0)
                self.assertEqual(results[0]["row"]["source_id"], MANDI)
                if match_score < 100:
                    self.assertNotIn("projected_load", results[1])

    def test_invalid_workload_or_different_scope_cannot_change_order(self):
        for field, value in (("caseload", "unavailable"), ("capacity", "0"), ("scope", "Inpatient sessions")):
            with self.subTest(field=field):
                projections = load_projections().astype(str)
                projections.loc[projections["facility_id"].eq(f"fdr:{KULLU}"), field] = value
                with patch("projected_load.load_projections", return_value=projections):
                    results, count = apply_projected_load(self.baseline, "Dialysis", uses_routes=False)
                self.assertEqual(count, 0)
                self.assertEqual(results[0]["row"]["source_id"], MANDI)
