"""Checks for the XGBoost caseload experiment and its published cohorts."""

import json
import unittest
from unittest.mock import patch

try:
    import xgboost_caseload as experiment
except ModuleNotFoundError as exc:
    if exc.name not in {"numpy", "xgboost"}:
        raise
    raise unittest.SkipTest(f"Optional caseload dependency unavailable: {exc.name}") from exc


class XGBoostCaseloadTests(unittest.TestCase):
    def setUp(self):
        self.hp = json.loads((experiment.ROOT / "hp_dialysis_model_data.json").read_text())
        self.tn = json.loads((experiment.ROOT / "tn_maternity_model_data.json").read_text())

    def test_published_workloads_and_missing_targets(self):
        hp, application = experiment.validate(self.hp)
        self.assertEqual(len(hp), 47)
        for period, sessions, machines in (("FY2023–24", 53736, 121), ("FY2024–25", 61224, 128)):
            rows = [row for row in hp if row["period"] == period]
            self.assertEqual(sum(row["total"] for row in rows), sessions)
            self.assertEqual(sum(row["machines"] for row in rows), machines)
        self.assertEqual(len(application), 24)
        self.assertTrue(all(row["total"] is None for row in application))

        tn, application = experiment.validate(self.tn)
        self.assertEqual((len(tn), len({row["district"] for row in tn})), (94, 37))
        self.assertEqual(sum(row["total"] for row in tn), 137712)
        self.assertEqual(sum(row["beds"] for row in tn), 6173)
        self.assertFalse(application)

    def test_district_and_period_holds(self):
        hp, _ = experiment.validate(self.hp)
        with patch.object(experiment, "score", return_value=[]) as score:
            experiment.heldout(hp, self.hp, future=True)
        self.assertEqual(score.call_count, 11)
        for call in score.call_args_list:
            train, test, _ = call.args
            self.assertTrue({r["district"] for r in train}.isdisjoint(r["district"] for r in test))
            self.assertLess(max(r["period"] for r in train), min(r["period"] for r in test))

    def test_known_facility_forward_test_uses_prior_machines(self):
        hp, _ = experiment.validate(self.hp)
        with patch.object(experiment, "score", return_value=[]) as score:
            experiment.forward_known_facilities(hp, self.hp)
        train, test, _ = score.call_args.args
        self.assertEqual(len(test), 23)
        earlier = {row["facility_id"]: row for row in train}
        self.assertTrue(all(row["machines"] == earlier[row["facility_id"]]["machines"] for row in test))
        bilaspur = next(row for row in test if row["facility_id"] == "hp_pmndp:bilaspur")
        self.assertEqual((bilaspur["total"], bilaspur["machines"]), (3078, 4))

    def test_observed_period_cannot_be_an_application(self):
        duplicate = self.hp["observations"][-1].copy()
        duplicate[self.hp["columns"].index("total")] = None
        self.hp["applications"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "already observed"):
            experiment.validate(self.hp)

    def test_applies_only_to_supported_feature_values(self):
        hp, application = experiment.validate(self.hp)
        self.assertIsNone(experiment.application_reason(application[0], hp, ["machines"]))
        reason = experiment.application_reason({**application[0], "machines": 99}, hp, ["machines"])
        self.assertEqual(reason, "outside_observed_range:machines")


if __name__ == "__main__":
    unittest.main()
