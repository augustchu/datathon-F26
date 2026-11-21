"""Behavior checks using synthetic records only; no Databricks connection."""
import json
import unittest
from decimal import Decimal
from pathlib import Path

import cloudpickle
import numpy as np
import pandas as pd

from capacity_rf import FacilityEncoder, parse_number, run_capacity_rf


def synthetic_facilities():
    rng = np.random.default_rng(8)
    doctors = np.repeat(rng.integers(2, 60, size=60), 2)
    capacity = 12 + 5 * doctors
    rows = len(doctors)
    return pd.DataFrame({
        "facility_id": np.repeat(np.arange(60), 2),
        "before_boundary_leak": capacity,
        "countries": ["India"] * rows,
        "doctors": [Decimal(int(x)) if i % 9 else None for i, x in enumerate(doctors)],
        "capacity": [str(x) for x in capacity],
        "description": [f"Regional hospital {i} with {capacity[i]} beds and general surgery" for i in range(rows)],
        "services": [np.array(["dialysis", "MRI"]) if i % 2 else ["surgery"] for i in range(rows)],
        "equipment": [{"scanner": i % 3, "tags": ["CT"]} for i in range(rows)],
        "state": ["state_a" if i % 3 else "state_b" for i in range(rows)],
        "constant": ["same"] * rows,
        "all_missing": [None] * rows,
    }, index=np.arange(rows) * 7)


def fit(data, **kwargs):
    return run_capacity_rf(data, group_column="facility_id", n_estimators=25,
                           permutation_repeats=1, n_jobs=1, max_text_features=20, **kwargs)


class CapacityModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_facilities()
        cls.result = fit(cls.data)

    def test_exclusive_boundary_and_target_never_predictors(self):
        expected = list(self.data.columns[self.data.columns.get_loc("countries") + 1:])
        expected.remove("capacity")
        self.assertEqual(self.result["summary"]["predictor_columns"], expected)
        self.assertEqual(self.result["feature_audit"]["column"].tolist(), expected)
        self.assertEqual(set(self.result["importance"]["column"]), set(expected))
        roles = self.result["column_selection"].set_index("column")["role"]
        self.assertEqual(roles["facility_id"], "grouping_only")
        self.assertEqual(roles["countries"], "excluded_at_or_before_boundary")
        self.assertEqual(roles["capacity"], "target")
        self.assertNotIn("before_boundary_leak", expected)
        self.assertNotIn("before_boundary_leak", set(self.result["leakage_flags"]["column"]))

    def test_grouping_from_earlier_column_and_held_out_predictions(self):
        split = self.result["split"]
        self.assertEqual(split.groupby("group")["split"].nunique().max(), 1)
        facility_ids = self.data.iloc[split["source_row"]]["facility_id"].to_numpy()
        self.assertEqual(split.assign(facility_id=facility_ids).groupby("facility_id")["split"].nunique().max(), 1)
        pred = self.result["predictions"]
        self.assertEqual(set(pred["source_row"]), set(split.loc[split["split"].eq("test"), "source_row"]))
        np.testing.assert_allclose(pred["actual_capacity"], self.data.iloc[pred["source_row"]]["capacity"].astype(float))
        self.assertTrue(np.isfinite(pred["predicted_capacity"]).all())

    def test_performance_serialization_and_input_unchanged(self):
        metrics = self.result["metrics"].set_index("model")
        self.assertLess(metrics.loc["random_forest", "mae"], metrics.loc["baseline_mean", "mae"])
        self.assertIn("bed_count_in_text", self.result["leakage_flags"]["flag"].tolist())
        model = cloudpickle.loads(cloudpickle.dumps(self.result["pipeline"]))
        predictors = self.data[self.result["summary"]["predictor_columns"]]
        np.testing.assert_allclose(model.predict(predictors), self.result["pipeline"].predict(predictors))
        # pandas equality handles nested objects inconsistently; compare stable serialization.
        original = synthetic_facilities()
        self.assertEqual(self.data.to_json(), original.to_json())
        json.dumps(self.result["summary"])

    def test_reordered_source_produces_same_split_and_model(self):
        reordered = self.data.sample(frac=1, random_state=99).reset_index(drop=True)
        other = fit(reordered)
        original_split = self.result["split"].groupby("group")["split"].first().sort_index()
        new_split = other["split"].groupby("group")["split"].first().sort_index()
        pd.testing.assert_series_equal(original_split, new_split)
        predictors = self.data[self.result["summary"]["predictor_columns"]]
        np.testing.assert_allclose(other["pipeline"].predict(predictors), self.result["pipeline"].predict(predictors))

    def test_unknown_values_do_not_change_fitted_encoding(self):
        training = pd.DataFrame({"number": [1, None, 3, 5], "category": ["a", "b", "a", "b"]})
        encoder = FacilityEncoder().fit(training)
        before = cloudpickle.dumps(encoder)
        held_out = pd.DataFrame({"number": [None, 1000000], "category": ["never_seen", None]})
        result = encoder.transform(held_out).toarray()
        names = list(encoder.get_feature_names_out())
        self.assertEqual(result[0, names.index("number::value")], 3)
        self.assertEqual(result[0, names.index("category::unknown")], 1)
        self.assertEqual(before, cloudpickle.dumps(encoder))

    def test_invalid_targets_not_trained_or_imputed(self):
        data = self.data.copy()
        data["capacity"] = data["capacity"].astype(object)
        data.iloc[:5, data.columns.get_loc("capacity")] = [None, "100-200", "100 beds", "-5", True]
        result = fit(data)
        self.assertEqual(result["summary"]["valid_capacity_rows"], len(data) - 5)
        self.assertFalse(set(range(5)) & set(result["split"]["source_row"]))
        self.assertEqual(parse_number("1,000"), 1000)
        self.assertTrue(np.isnan(parse_number("10,00")))
        self.assertTrue(np.isnan(parse_number("1e1000")))

    def test_invalid_column_boundaries_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Boundary column"):
            fit(self.data.drop(columns="countries"))
        with self.assertRaisesRegex(ValueError, "No predictor columns"):
            fit(self.data[["facility_id", "countries", "capacity"]])
        with self.assertRaisesRegex(ValueError, "20 valid"):
            fit(self.data.assign(capacity=None))

    def test_exact_predictor_duplicates_share_split_without_id(self):
        data = pd.DataFrame({"countries": ["India"] * 40,
                             "numeric_feature": np.repeat(np.arange(20), 2),
                             "capacity": np.repeat(np.arange(20) * 10, 2)})
        result = run_capacity_rf(data, n_estimators=5, permutation_repeats=1, n_jobs=1)
        split = result["split"]
        feature = data.iloc[split["source_row"]]["numeric_feature"].to_numpy()
        self.assertEqual(split.assign(feature=feature).groupby("feature")["split"].nunique().max(), 1)

    def test_standalone_notebook_with_emulated_databricks_table(self):
        notebook = json.loads((Path(__file__).parent / "capacity_random_forest.ipynb").read_text())
        frame = self.data.copy()

        class Source:
            columns = frame.columns.tolist()
            dtypes = [(column, str(frame[column].dtype)) for column in columns]

            def limit(self, count):
                self.limit_count = count
                return self

            def toPandas(self):
                return frame.head(self.limit_count).copy()

        class Spark:
            def table(self, name):
                if name != "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities":
                    raise AssertionError("Unexpected table name")
                return Source()

        import matplotlib
        matplotlib.use("Agg")
        scope = {"__name__": "__main__", "spark": Spark(), "display": lambda *args: None}
        for number, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            code = cell["source"]
            if code.startswith("%pip") or code.startswith("dbutils.library.restartPython"):
                continue  # Uses the existing local test environment.
            exec(compile(code, f"notebook_cell_{number}", "exec"), scope)
            if code.startswith('TABLE = '):
                scope.update(N_TREES=10, PERMUTATION_REPEATS=1, GROUP_COLUMN="facility_id")
        result = scope["result"]
        self.assertNotIn("countries", result["summary"]["predictor_columns"])
        self.assertEqual(result["summary"]["after_column"], "countries")
        # Tests serialization with custom classes defined in notebook __main__.
        restored = cloudpickle.loads(cloudpickle.dumps(result["pipeline"]))
        predictors = frame[result["summary"]["predictor_columns"]]
        np.testing.assert_allclose(restored.predict(predictors), result["pipeline"].predict(predictors))
        scope["plt"].close("all")


if __name__ == "__main__":
    unittest.main()
