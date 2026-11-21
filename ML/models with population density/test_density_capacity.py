"""Check that held-out feature extremes stay within the fitted input range."""

import unittest

import numpy as np
import pandas as pd
import torch

from density_capacity import CATEGORICAL, NUMERIC, TEXT, _facility_groups, _features, _partitions, train_capacity
from density_data import DENSITY_FEATURES


class DensityTrainerTests(unittest.TestCase):
    def test_unseen_numeric_extreme_and_rare_indicator_stay_bounded(self):
        n_rows = 24
        data = pd.DataFrame({
            **{column: np.arange(n_rows, dtype=float) for column in NUMERIC},
            **{column: ["reported"] * n_rows for column in (*CATEGORICAL, *TEXT)},
            "capacity": np.arange(10, n_rows + 10),
            "unique_id": [f"fixture-{i}" for i in range(n_rows)],
            DENSITY_FEATURES[0]: np.arange(100, n_rows + 100, dtype=float),
            DENSITY_FEATURES[1]: 0,
        })
        partitions, split_fingerprint = _partitions(_facility_groups(data), 42)
        train, _, test = partitions
        row = np.flatnonzero(test)[0]
        field = "distinct_social_media_presence_count"
        data.at[row, field] = 1_000_000.5
        data.at[row, "procedure"] = "null"
        data.at[row, DENSITY_FEATURES[0]] = np.nan
        data.at[row, DENSITY_FEATURES[1]] = 99
        expected_training_max = data.loc[train, field].max()

        previous_threads = torch.get_num_threads()
        try:
            torch.set_num_threads(1)
            result = train_capacity(data, include_nfhs=False, epochs=1)
        finally:
            torch.set_num_threads(previous_threads)
        processor = result["preprocessor"]
        features, _ = _features(data, include_density=True, include_nfhs=False)
        transformed = processor.transform(features.iloc[[row]])[0]
        names = list(processor.get_feature_names_out())
        np.testing.assert_array_less(-1e-8, transformed)
        np.testing.assert_array_less(transformed, 1 + 1e-8)
        self.assertEqual(transformed[names.index(f"numeric__{field}")], 1)
        self.assertEqual(transformed[names.index("indicators__procedure_present")], 0)
        self.assertEqual(transformed[names.index(f"indicators__{DENSITY_FEATURES[1]}")], 1)

        continuous = processor.transformers_[0][2]
        fitted = processor.named_transformers_["numeric"].named_steps["quantile"]
        self.assertEqual(fitted.quantiles_[-1, continuous.index(field)], expected_training_max)
        self.assertEqual(result["split_fingerprint"], split_fingerprint)
        self.assertEqual(result["row_counts"]["test"], int(test.sum()))


if __name__ == "__main__":
    unittest.main()
