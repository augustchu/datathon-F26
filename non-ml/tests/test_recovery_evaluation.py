"""Verify that recovery metrics count scope, linkage, and evidence separately."""

import unittest

import pandas as pd

from facility_tool.recovery_evaluation import evaluate_recovery, recovery_case_fingerprint


def case(number, *, facility=None, answerable=True, **extra):
    return dict(case_id=f"case-{number}", facility_id=facility or f"branch-{number}", field="capacity",
                answerable=answerable, expected_value=120 if answerable else None,
                expected_scope="whole_facility" if answerable else None, **extra)


def decision(number, *, facility=None, accepted=True, **extra):
    record = dict(case_id=f"case-{number}", status="accepted" if accepted else "abstained", reviewer_minutes=2)
    if accepted:
        record.update(facility_id=facility or f"branch-{number}", field="capacity", value=120,
                      scope="whole_facility", citation_supported=True)
    return {**record, **extra}


class RecoveryEvaluationTests(unittest.TestCase):
    def test_tuple_citation_and_branch_errors_count_separately(self):
        cases = [case(i, answerable=i not in (4, 6), originally_missing=i in (1, 2),
                      source_type="certificate", language="en") for i in range(1, 7)]
        decisions = [decision(1), decision(2, citation_supported=False),
                     decision(3, facility="sibling-branch"), decision(4),
                     decision(5, accepted=False), decision(6, accepted=False)]
        result = evaluate_recovery(pd.DataFrame(cases), pd.DataFrame(decisions))
        overall = result["overall"]
        for metric, (numerator, denominator) in {
            "accepted_claim_precision": (1, 4), "answerable_case_recall": (1, 4),
            "abstention_rate": (2, 6), "correct_tuple_rate": (2, 6),
            "unsupported_citation_rate": (1, 4), "wrong_branch_rate": (1, 4),
        }.items():
            self.assertEqual(overall[metric], dict(numerator=numerator, denominator=denominator,
                                                  value=numerator / denominator, interval=None))
        self.assertEqual(overall["reviewer_minutes_per_accepted_new_fact"], 12)
        self.assertEqual(result["by_source_type"]["certificate"], overall)
        self.assertEqual(result["by_language"]["en"], overall)
        self.assertEqual(result["per_case"][2]["predicted_facility_id"], "sibling-branch")

    def test_scope_and_explicit_contradiction_are_part_of_the_tuple(self):
        cases = [case(1, expected_stance="contradicts"), case(2), case(3, expected_stance="contradicts")]
        decisions = [decision(1), decision(2, scope="icu"), decision(3, stance="contradicts")]
        result = evaluate_recovery(cases, decisions)
        self.assertEqual([row["correct_tuple"] for row in result["per_case"]], [False, False, True])
        self.assertIsNone(result["overall"]["reviewer_minutes_per_accepted_new_fact"])

    def test_missing_or_invalid_references_and_decisions_raise(self):
        with self.assertRaisesRegex(ValueError, "exactly one decision"):
            evaluate_recovery([case(1), case(2)], [decision(1)])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evaluate_recovery([case(1)], [decision(1), decision(1)])
        with self.assertRaisesRegex(ValueError, "citation_supported"):
            evaluate_recovery([case(1)], [decision(1, citation_supported="yes")])
        with self.assertRaisesRegex(ValueError, "exact nonnegative count"):
            evaluate_recovery([case(1)], [decision(1, value=True)])
        with self.assertRaisesRegex(ValueError, "leave its expected claim blank"):
            evaluate_recovery([{**case(1), "answerable": False}], [decision(1)])

    def test_fingerprint_locks_references_and_evaluation_requires_one_split(self):
        cases = [case(1, split="development"), case(2, split="test")]
        fingerprint = recovery_case_fingerprint(cases)
        with self.assertRaisesRegex(ValueError, "Choose one split"):
            evaluate_recovery(cases, [decision(2)])
        result = evaluate_recovery(cases[::-1], [decision(2)], split="test", expected_case_fingerprint=fingerprint)
        self.assertEqual(result["case_fingerprint"], fingerprint)
        self.assertEqual(result["split"], "test")
        with self.assertRaisesRegex(ValueError, "frozen reference"):
            evaluate_recovery([cases[0], {**cases[1], "expected_value": 130}], [decision(2)], split="test",
                              expected_case_fingerprint=fingerprint)

    def test_transitively_linked_source_families_cannot_cross_splits(self):
        cases = [case(1, split="development", document_family="shared"),
                 case(2, split="development", document_family="shared", facility="branch-x"),
                 case(3, split="test", facility="branch-x")]
        with self.assertRaisesRegex(ValueError, "cross development and test"):
            evaluate_recovery(cases, [decision(3)], split="test")

    def test_intervals_are_opt_in_and_account_for_repeated_branches(self):
        cases = [case(1), case(2, facility="branch-1"), case(3), case(4)]
        decisions = [decision(1), decision(2, facility="branch-1"), decision(3), decision(4, value=10)]
        with self.assertRaisesRegex(ValueError, "require independent cases"):
            evaluate_recovery(cases, decisions, interval_method="wilson")
        first = evaluate_recovery(cases, decisions, interval_method="cluster_bootstrap", resamples=200)
        second = evaluate_recovery(cases[::-1], decisions[::-1], interval_method="cluster_bootstrap", resamples=200)
        self.assertEqual(first["overall"], second["overall"])
        self.assertEqual(first["overall"]["clusters"], 3)
        low, high = first["overall"]["accepted_claim_precision"]["interval"]
        self.assertTrue(low < .75 < high)
        self.assertIsNone(first["overall"]["wrong_branch_rate"]["interval"])

        independent = evaluate_recovery([case(1)], [decision(1)], interval_method="wilson")
        low, high = independent["overall"]["accepted_claim_precision"]["interval"]
        self.assertTrue(low < 1)
        self.assertAlmostEqual(high, 1)

    def test_repeated_facts_do_not_inflate_reviewer_throughput(self):
        cases = [case(1, originally_missing=True), case(2, facility="branch-1", originally_missing=True)]
        decisions = [decision(1), decision(2, facility="branch-1")]
        result = evaluate_recovery(cases, decisions)
        self.assertEqual(result["overall"]["accepted_new_facts"], 1)
        self.assertEqual(result["overall"]["reviewer_minutes_per_accepted_new_fact"], 4)

    def test_empty_metric_denominators_stay_unavailable(self):
        result = evaluate_recovery([case(1, answerable=False)], [decision(1, accepted=False)])
        self.assertIsNone(result["overall"]["accepted_claim_precision"]["value"])
        self.assertIsNone(result["overall"]["answerable_case_recall"]["value"])
        self.assertEqual(result["overall"]["abstention_rate"]["value"], 1)


if __name__ == "__main__":
    unittest.main()
