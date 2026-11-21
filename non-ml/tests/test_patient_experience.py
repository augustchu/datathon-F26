import json
import unittest
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from facility_tool.patient_experience import PATIENT_EXPERIENCE_COLUMNS, prepare_patient_experience


AS_OF = '2026-11-21'


def sites():
    return pd.DataFrame([
        dict(facility_id='fdr:jaipur-a', name='आरोग्य & Care', address='38 Test Road', city='Jaipur',
             state='Rajasthan', pin='302001', phone='+91 90000 00000'),
        dict(facility_id='fdr:jaipur-b', name='आरोग्य & Care', address='57 Test Street', city='Jaipur',
             state='Rajasthan', pin='302004'),
        dict(facility_id='fdr:delhi', name='Fictional Delhi Hospital', address='7 Example Road', city='New Delhi',
             state='Delhi', pin='110001'),
        dict(facility_id='nqas:unresolved', name='Unresolved Hospital', state='Rajasthan'),
    ])


def aggregate(ident='fdr:jaipur-a', **updates):
    row = dict(facility_id=ident, source='Fictional standardized survey',
               source_url='https://example.invalid/results?period=2026', metric='Overall experience mean',
               period_start='2026-01-01', period_end='2026-11-01', rating='5', rating_min='1', rating_max='5',
               response_count='30.0')
    row.update(updates)
    return row


class PatientExperienceTests(unittest.TestCase):
    def test_branch_search_links_only_use_public_facility_labels_and_do_not_require_ratings(self):
        profiles = prepare_patient_experience(sites(), None, AS_OF)
        self.assertEqual(list(profiles.columns), list(PATIENT_EXPERIENCE_COLUMNS))
        self.assertTrue(profiles['patient_experience_status'].eq('unknown').all())
        self.assertTrue(profiles['patient_experience_value'].isna().all())
        first, second, _, unknown = profiles['patient_reviews_search_url']
        query = parse_qs(urlsplit(first).query)
        self.assertEqual(urlsplit(first).netloc, 'www.google.com')
        self.assertEqual(query, {'api': ['1'], 'query': ['आरोग्य & Care, 38 Test Road, Jaipur, Rajasthan, 302001, India']})
        self.assertIn('57 Test Street', parse_qs(urlsplit(second).query)['query'][0])
        self.assertEqual(unknown, '')
        self.assertNotIn('fdr:', first)
        self.assertNotIn('90000', first)

    def test_survey_comparison_and_shrinkage_are_not_google_ratings(self):
        rows = [
            aggregate(patient_name='ignored private field'),
            aggregate('fdr:jaipur-b', rating=1, response_count=80, source_url='https://example.invalid/branch-b'),
            aggregate('fdr:delhi', rating=3, response_count=50, period_end='2026-11-02'),
        ]
        profiles = prepare_patient_experience(sites(), pd.DataFrame(rows), AS_OF).set_index('facility_id')
        first, second, third = [profiles.loc[ident] for ident in ('fdr:jaipur-a', 'fdr:jaipur-b', 'fdr:delhi')]
        self.assertTrue(profiles.iloc[:3]['patient_experience_status'].eq('eligible').all())
        self.assertAlmostEqual(first['patient_experience_value'], 0.8)
        self.assertAlmostEqual(second['patient_experience_value'], 0.1)
        self.assertAlmostEqual(third['patient_experience_value'], 0.5)
        self.assertEqual(first['patient_comparison_key'], second['patient_comparison_key'])
        self.assertNotEqual(first['patient_comparison_key'], third['patient_comparison_key'])
        self.assertEqual(json.loads(first['patient_comparison_key'])[-2:], ['2026-01-01', '2026-11-01'])
        self.assertEqual(first['patient_rating_response_count'], 30)
        self.assertEqual(first['patient_rating'], 5)
        self.assertFalse('patient_name' in profiles.columns)

    def test_stale_and_small_samples_keep_observed_facts_without_a_rank_value(self):
        rows = [
            aggregate(response_count=29),
            aggregate('fdr:jaipur-b', response_count=100, period_start='2025-08-01', period_end='2025-11-20'),
            aggregate('fdr:delhi', response_count=30, period_start='2025-08-01', period_end='2025-11-21'),
        ]
        profiles = prepare_patient_experience(sites(), pd.DataFrame(rows), AS_OF).set_index('facility_id')
        self.assertEqual(profiles['patient_experience_status'].tolist(), ['small_sample', 'stale', 'eligible', 'unknown'])
        self.assertTrue(profiles.loc[['fdr:jaipur-a', 'fdr:jaipur-b', 'nqas:unresolved'], 'patient_experience_value'].isna().all())
        self.assertEqual(profiles.loc['fdr:jaipur-a', 'patient_rating_response_count'], 29)
        self.assertEqual(profiles.loc['fdr:jaipur-b', 'patient_rating_period_end'], '2025-11-20')

    def test_ambiguous_ids_and_invalid_aggregate_values_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, 'duplicate facility_id'):
            prepare_patient_experience(sites(), pd.DataFrame([aggregate(), aggregate(rating=2)]), AS_OF)
        with self.assertRaisesRegex(ValueError, 'absent from the prepared entities'):
            prepare_patient_experience(sites(), pd.DataFrame([aggregate('missing')]), AS_OF)
        for edits, message in [
            ({'period_end': '2026-02-30'}, 'valid YYYY-MM-DD'), ({'period_end': '2026-11-22'}, 'extend past as_of'),
            ({'source_url': 'http://example.invalid'}, 'HTTPS link'), ({'source_url': 'https://name:key@example.invalid'}, 'embedded credentials'),
            ({'rating_max': 'inf'}, 'finite number'), ({'rating': 0}, 'increasing finite rating scale'),
            ({'response_count': -1}, 'whole number'), ({'response_count': 30.5}, 'whole number'),
        ]:
            with self.subTest(edits=edits), self.assertRaisesRegex(ValueError, message):
                prepare_patient_experience(sites(), pd.DataFrame([aggregate(**edits)]), AS_OF)

    def test_existing_identifiers_are_matched_verbatim(self):
        legacy_id = 'fdr:jaipur-a\r\n'
        entities = sites().iloc[:1].copy()
        entities.loc[0, 'facility_id'] = legacy_id
        profiles = prepare_patient_experience(entities, pd.DataFrame([aggregate(legacy_id)]), AS_OF)
        self.assertEqual(profiles.loc[0, 'facility_id'], legacy_id)
        self.assertEqual(profiles.loc[0, 'patient_experience_status'], 'eligible')
        with self.assertRaisesRegex(ValueError, 'absent from the prepared entities'):
            prepare_patient_experience(entities, pd.DataFrame([aggregate(legacy_id.strip())]), AS_OF)


if __name__ == '__main__':
    unittest.main()
