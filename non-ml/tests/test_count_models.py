"""Count-model selection controls leakage and preserves shadow estimates."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from facility_tool.count_models import fit_count_models
from facility_tool.eda import UPDATE_COLUMNS
from facility_tool.updates import load_estimates
from facility_tool.workspace import Store


class CountModelTests(unittest.TestCase):
    def test_phone_fields_are_excluded_and_intervals_import(self):
        rng = np.random.default_rng(47)
        doctors = rng.integers(5, 80, 350).astype(float)
        capacity = 4 * doctors + 5
        splits = ['train'] * 220 + ['calibration'] * 60 + ['test'] * 70
        frame = pd.DataFrame({'facility_id': [f'facility-{i}' for i in range(len(doctors))],
                              'split': splits, 'status': 'reported', 'facilityTypeId': 'hospital',
                              'operatorTypeId': 'private', 'phone_numbers': capacity,
                              'has_phone': capacity > 170})
        bed_audit = frame.assign(value=capacity)
        bed_audit.loc[range(20), 'value'] = np.nan
        bed_audit.loc[range(20), 'status'] = 'missing'
        bed_audit.loc[21, 'operatorTypeId'] = 'https://example.invalid/invalid-category'
        doctor_audit = frame.assign(value=doctors)
        schema = ['countries', 'capacity', 'numberDoctors', 'facilityTypeId', 'operatorTypeId',
                  'phone_numbers', 'has_phone']
        result = fit_count_models('capacity', bed_audit, doctor_audit, schema, as_of='2026-11-21')
        selection = result['candidate_evidence'].set_index('candidate')['selected']
        self.assertEqual(selection.loc[selection].index.to_list(), ['numberDoctors'])
        self.assertEqual(set(selection.index), {'facilityTypeId', 'operatorTypeId', 'numberDoctors'})
        coefficients = result['coefficients']['encoded_feature']
        self.assertFalse(coefficients.str.contains('phone', case=False).any())
        quality = result['category_quality']
        self.assertEqual(quality.loc[quality['status'].eq('unrecognized'), 'count'].sum(), 1)
        updates = result['updates']
        self.assertGreater(len(updates), 0)
        self.assertEqual(updates.columns.to_list(), UPDATE_COLUMNS)
        self.assertTrue(set(updates['facility_id']).issubset(set(bed_audit.loc[:19, 'facility_id'])))
        self.assertTrue(updates['lower'].le(updates['value']).all() and updates['value'].le(updates['upper']).all())
        with TemporaryDirectory() as temp:
            store = Store(Path(temp), 'demo', '2026-11-21', temp)
            store.write('count_model_estimates', updates)
            checkpoint = store.read('count_model_estimates')
            self.assertEqual(checkpoint.columns.to_list(), UPDATE_COLUMNS)
            path = Path(temp) / 'estimates.csv'
            checkpoint.to_csv(path, index=False)
            self.assertEqual(len(load_estimates(path, bed_audit['facility_id'], services=[])), len(updates))


if __name__ == '__main__':
    unittest.main()
