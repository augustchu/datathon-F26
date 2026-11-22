from datetime import date
import unittest

import pandas as pd

from pipeline import evidence_penalty_minutes, prepare_facilities, rank_candidates, resolve_location, service_candidates
from service_confidence import reviewed_service_status, service_confidence, service_match


AS_OF = date(2026, 11, 22)
KEY = ("fdr:example", "302004", "dialysis")


def claim(kind="hospital_site", publisher="hospital", **changes):
    return {
        "source_kind": kind, "publisher_key": publisher, "source_url": f"https://{publisher}.example/",
        "excerpt": "The Jaipur branch provides dialysis.", "valid_from": None, "valid_through": None,
        **changes,
    }


def facilities(claims=(), *, holds=None, **changes):
    raw = pd.DataFrame([{
        "unique_id": "example", "facility_id": "fdr:example", "name": "Example Hospital",
        "pin": "302004", "city": "Jaipur", "state": "Rajasthan", "district": "Jaipur",
        "latitude": 26.9, "longitude": 75.8, "confidence_score": 10,
        "description": "Dialysis mentioned in facility text", "source_urls": "https://example.org/facility",
        **changes,
    }])
    return prepare_facilities(raw, reviewed_evidence={KEY: claims}, audited_location_holds=holds, as_of=AS_OF)


