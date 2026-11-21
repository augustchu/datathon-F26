from pathlib import Path
import unittest

import pandas as pd

from facility_tool.data import link_entities, load_records
from facility_tool.geo_audit import audit_locations
from facility_tool.geography import add_postal_context, build_postal_areas


ROOT = Path(__file__).resolve().parents[2]
PIN = '302001'


def source_row(identifier, lat, lon, *, name=None, pin=PIN, state='Rajasthan', address='1 Fictional Main Road'):
    return dict(unique_id=identifier, name=name or identifier, latitude=lat, longitude=lon,
                address_line1=address, address_stateOrRegion=state, address_zipOrPostcode=pin,
                source_urls=[f'https://example.invalid/{identifier}'])


def postal_row(pin, state, district, lat, lon):
    return dict(pincode=pin, statename=state, district=district, latitude=lat, longitude=lon)


def records_for(rows):
    return load_records(ROOT, pd.DataFrame(rows), include_public=False)


class GeographicAuditTests(unittest.TestCase):
    def test_raw_coordinate_problems_are_kept_and_never_corrected_by_inference(self):
        records = records_for([
            source_row('bad-swap', '75.7800', '26.9100'),
            source_row('missing', None, 75.78),
            source_row('zero', 26.91, 0),
            source_row('outside', 51.5, -0.1),
            source_row('text', 'unknown', 75.78),
            dict(source_row('documented-zero', None, None), coordinate_quality='zero_placeholder'),
        ])
        entities, links = link_entities(records)
        original = entities.copy(deep=True)
        postal = build_postal_areas(pd.DataFrame([postal_row(PIN, 'Rajasthan', 'Jaipur', 26.91, 75.78)]))
        source_audit, branches, summary = audit_locations(records, entities, postal, links=links)
        by_name = source_audit.set_index('name')
        self.assertEqual(by_name.loc['bad-swap', 'raw_latitude'], '75.7800')
        self.assertEqual(by_name.loc['bad-swap', 'raw_longitude'], '26.9100')
        self.assertEqual(by_name.loc['bad-swap', 'base_record_id'], 'fdr:bad-swap')
        self.assertEqual(by_name['coordinate_status'].to_dict(), {
            'bad-swap': 'likely_lat_lon_swap', 'missing': 'missing_coordinates',
            'zero': 'zero_coordinate_placeholder', 'outside': 'outside_india_bounds', 'text': 'invalid_coordinates',
            'documented-zero': 'zero_coordinate_placeholder',
        })
        self.assertTrue(pd.isna(by_name.loc['documented-zero', 'raw_latitude']))
        self.assertTrue(branches['lat'].isna().all())
        self.assertFalse(branches['location_routing_eligible'].any())
        self.assertIn('appear to be swapped', branches.set_index('name').loc['bad-swap', 'location_hold_reason'])
        self.assertTrue(by_name['postal_centroid_distance_km'].isna().all())
        self.assertTrue(by_name['postal_centroid_lat'].eq(26.91).all())
        metric = summary.loc[(summary['scope'] == 'record') & (summary['check'] == 'likely_lat_lon_swap')].iloc[0]
        self.assertEqual((metric['source'], metric['count'], metric['total']), ('fdr', 1, 6))
        pd.testing.assert_frame_equal(entities, original)

    def test_exact_coordinate_collision_counts_distinct_branches_and_retains_links(self):
        records = records_for([
            source_row('a', 26.91, 75.78, name='Branch A'),
            source_row('a-secondary', 26.91, 75.78, name='Branch A'),
            source_row('b', 26.91, 75.78, name='Branch B'),
            source_row('c', 26.911, 75.78, name='Nearby branch'),
        ])
        entities, links = link_entities(records)
        source_audit, branches, _ = audit_locations(records, entities, build_postal_areas(pd.DataFrame()), links=links)
        by_record = source_audit.set_index('record_id')
        self.assertEqual(by_record.loc['fdr:a-secondary', 'link_status'], 'linked')
        self.assertEqual(by_record.loc['fdr:a-secondary', 'base_record_id'], 'fdr:a')
        self.assertEqual(by_record.loc['fdr:a-secondary', 'shared_coordinate_facility_count'], 2)
        by_name = branches.set_index('name')
        self.assertEqual(by_name.loc['Branch A', 'location_source_urls'], ['https://example.invalid/a'])
        self.assertEqual(by_name.loc['Branch A', 'location_quality'], 'source_coordinates_unverified')
        self.assertIn('shared_exact_coordinates', by_name.loc['Branch B', 'review_flags'])
        self.assertEqual(set(by_name.loc['Branch A', 'linked_review_record_ids']), {'fdr:a', 'fdr:a-secondary'})
        self.assertFalse(by_name.loc['Nearby branch', 'review_needed'])

    def test_postal_labels_and_centroid_distance_keep_their_distinct_uncertainties(self):
        records = records_for([
            source_row('bad-state', 26.91, 75.78, state='Bihar'),
            source_row('bad-district', 26.92, 75.78),
            source_row('ambiguous', 26.93, 75.78),
            source_row('resolved', 26.94, 75.78),
            source_row('far', 28.1, 76.3),
            source_row('alias', 20.297, 85.82, pin='751001', state='Orissa'),
            source_row('address-conflict', 26.915, 75.78, address='Fictional address PIN code 302 019'),
        ])
        records.loc[records['name'].eq('bad-district'), 'district'] = 'Jodhpur'
        records.loc[records['name'].isin(['resolved', 'far']), 'district'] = 'Jaipur'
        entities, links = link_entities(records)
        areas = build_postal_areas(pd.DataFrame([
            postal_row(PIN, 'Rajasthan', 'Jaipur', 26.91, 75.78),
            postal_row(PIN, 'Rajasthan', 'Ajmer', 26.45, 74.63),
            postal_row('751001', 'Odisha', 'Khordha', 20.296, 85.82),
        ]))
        source_audit, branches, _ = audit_locations(records, entities, areas, links=links)
        by_name = branches.set_index('name')
        self.assertIn('pin_state_conflict', by_name.loc['bad-state', 'review_flags'])
        self.assertIn('pin_district_conflict', by_name.loc['bad-district', 'review_flags'])
        self.assertEqual(by_name.loc['bad-district', 'postal_candidate_labels'], ['Ajmer, Rajasthan', 'Jaipur, Rajasthan'])
        self.assertIn('ambiguous_pin_area', by_name.loc['ambiguous', 'review_flags'])
        self.assertTrue(pd.isna(by_name.loc['ambiguous', 'postal_centroid_distance_km']))
        self.assertEqual(by_name.loc['resolved', 'postal_district'], 'Jaipur')
        self.assertIn('far_from_postal_centroid', by_name.loc['far', 'review_flags'])
        self.assertGreater(by_name.loc['far', 'postal_centroid_distance_km'], 50)
        self.assertEqual((by_name.loc['far', 'lat'], by_name.loc['far', 'postal_centroid_lat']), (28.1, 26.91))
        self.assertEqual((by_name.loc['alias', 'state'], by_name.loc['alias', 'postal_state']), ('Orissa', 'Odisha'))
        self.assertEqual(add_postal_context(entities, areas).set_index('name').loc['resolved', 'postal_district'], 'Jaipur')
        review = source_audit.set_index('name').loc['address-conflict']
        self.assertEqual(review['explicit_address_pins'], ['302019'])
        self.assertIn('declared_pin_conflicts_with_address', review['review_flags'])

    def test_routing_holds_are_computed_before_lookup_and_leave_review_only_branches_eligible(self):
        records = records_for([
            source_row('bad-state', 26.92, 75.78, state='Bihar'),
            source_row('published-conflict', 26.91, 75.78),
            source_row('approximate-site', 26.912, 75.781),
            source_row('district-only', 26.913, 75.782),
            source_row('far', 28.1, 76.3),
            source_row('shared-a', 26.915, 75.784),
            source_row('shared-b', 26.915, 75.784),
        ])
        records.loc[records['name'].eq('district-only'), 'district'] = 'Ajmer'
        entities, links = link_entities(records)
        entities.loc[entities['name'].eq('published-conflict'), 'location_conflict'] = True
        entities.loc[entities['name'].eq('approximate-site'), 'location_quality'] = 'postal_centroid'
        postal = build_postal_areas(pd.DataFrame([postal_row(PIN, 'Rajasthan', 'Jaipur', 26.91, 75.78)]))
        _, branches, _ = audit_locations(records, entities, postal, links=links)
        by_name = branches.set_index('name')
        self.assertEqual(set(by_name.index[~by_name['location_routing_eligible']]),
                         {'bad-state', 'published-conflict', 'approximate-site'})
        self.assertIn('disagrees with the PIN directory', by_name.at['bad-state', 'location_hold_reason'])
        self.assertIn('location details conflict', by_name.at['published-conflict', 'location_hold_reason'])
        self.assertIn('usable site point', by_name.at['approximate-site', 'location_hold_reason'])
        self.assertEqual(by_name.at['approximate-site', 'location_quality'], 'postal_centroid')
        for facility, flag in (
            ('district-only', 'pin_district_conflict'),
            ('far', 'far_from_postal_centroid'),
            ('shared-a', 'shared_exact_coordinates'),
        ):
            self.assertIn(flag, by_name.at[facility, 'review_flags'])
            self.assertEqual(by_name.at[facility, 'location_hold_reason'], '')


if __name__ == '__main__':
    unittest.main()
