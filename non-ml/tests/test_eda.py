"""Synthetic checks for the count audit and proposed facility holdout."""

import math
import unittest

import pandas as pd

from facility_tool.eda import capacity_eda, parse_count, reported_count_updates
from facility_tool.examples import demo_inputs


class CapacityEdaTests(unittest.TestCase):
    def test_distinguish_exact_counts_from_ranges_and_bed_subtypes(self):
        self.assertEqual(parse_count("1,200 beds", "capacity"), 1200)
        self.assertEqual(parse_count("Doctors: 0", "numberDoctors"), 0)
        for raw in ("80–100 beds", "12 ICU beds", "10.5 beds", "-2", "unknown", True):
            with self.subTest(raw=raw):
                self.assertTrue(math.isnan(parse_count(raw, "capacity")))

    def test_conflicting_branches_do_not_create_a_reported_count(self):
        frame = pd.DataFrame({
            "facility_id": ["a", "a", "b", "b", "c"],
            "unique_id": ["1", "2", "3", "4", "5"],
            "countries": ["India"] * 5, "capacity": ["12", "14", None, "8 beds", "20 ICU beds"],
            "numberDoctors": [2, 3, 2, 2, 12],
        })
        result = capacity_eda(frame, "capacity", frame.columns)
        audit = result["target_audit"].set_index("facility_id")
        self.assertEqual(audit.loc["a", "status"], "conflicting")
        self.assertEqual(audit.loc["c", "status"], "unparseable")
        self.assertEqual(result["split"]["facility_id"].nunique(), 3)
        reported = reported_count_updates(frame, "capacity")
        self.assertEqual(reported["facility_id"].tolist(), ["b"])
        self.assertEqual(reported["value"].tolist(), [8])
        self.assertIn("source record IDs: 4", reported.iloc[0]["provenance"])

    def test_same_facility_partitions_and_train_only_relationships(self):
        source = demo_inputs()["facilities"]
        frame = source.assign(facility_id=source["unique_id"])
        baseline = capacity_eda(frame, "capacity", source.columns)
        shuffled = frame.sample(frac=1, random_state=7)
        observed = capacity_eda(shuffled, "numberDoctors", source.columns)
        pd.testing.assert_series_equal(baseline["split"].set_index("facility_id")["split"],
                                       observed["split"].set_index("facility_id")["split"])
        test_ids = set(baseline["split"].loc[lambda part: part["split"].eq("test"), "facility_id"])
        self.assertTrue(test_ids.isdisjoint(baseline["analysis_rows"]["facility_id"]))
        with self.assertRaisesRegex(ValueError, "after 'countries'"):
            capacity_eda(frame, "capacity", source.columns, feature_columns=["name"])

    def test_establishment_year_is_numeric_and_excludes_future_dates(self):
        frame = pd.DataFrame({"facility_id": ["a", "b", "c"], "countries": ["India"] * 3,
                              "capacity": [14, 32, 40], "yearEstablished": ["1970", "1990.0", "2050"]})
        result = capacity_eda(frame, "capacity", frame.columns, reference_year=2026)
        row = result["numeric_relationships"].set_index("feature").loc["yearEstablished"]
        self.assertEqual(row["paired_facilities"], 2)
        self.assertTrue(pd.isna(result["analysis_rows"].set_index("facility_id").loc["c", "yearEstablished"]))


if __name__ == "__main__":
    unittest.main()