class ServiceConfidenceTests(unittest.TestCase):
    def test_points_and_zero_without_support(self):
        self.assertEqual(service_confidence("none", 20), (0, "Not expected to offer"))
        self.assertEqual(service_confidence("mention", 10), (25, "Limited"))
        self.assertEqual(service_confidence("record_listing", 10), (60, "Moderate"))
        self.assertEqual(service_confidence("conflict", 20), (0, "Conflicting service evidence"))
        self.assertEqual(service_confidence("directory", 20), (90, "High"))
        self.assertEqual(service_confidence("hospital_site", 20), (95, "High"))
        self.assertEqual(service_confidence("corroborated", 20), (100, "High"))
        self.assertEqual(service_match("none"), 0)

    def test_independence_and_valid_dates(self):
        official = claim()
        duplicate = claim()
        expired = claim("directory", "registry", valid_through=date(2026, 11, 21))
        future = claim("directory", "another", valid_from=date(2026, 11, 23))
        active = claim("directory", "registry", valid_through=AS_OF)
        self.assertEqual(reviewed_service_status([official, duplicate, expired, future], AS_OF)[0], "hospital_site")
        self.assertEqual(reviewed_service_status([official, active], AS_OF)[0], "corroborated")

    def test_reviewed_source_can_find_a_service_and_reaches_ranking_card(self):
        reviewed = claim("directory", "registry")
        prepared = facilities([reviewed], description="This branch offers dialysis.", source_urls="")
        self.assertTrue(service_candidates(prepared, "NICU").empty)
        result = rank_candidates(prepared, "Dialysis", (26.9, 75.8)).iloc[0]
        self.assertEqual(result.service_confidence_score, 90)
        self.assertEqual(result.service_match_score, 100)
        self.assertEqual(result.supporting_excerpt, reviewed["excerpt"])
        self.assertIn(reviewed["source_url"], result.service_evidence_sources_json)
        self.assertEqual(result.service_match_penalty, 0)
        self.assertLess(result.evidence_confidence_penalty, 0.01)

    def test_facility_mention_qualifies_as_limited_without_review_or_url(self):
        prepared = facilities(description="Our nephrology team counsels patients about dialysis.", source_urls="")
        result = service_candidates(prepared, "Dialysis").iloc[0]
        self.assertEqual(result.service_evidence_kind, "mention")
        self.assertEqual(result.service_evidence_label, "Mentioned in facility record")
        self.assertEqual(result.service_confidence_score, 25)
        self.assertEqual(result.service_confidence_tier, "Limited")
        self.assertEqual(result.service_match_score, 40)
        self.assertIn("counsels patients about dialysis", result.excerpt_dialysis)
        self.assertEqual(result.service_evidence_sources_json, "[]")

    def test_explicit_record_entries_and_statements_receive_moderate_credit(self):
        for source in (
            {"capability": '["Cardiology", "Hemodialysis"]'},
            {"procedure": "Dialysis clinic"},
            {"description": "This branch has an on-site dialysis unit."},
            {"description": "Example Hospital offers dialysis."},
            {"description": "Dialysis is available at Example Hospital."},
            {"description": "Services: MRI, dialysis."},
            {"description": "We counsel patients about dialysis. We offer dialysis on site."},
        ):
            with self.subTest(source=source):
                result = service_candidates(facilities(source_urls="", **source), "Dialysis").iloc[0]
                self.assertEqual(result.service_evidence_kind, "record_listing")
                self.assertEqual(result.service_evidence_label, "Listed in facility record")
                self.assertEqual(result.service_confidence_score, 60)
                self.assertEqual(result.service_confidence_tier, "Moderate")
                self.assertEqual(result.service_match_score, 100)
                self.assertEqual(result.service_evidence_sources_json, "[]")

    def test_related_procedure_and_indirect_support_remain_mentions(self):
        for service, source in (
            ("Dialysis", {"procedure": "Peritoneal dialysis catheter insertion"}),
            ("Dialysis", {"capability": "Provides dialysis counseling"}),
            ("Dialysis", {"description": "Counseling services include dialysis education."}),
            ("Dialysis", {"description": "Dialysis is available at ABC Hospital."}),
            ("Dialysis", {"capability": "Offers dialysis at ABC Hospital."}),
            ("MRI", {"description": "Has tie-ups for CT-MRI imaging"}),
            ("Emergency C-section", {"procedure": "Cesarean section"}),
            ("NICU", {"capability": "SNCU"}),
        ):
            with self.subTest(source=source):
                row = service_candidates(facilities(**source), service).iloc[0]
                self.assertEqual(row.service_evidence_kind, "mention")
                self.assertEqual(row.service_confidence_score, 25)

    def test_generic_url_equipment_or_wrong_pin_alone_do_not_qualify(self):
        for records in (facilities(description="Hospital serving Jaipur"),
                        facilities(description="", equipment="Dialysis machines"),
                        facilities([claim()], description="", pin="302005")):
            self.assertTrue(service_candidates(records, "Dialysis").empty)
        older = facilities().drop(columns=["service_evidence_kind_dialysis", "service_evidence_sources_dialysis"])
        older["claim_citable_dialysis"] = True
        self.assertTrue(service_candidates(older, "Dialysis").empty)

    def test_matcher_skips_explicit_non_offerings(self):
        for text in ("No dialysis at this branch.", "Peritoneal dialysis is not available.",
                     "Dialysis: referral only", "For dialysis, we refer to another hospital.",
                     "Plans to add dialysis in April."):
            with self.subTest(text=text):
                self.assertTrue(service_candidates(facilities(description=text), "Dialysis").empty)
        text = "Dialysis not offered at our old site. This branch provides dialysis."
        row = service_candidates(facilities(description=text), "Dialysis").iloc[0]
        self.assertEqual(row.excerpt_dialysis, "description: This branch provides dialysis")

    def test_reviewed_score_requires_citation_and_results_require_location_screen(self):
        prepared = facilities([claim()])
        self.assertTrue(service_candidates(prepared.drop(columns="location_search_eligible"), "Dialysis").empty)
        prepared["service_evidence_sources_dialysis"] = "[]"
        self.assertTrue(service_candidates(prepared, "Dialysis").empty)

    def test_service_and_routing_conflicts_withhold_the_result(self):
        disputed = facilities([claim(), claim("directory", "registry", stance="contradicts")])
        self.assertTrue(service_candidates(disputed, "Dialysis").empty)
        holds = {"fdr:example": [(26.9, 75.8)]}
        self.assertTrue(service_candidates(facilities([claim()], holds=holds), "Dialysis").empty)
        self.assertTrue(service_candidates(facilities(holds=holds), "Dialysis").empty)
        self.assertTrue(service_candidates(facilities(holds=holds, capability="Dialysis"), "Dialysis").empty)
        corrected = facilities([claim()], holds=holds, longitude=75.9)
        self.assertEqual(service_candidates(corrected, "Dialysis").iloc[0].service_confidence_score, 95)

    def test_invalid_points_never_appear_or_contribute_to_search_origin(self):
        valid = facilities([claim()])
        held = facilities([claim()], latitude=25.0, routing_eligible=False)
        outside = facilities([claim()], longitude=100.0)
        missing = facilities([claim()], latitude=None)
        wrong_region = facilities([claim()], latitude=20.0).assign(region_match_status="outside_or_missing")
        unmatched_population = valid.assign(region_match_status="nfhs_name_unmatched")
        self.assertEqual(len(service_candidates(unmatched_population, "Dialysis")), 1)
        snapshot = pd.concat([held, outside, missing, wrong_region, valid], ignore_index=True)
        for travel_times in (None, {"fdr:example": 10}):
            ranked = rank_candidates(snapshot, "Dialysis", (26.9, 75.8), travel_times=travel_times)
            self.assertEqual(ranked.latitude.tolist(), [26.9])
        self.assertEqual(resolve_location(snapshot, "Jaipur, Rajasthan")[0], (26.9, 75.8))
        self.assertEqual(resolve_location(snapshot, "302004")[0], (26.9, 75.8))

    def test_ranking_balances_drive_time_match_and_evidence(self):
        passing = facilities().assign(facility_id="passing", confidence_score=100)
        listing = facilities(capability="Dialysis").assign(facility_id="listing")
        reviewed = facilities([claim()]).assign(facility_id="reviewed")
        far = listing.assign(facility_id="far")
        no_route = reviewed.assign(facility_id="no_route")
        snapshot = pd.concat([passing, listing, reviewed, far, no_route], ignore_index=True)
        minutes = {"passing": 4, "listing": 10, "reviewed": 14, "far": 80, "no_route": float("inf")}
        ranked = rank_candidates(snapshot, "Dialysis", (26.9, 75.8), travel_times=minutes)
        self.assertEqual(ranked.facility_id.tolist(), ["reviewed", "listing", "passing", "far"])
        self.assertEqual(set(ranked.score_unit), {"minutes"})
        self.assertAlmostEqual(ranked.iloc[0].score, 14.00, places=2)
        self.assertAlmostEqual(ranked.iloc[1].score, 21.60, places=2)
        self.assertAlmostEqual(ranked.iloc[2].service_match_penalty, 6)
        self.assertAlmostEqual(ranked.iloc[2].evidence_confidence_penalty, 59.99, places=2)
        self.assertAlmostEqual(ranked.iloc[2].score, 69.99, places=2)

    def test_confidence_90_vs_60_can_offset_about_12_drive_minutes(self):
        listing = facilities(capability="Dialysis").assign(facility_id="listing")
        reviewed = facilities([claim("directory", "registry")]).assign(facility_id="reviewed")
        snapshot = pd.concat([listing, reviewed], ignore_index=True)
        for minutes, winner in ((21, "reviewed"), (22, "listing")):
            with self.subTest(minutes=minutes):
                ranked = rank_candidates(snapshot, "Dialysis", (26.9, 75.8),
                                         travel_times={"listing": 10, "reviewed": minutes})
                self.assertEqual(ranked.facility_id.iloc[0], winner)
                self.assertEqual(ranked.service_match_score.tolist(), [100, 100])
                penalty = ranked.set_index("facility_id").evidence_confidence_penalty
                self.assertAlmostEqual(penalty["listing"] - penalty["reviewed"], 11.60, places=2)

    def test_logistic_penalty_is_bounded_and_soft_near_high_confidence(self):
        penalties = [evidence_penalty_minutes(score) for score in range(101)]
        self.assertEqual(penalties[0], 60)
        self.assertEqual(penalties[100], 0)
        self.assertTrue(all(first >= second for first, second in zip(penalties, penalties[1:])))
        self.assertAlmostEqual(penalties[55], 30, places=2)
        self.assertAlmostEqual(penalties[60], 11.60, places=2)
        self.assertAlmostEqual(penalties[65], 3.26, places=2)
        self.assertLess(penalties[90] - penalties[95], 0.01)
        self.assertGreater(penalties[50] - penalties[60], 35)

    def test_routine_load_contributes_and_emergency_load_does_not(self):
        snapshot = facilities([claim()], capacity=20).assign(regional_population=500)
        kwargs = {"population_share_pct": 1.0, "travel_times": {"fdr:example": 10}}
        routine = rank_candidates(snapshot, "Dialysis", (26.9, 75.8), **kwargs).iloc[0]
        emergency = rank_candidates(snapshot, "Dialysis", (26.9, 75.8), severity="emergency", **kwargs).iloc[0]
        self.assertGreater(routine.load_penalty, 0)
        self.assertEqual(emergency.load_penalty, 0)
        self.assertAlmostEqual(routine.score - emergency.score, routine.load_penalty)

    def test_no_route_fallback_uses_distance_alone_and_keeps_location_gate(self):
        passing = facilities().assign(facility_id="nearest")
        listing = facilities(capability="Dialysis", latitude=26.92).assign(facility_id="listing")
        held = facilities([claim()], routing_eligible=False).assign(facility_id="held")
        snapshot = pd.concat([passing, listing, held], ignore_index=True)
        normal = rank_candidates(snapshot, "Dialysis", (26.9, 75.8))
        fallback = rank_candidates(snapshot, "Dialysis", (26.9, 75.8), travel_times={}, nearest_only=True)
        self.assertEqual(normal.facility_id.tolist(), ["listing", "nearest"])
        self.assertEqual(fallback.facility_id.tolist(), ["nearest", "listing"])
        self.assertEqual(fallback.score.tolist(), fallback.distance_km.tolist())
        self.assertEqual(fallback.service_match_score.tolist(), [40, 100])


if __name__ == "__main__":
    unittest.main()
