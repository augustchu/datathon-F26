from pathlib import Path
import unittest

import pandas as pd

from facility_tool.data import link_entities, load_records
from facility_tool.geo_audit import attach_routing_review, audit_locations
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

    def test_pinless_state_contradiction_uses_trimmed_reference_and_keeps_border_points(self):
        records = records_for([
            source_row('delhi-point-for-punjab', 28.61288, 77.16731, pin='', state='Punjab'),
            source_row('plausible-punjab', 30.44, 74.55, pin='', state='Punjab'),
            source_row('near-reference-margin', 30.44, 75.1, pin='', state='Punjab'),
            source_row('ambiguous-state-label', 28.61, 77.17, pin='', state='Punjab / Delhi'),
            source_row('known-alias', 28.61, 77.17, pin='', state='Orissa'),
        ])
        entities, links = link_entities(records)
        references = [
            postal_row(str(150001 + i), 'Punjab', 'Faridkot', 30.2 + i * .015, 74.2 + i * .018)
            for i in range(35)
        ] + [
            postal_row(str(751001 + i), 'Odisha', 'Khordha', 20.2 + i * .015, 85.2 + i * .018)
            for i in range(35)
        ]
        # Save one obviously erroneous same-state postal point. It must not
        # expand the envelope around the wrong facility coordinate.
        references.append(postal_row('150050', 'Punjab', 'Faridkot', 28.61288, 77.16731))
        postal = build_postal_areas(pd.DataFrame(references))
        source, branches, _ = audit_locations(records, entities, postal, links=links)
        by_name = branches.set_index('name')
        bad = by_name.loc['delhi-point-for-punjab']
        self.assertFalse(bad['location_routing_eligible'])
        self.assertIn('state_point_conflict', bad['review_flags'])
        self.assertEqual(bad['state_point_status'], 'outside_buffered_reference')
        self.assertEqual(bad['state_point_reference_count'], 36)
        self.assertGreater(bad['state_point_outside_km'], 75)
        self.assertIn('state_point_conflict', source.set_index('name').loc['delhi-point-for-punjab', 'review_flags'])
        self.assertTrue(by_name.loc['plausible-punjab', 'location_routing_eligible'])
        self.assertTrue(by_name.loc['near-reference-margin', 'location_routing_eligible'])
        self.assertGreater(by_name.loc['near-reference-margin', 'state_point_outside_km'], 0)
        self.assertTrue(by_name.loc['ambiguous-state-label', 'location_routing_eligible'])
        self.assertEqual(by_name.loc['known-alias', 'state_point_reference_state'], 'odisha')
        self.assertFalse(by_name.loc['known-alias', 'location_routing_eligible'])

    def test_far_centroid_holds_only_with_explicit_unambiguous_clustered_pin(self):
        records = records_for([
            source_row('documented-pin', 25.0, 73.4),
            source_row('address-has-unnamed-number', 25.01, 73.41, pin='', address='Fictional address 302001'),
            source_row('scatter-in-postal-points', 25.02, 73.42, pin='302019'),
        ])
        entities, links = link_entities(records)
        references = [
            postal_row(str(303001 + i), 'Rajasthan', 'Jaipur', 24 + i * .14, 72 + i * .15)
            for i in range(35)
        ] + [
            postal_row(PIN, 'Rajasthan', 'Jaipur', 26.91, 75.78),
            postal_row('302019', 'Rajasthan', 'Jaipur', 26.91, 75.78),
            postal_row('302019', 'Rajasthan', 'Jaipur', 26.92, 75.79),
            postal_row('302019', 'Rajasthan', 'Jaipur', 25.02, 73.42),
        ]
        postal = build_postal_areas(pd.DataFrame(references))
        _, branches, _ = audit_locations(records, entities, postal, links=links)
        by_name = branches.set_index('name')
        self.assertEqual(set(by_name['state_point_status']), {'within_buffered_reference'})
        self.assertFalse(by_name.loc['documented-pin', 'location_routing_eligible'])
        self.assertIn('reliable_pin_point_conflict', by_name.loc['documented-pin', 'review_flags'])
        self.assertTrue(by_name.loc['address-has-unnamed-number', 'location_routing_eligible'])
        self.assertTrue(by_name.loc['scatter-in-postal-points', 'location_routing_eligible'])
        self.assertIn('far_from_postal_centroid', by_name.loc['scatter-in-postal-points', 'review_flags'])
        self.assertGreater(by_name.loc['scatter-in-postal-points', 'postal_office_point_spread_km'], 150)

    def test_exact_district_reference_catches_wrong_same_state_point_conservatively(self):
        rows = [
            source_row('far-in-same-state', 28.60595, 77.19496, pin='', state='Uttar Pradesh',
                       address='Fictional road, Dist. Sonbhadra'),
            source_row('near-district-margin', 24.85, 83.08, pin='', state='Uttar Pradesh',
                       address='Fictional road, Dist. Sonbhadra'),
            source_row('unverified-label-variant', 28.60596, 77.19497, pin='', state='Uttar Pradesh',
                       address='Fictional road, Dist. Sonebhadra'),
            source_row('conflicting-districts', 28.60597, 77.19498, pin='', state='Uttar Pradesh',
                       address='Fictional road, Dist. Sonbhadra'),
            source_row('pin-disagrees', 28.60598, 77.19499, pin='201306', state='Uttar Pradesh',
                       address='Fictional road, Dist. Sonbhadra'),
            source_row('noisy-district-reference', 28.60599, 77.195, pin='', state='Uttar Pradesh',
                       address='Fictional road, Dist. Example Large District'),
        ]
        records = records_for(rows)
        records.loc[records['name'].eq('conflicting-districts'), 'district'] = 'Gautam Buddh Nagar'
        references = [
            postal_row(str(231001 + i), 'Uttar Pradesh', 'Sonbhadra', 24.35 + i * .04, 83 + i * .02)
            for i in range(6)
        ] + [
            postal_row(str(223001 + i), 'Uttar Pradesh', 'Example Large District', 24.3 + i * .15, 77.2 + i * .17)
            for i in range(35)
        ] + [postal_row('201306', 'Uttar Pradesh', 'Gautam Buddh Nagar', 28.61, 77.195)]
        entities, links = link_entities(records)
        _, branches, _ = audit_locations(records, entities, build_postal_areas(pd.DataFrame(references)), links=links)
        by_name = branches.set_index('name')
        self.assertEqual(set(by_name.index[~by_name['location_routing_eligible']]), {'far-in-same-state'})
        wrong = by_name.loc['far-in-same-state']
        self.assertEqual(wrong['state_point_status'], 'within_buffered_reference')
        self.assertEqual(wrong['district_point_reference_count'], 6)
        self.assertIn('district_point_conflict', wrong['review_flags'])
        self.assertEqual(by_name.loc['near-district-margin', 'district_point_status'], 'within_buffered_reference')
        for name in ('unverified-label-variant', 'noisy-district-reference'):
            self.assertEqual(by_name.loc[name, 'district_point_status'], 'reference_unavailable')
        for name in ('conflicting-districts', 'pin-disagrees'):
            self.assertEqual(by_name.loc[name, 'district_point_status'], 'conflicting_district_labels')

    def test_shared_point_holds_three_distinct_state_claims_without_pin_data(self):
        records = records_for([
            source_row('alias-delhi', 22.351115, 78.667743, pin='', state='NCT Delhi'),
            source_row('delhi', 22.351115, 78.667743, pin='', state='Delhi'),
            source_row('punjab', 22.351115, 78.667743, pin='', state='Punjab'),
            source_row('rajasthan', 22.351115, 78.667743, pin='', state='Rajasthan'),
            source_row('campus-one', 26.91, 75.78, pin='', state='Rajasthan'),
            source_row('campus-two', 26.91, 75.78, pin='', state='Rajasthan'),
        ])
        entities, links = link_entities(records)
        _, branches, _ = audit_locations(records, entities, build_postal_areas(pd.DataFrame()), links=links)
        by_name = branches.set_index('name')
        self.assertEqual(set(by_name.index[~by_name['location_routing_eligible']]),
                         {'alias-delhi', 'delhi', 'punjab', 'rajasthan'})
        self.assertEqual(by_name.loc['alias-delhi', 'shared_point_state_labels'], ['delhi', 'punjab', 'rajasthan'])
        self.assertIn('shared_exact_coordinates', by_name.loc['campus-one', 'review_flags'])

    def test_linked_point_keeps_its_source_state_when_branch_has_none_and_holds_disagreement(self):
        records = records_for([
            source_row('no-state', None, None, pin='', state=''),
            source_row('declared-delhi', None, None, pin='', state='New Delhi'),
            source_row('point-source', 30.67, 74.75, pin='', state='Punjab'),
        ])
        entities, links = link_entities(records)
        combined = entities['name'].isin(['no-state', 'declared-delhi'])
        entities.loc[combined, ['lat', 'lon']] = [30.67, 74.75]
        entities.loc[combined, 'location_record_id'] = 'fdr:point-source'
        entities.loc[combined, 'location_quality'] = 'source_coordinates_unverified'
        _, branches, _ = audit_locations(records, entities, build_postal_areas(pd.DataFrame()), links=links)
        by_name = branches.set_index('name')
        self.assertEqual(by_name.loc['no-state', 'state_point_reference_state'], 'punjab')
        self.assertEqual(by_name.loc['no-state', 'state_point_status'], 'reference_unavailable')
        self.assertTrue(by_name.loc['no-state', 'location_routing_eligible'])
        self.assertIn('linked_point_state_conflict', by_name.loc['declared-delhi', 'review_flags'])
        self.assertFalse(by_name.loc['declared-delhi', 'location_routing_eligible'])

    def test_reviewed_hold_is_bound_to_the_source_and_original_point(self):
        url = 'https://example.invalid/coordinate-review'
        hold = dict(record_id='fdr:reviewed', latitude=28.61288, longitude=77.16731, source_url=url,
                    reason='The source location is in a different state.')
        locations = [
            source_row('reviewed', 28.6128801, 77.16731, state='Punjab', pin=''),
            source_row('other-source', 28.61288, 77.16731, state='Punjab', pin=''),
        ]
        no_postal_data = build_postal_areas(pd.DataFrame())
        records = records_for(locations)
        entities, links = link_entities(records)
        _, branches, _ = audit_locations(records, entities, no_postal_data, links=links, reviewed_holds=[hold])
        by_name = branches.set_index('name')
        self.assertFalse(by_name.loc['reviewed', 'location_routing_eligible'])
        self.assertTrue(by_name.loc['other-source', 'location_routing_eligible'])
        self.assertEqual(by_name.loc['reviewed', 'reviewed_coordinate_reason'], hold['reason'])
        attached = attach_routing_review(entities, branches).set_index('name')
        self.assertIn(url, attached.loc['reviewed', 'location_source_urls'])
        records = records_for([dict(locations[0], latitude=30.675, longitude=74.755)])
        entities, links = link_entities(records)
        _, corrected, _ = audit_locations(records, entities, no_postal_data, links=links, reviewed_holds=[hold])
        self.assertTrue(corrected['location_routing_eligible'].all())


if __name__ == '__main__':
    unittest.main()
