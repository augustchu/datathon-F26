"""Exercise the audited training inputs for the XGBoost capacity forests."""

import unittest

import numpy as np
import pandas as pd

from xgboost_capacity import _eligible_rows, build_capacity_features, train_xgboost_capacity


def fixture():
    facilities = [f"branch-{number:02}" for number in range(16)]
    frame = pd.DataFrame({
        "facility_id": [*facilities, facilities[1]],
        "unique_id": [f"source-{number:02}" for number in range(17)],
        "phone_numbers": ["1234567890"] * 17,
        "countries": ["India"] * 17,
        "facilityTypeId": ["hospital"] * 17,
        "operatorTypeId": ["public"] * 17,
        "numberDoctors": np.arange(17, dtype=float),
        "latitude": [24.5] * 17,
        "longitude": [78.2] * 17,
    })
    frame.loc[2, "facilityTypeId"] = None
    frame.loc[15, "operatorTypeId"] = "invalid category"
    splits = ["train"] * 6 + ["calibration"] * 4 + ["test"] * 6
    capacity = pd.DataFrame({
        "facility_id": facilities,
        "split": splits,
        "status": ["reported"] * 16,
        "value": np.arange(10, 26, dtype=float),
    })
    doctors = capacity.assign(value=np.arange(16, dtype=float))
    capacity.loc[[12, 13], ["status", "value"]] = ["missing", np.nan]
    capacity.loc[14, ["status", "value"]] = ["conflicting", np.nan]
    doctors.loc[2, ["status", "value"]] = ["missing", np.nan]

    source = frame[["unique_id", "facility_id"]].rename(columns={"unique_id": "source_record_id"})
    source["source"] = "fdr"
    source["input_position"] = np.arange(len(frame))
    source["review_flags"] = [[] for _ in range(len(frame))]
    source.at[2, "review_flags"] = ["missing_coordinates"]
    source.at[16, "review_flags"] = ["reviewed_coordinate_conflict"]
    branches = pd.DataFrame({"facility_id": facilities, "review_flags": [[] for _ in facilities]})
    branches.at[12, "review_flags"] = ["pin_state_conflict"]
    return frame, capacity, doctors, source, branches


class AuditedXGBoostCapacityTests(unittest.TestCase):
    def test_excludes_bad_records_and_uses_only_reported_labels(self):
        result = train_xgboost_capacity(*fixture(), n_jobs=1)
        self.assertEqual(result["facility_counts"], {"train": 5, "validation": 4, "test": 2, "prediction": 1})
        self.assertEqual(result["predictions"]["facility_id"].to_list(), ["branch-13"])
        self.assertTrue(np.isfinite(result["predictions"]["estimated_capacity"]).all())
        counts = result["exclusions"].set_index(["reason", "capacity_status"])
        self.assertEqual(counts.loc[("wrong location", "reported"), "source_rows"], 2)
        self.assertEqual(counts.loc[("wrong location", "missing"), "facilities"], 1)
        self.assertEqual(counts.loc[("unusable capacity", "conflicting"), "facilities"], 1)
        self.assertEqual(counts.loc[("invalid facility or operator category", "reported"), "facilities"], 1)

    def test_missing_predictors_remain_eligible_with_explicit_indicators(self):
        rows, labels, _ = _eligible_rows(*fixture())
        features, core, _, _ = build_capacity_features(rows)
        missing = features.loc["branch-02"]
        self.assertEqual(labels.loc["branch-02", "status"], "reported")
        self.assertEqual(missing["facilityTypeId"], "missing")
        self.assertTrue(np.isnan(missing["numberDoctors"]))
        self.assertEqual(missing["numberDoctors_missing"], 1)
        self.assertEqual(missing["latitude_missing"], 1)
        self.assertEqual(features.loc["branch-00", "numberDoctors"], 0)
        self.assertEqual(features.loc["branch-00", "numberDoctors_missing"], 0)
        self.assertNotIn("phone_numbers", core)

    def test_refuses_reordered_fdr_source_rows(self):
        frame, *audits = fixture()
        order = list(range(len(frame)))
        order[1], order[-1] = order[-1], order[1]
        with self.assertRaisesRegex(ValueError, "original order"):
            _eligible_rows(frame.iloc[order], *audits)


if __name__ == "__main__":
    unittest.main()
