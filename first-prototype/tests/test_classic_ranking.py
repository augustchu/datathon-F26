import unittest
from unittest.mock import patch

import pandas as pd

from classic_ranking import recommended_assessments, score_recommended


class ClassicRankingTests(unittest.TestCase):
    def test_recommended_uses_exact_service_evidence_before_ranking(self):
        claim = {
            "source_kind": "hospital_site",
            "publisher_key": "hospital",
            "source_url": "https://example.org/branches/302004",
            "excerpt": "Our Jaipur branch offers dialysis.",
            "valid_from": None,
            "valid_through": None,
        }
        reviews = {
            "fdr:reviewed": {"302004": {"dialysis": [claim]}},
            "fdr:conflicting": {"302004": {"dialysis": [{**claim, "stance": "contradicts"}]}},
        }
        common = {"lat": 26.9, "lon": 75.8, "pin": "302004"}
        facilities = pd.DataFrame([
            {**common, "facility_id": "fdr:reviewed", "name": "Reviewed Hospital"},
            {**common, "facility_id": "fdr:listing", "name": "Facility Listing", "specialties": "Dialysis"},
            {**common, "facility_id": "fdr:conflicting", "name": "Conflicting Listing", "specialties": "Dialysis"},
            {**common, "facility_id": "fdr:negated", "name": "Unavailable", "description": "Dialysis is not available."},
            {**common, "facility_id": "fdr:related", "name": "Related Service", "specialties": "Nephrology"},
        ], index=[7, 12, 14, 20, 23])

        with patch("classic_confidence._reviewed_by_branch", return_value=reviews):
            eligible = recommended_assessments(facilities, "Dialysis", ["dialysis"])

        self.assertEqual({7, 12}, set(eligible))
        self.assertEqual(eligible[7].urls, (claim["source_url"],))
        self.assertEqual(eligible[7].score, 95)
        self.assertEqual(eligible[12].score, 60)

    def test_recommended_scores_selected_service_and_matching_travel_units(self):
        result = {"row": {"specialties": "Dialysis"}, "dist": 12, "route_minutes": 28}
        score_recommended([result], "Dialysis", ["dialysis"])
        distance = result["recommended"]
        score_recommended([result], "Dialysis", ["dialysis"], uses_routes=True, preassessed=True)
        route = result["recommended"]

        self.assertEqual(distance["travel"], 12)
        self.assertEqual(route["travel"], 28)
        self.assertEqual(route["service_confidence"], distance["service_confidence"] * 2)
        self.assertEqual(route["service_match"], 0)
        score_recommended([result], "MRI", ["mri"])
        self.assertEqual(result["service_confidence"].score, 0)
        self.assertEqual(result["recommended"]["score"], 47)
