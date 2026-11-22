import unittest

from evaluate import compare, reference_index, score


QUERY = {"id": "case", "stratum": "rural", "suite": "evidence_panel", "origin": "Example",
         "service": "dialysis", "transport_mode": "walking", "purpose": "Test identity and ranking."}
SOURCE = {"url": "https://example.org/evidence", "excerpt": "Dialysis at this branch."}
REFERENCE = {"query_id": "case", "judgments": [
    {"label": "supported", "names": ["Example Hospital"], "pin": "123456",
     "facility_ids": ["known"], "reason": "Source names branch.", "sources": [SOURCE]},
    {"label": "unsupported", "facility_ids": ["held"], "reason": "Location hold.", "sources": [SOURCE]},
]}


def run(*results, outcome="recommendations"):
    return [{"query_id": "case", "outcome": outcome, "results": list(results)}]


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.queries = {"case": QUERY}
        self.reference = reference_index([REFERENCE], self.queries)

    def check(self, payload, k=5):
        return score(self.queries, self.reference, payload, k)

    def test_exact_name_pin_only_and_review_unknown_before_anchor(self):
        output = self.check(run(
            {"facility_id": "other", "name": "Example Hospital", "pin": "123457"},
            {"name": "EXAMPLE HOSPITAL.", "pin": "123456",
             "source_url": SOURCE["url"], "evidence_excerpt": SOURCE["excerpt"]},
        ))
        case = output["queries"][0]
        self.assertEqual(case["top_one"], "unreviewed")
        self.assertEqual(case["first_supported_rank"], 2)
        self.assertEqual(case["anchor_check"], "hit")
        self.assertEqual(output["groups"]["all"]["citations_present"], 1)

    def test_exact_hold_and_alias_duplicate_are_visible(self):
        data = run({"facility_id": "held", "name": "Held point"},
                   {"facility_id": "known", "name": "Example Hospital", "pin": "123456"},
                   {"name": "Example Hospital", "pin": "123456"})
        result = self.check(data)
        self.assertEqual(result["groups"]["all"]["top_one"]["unsupported"], 1)
        self.assertEqual(result["groups"]["all"]["duplicate_results"], 1)
        self.assertEqual(self.check(data, k=1)["queries"][0]["anchor_check"], "miss")

    def test_conflicting_pin_cannot_receive_positive_credit(self):
        case = self.check(run({"facility_id": "known", "name": "Example Hospital",
                               "pin": "123457"}))["queries"][0]
        self.assertTrue(case["results"][0]["pin_conflict"])
        self.assertEqual(case["top_one"], "unresolved")
        self.assertEqual(case["anchor_check"], "miss")

    def test_abstain_error_unsupported_and_missing_are_distinct(self):
        cases = {outcome: self.check(run(outcome=outcome))["queries"][0]
                 for outcome in ("abstain", "error", "unsupported_query")}
        self.assertEqual(cases["abstain"]["anchor_check"], "miss")
        self.assertEqual(cases["error"]["anchor_check"], "not_scored")
        self.assertEqual(cases["unsupported_query"]["anchor_check"], "not_scored")
        self.assertEqual(self.check([])["queries"][0]["outcome"], "missing_output")
        open_case = score(self.queries, {}, run(outcome="abstain"), 5)["queries"][0]
        self.assertEqual(open_case["anchor_check"], "not_scored")

    def test_paired_comparison_skips_unscored_runs(self):
        hit = self.check(run({"facility_id": "known", "name": "Example Hospital"}))
        miss = self.check(run(outcome="abstain"))
        skipped = self.check(run(outcome="error"))
        self.assertEqual(compare(hit, miss)["baseline_miss_to_current_hit"], 1)
        self.assertEqual(compare(hit, skipped)["queries_scored_for_anchors_by_both"], 0)

    def test_route_gap_compares_same_units_and_flags_car_label(self):
        output = self.check(run(
            {"facility_id": "a", "name": "A", "travel_basis": "route_time", "travel_value": 200, "travel_mode": "walking"},
            {"facility_id": "b", "name": "B", "travel_basis": "route_time", "travel_value": 50, "travel_mode": "walking"},
            {"facility_id": "c", "name": "C", "travel_basis": "route_time", "travel_value": 10, "travel_mode": "driving",
             "car_only": True, "requested_mode_status": "unavailable"},
            {"facility_id": "d", "name": "D", "travel_basis": "route_distance", "travel_value": 5, "travel_mode": "driving",
             "car_only": True, "requested_mode_status": "not_routed_within_limit"},
        ))
        case = output["queries"][0]
        self.assertEqual(case["top_result_travel_comparison"]["top_result_extra_over_nearest_returned"], 150)
        self.assertEqual(output["groups"]["all"]["route_issues"], 1)

    def test_duplicate_and_unknown_query_ids_fail_instead_of_overwriting(self):
        abstentions = run(outcome="abstain")
        with self.assertRaisesRegex(ValueError, "duplicate query_id"):
            self.check(abstentions * 2)
        with self.assertRaisesRegex(ValueError, "unknown query IDs"):
            self.check([{"query_id": "typo", "outcome": "abstain"}])


if __name__ == "__main__":
    unittest.main()
