from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

import pandas as pd

from facility_tool.data import extract_claims, link_entities, load_records
from facility_tool.examples import demo_inputs, demo_overlays, demo_pressure
from facility_tool.geo_audit import attach_routing_review, audit_locations
from facility_tool.geography import build_postal_areas
from facility_tool.lookup import lookup, side_by_side
from facility_tool.moderation import ModerationStack
from facility_tool.patient_experience import prepare_patient_experience
from facility_tool.quality import build_quality_profiles
from facility_tool.query_assets import prepare_lookup_assets
from facility_tool.recommend import prepare_service_candidates
from facility_tool.response import CONTACT_FIELDS, new_alternatives, record_call, report_unconfirmed_call
from facility_tool.updates import contact_evidence, load_estimates
from facility_tool.verification import verify_services
from facility_tool.workspace import Store, load_inputs, read_config


ROOT = Path(__file__).resolve().parents[2]


class TravelLookupTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = read_config(ROOT)
        self.config['traveltime']['max_destinations'] = 3
        self.store = Store(ROOT, 'public', self.config['as_of'], temp.name)
        self.branches = {'far': 77.90, **{f'branch-{index}': 77 + index / 100 for index in range(1, 7)},
                         'location-hold': 77.07, 'evidence-hold': 77.08}
        entities = pd.DataFrame([
            dict(facility_id=ident, name=ident, address='Example address', city='Example city',
                 district='Example district', state='Example state', pin='110001', lat=28.0, lon=lon,
                 location_quality='source_coordinates_unverified', location_routing_eligible=ident != 'location-hold',
                 location_hold_reason='Conflicting branch coordinates' if ident == 'location-hold' else '')
            for ident, lon in self.branches.items()
        ])
        claims = pd.DataFrame([
            dict(facility_id=ident, record_id=f'fixture-{ident}', service='dialysis', stance='supports',
                 excerpt='Dialysis offered here', source_url='https://example.invalid/branch', source='fixture',
                 scope='Whole branch', validity='active', citation_quality='candidate_links' if ident == 'evidence-hold' else 'direct')
            for ident in self.branches
        ])
        prepared = prepare_service_candidates(entities, claims, 'dialysis', as_of=self.config['as_of'])
        self.store.write('lookup_candidates_dialysis', prepared.assign(**dict.fromkeys(CONTACT_FIELDS)))
        self.store.write('origin_index', pd.DataFrame(columns=['lookup_key']))
        credentials = patch.dict('os.environ', {'TRAVELTIME_APP_ID': 'test-app', 'TRAVELTIME_API_KEY': 'test-key'})
        credentials.start()
        self.addCleanup(credentials.stop)

    def _route_matrix(self, payload, *, unreachable=(), missing=()):
        search = payload['departure_searches'][0]
        travel_mode = search['transportation']['type']
        branches = {lon: ident for ident, lon in self.branches.items()}
        routes, omitted = [], []
        for target in payload['locations'][1:]:
            ident = branches[target['coords']['lng']]
            self.assertNotIn(ident, {'location-hold', 'evidence-hold'})
            if (travel_mode, ident) in unreachable:
                omitted.append(target['id'])
            elif (travel_mode, ident) not in missing:
                order = 0 if ident == 'far' else int(ident[-1])
                properties = {'travel_time': 9_000 + order * 60}
                if travel_mode == 'driving':
                    properties['distance'] = 110_000 + 2_000 * order
                routes.append({'id': target['id'], 'properties': [properties]})
        return {'results': [{'search_id': search['id'], 'locations': routes, 'unreachable': omitted}]}

    def _lookup(self, **kwargs):
        return lookup(self.store, self.config, concern='dialysis', location='28, 77', **kwargs)

    @staticmethod
    def _confirmed(result):
        return result['rows'].query("mode == 'confirmed' and kind == 'recommendation'")

    @patch('facility_tool.travel._post_json')
    def test_all_documented_branches_routed_and_only_five_ranked(self, post):
        post.side_effect = lambda payload, **_: self._route_matrix(payload, missing={('driving', 'branch-6')})
        result = self._lookup()
        rows = self._confirmed(result)
        self.assertEqual(rows['facility_id'].tolist(), ['far', 'branch-1', 'branch-2', 'branch-3', 'branch-4'])
        self.assertGreater(rows.iloc[0]['distance_km'], 50)
        self.assertTrue(rows['ranking_travel_metric'].eq('route_distance_km').all())
        self.assertEqual(post.call_count, 3)
        self.assertEqual(result['routing']['comparison_status'], 'partial')
        self.assertEqual(result['routing']['eligible_count'], 7)
        self.assertEqual(result['routing']['unavailable_count'], 1)
        self.assertEqual(set(result['route_status']['facility_id']), set(self.branches) - {'location-hold', 'evidence-hold'})
        self.assertIn('route distance', rows.iloc[0]['fit_score_basis'])

    @patch('facility_tool.travel._post_json')
    def test_keeps_best_by_car_in_five_when_public_transport_cannot_reach_it(self, post):
        post.side_effect = lambda payload, **_: self._route_matrix(payload, unreachable={('public_transport', 'far')})
        result = self._lookup(travel_mode='public_transport')
        rows = self._confirmed(result)
        self.assertEqual(rows['facility_id'].tolist(), ['branch-1', 'branch-2', 'branch-3', 'branch-4', 'far'])
        self.assertEqual(rows['rank'].tolist(), [1, 2, 3, 4, 5])
        self.assertEqual(rows['travel_mode'].tolist(), ['public_transport'] * 4 + ['driving'])
        self.assertEqual(rows.iloc[-1]['driving_rank'], 1)
        self.assertIn('Car only for this search', rows.iloc[-1]['route_note'])
        self.assertEqual(rows.iloc[-1]['requested_mode_route_status'], 'not_routed_within_limit')
        self.assertEqual(result['routing']['driving_fallback_count'], 1)
        self.assertEqual(post.call_count, 6)
        compared = side_by_side(result['rows']).set_index('facility_id')
        self.assertEqual(compared.index.tolist(), rows['facility_id'].tolist())
        self.assertEqual(compared.loc['far', 'travel_mode'], 'driving')
        self.assertIn('Car only', compared.loc['far', 'route_note'])

    @patch('facility_tool.travel._post_json')
    def test_missing_alternate_estimate_does_not_assert_car_only(self, post):
        post.side_effect = lambda payload, **_: self._route_matrix(
            payload, missing={('cycling', branch) for branch in self.branches},
        )
        result = self._lookup(travel_mode='cycling')
        rows = self._confirmed(result)
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows['travel_mode'].eq('driving').all())
        self.assertTrue(rows['route_note'].str.contains('estimate was unavailable').all())
        self.assertEqual(result['routing']['comparison_status'], 'unavailable')


class LookupWorkflowTest(unittest.TestCase):
    def test_private_export_keeps_column_order_and_skips_other_tables(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / 'facilities.json'
            columns = ['countries', 'capacity', 'numberDoctors', 'description', 'source_urls']
            rows = [['India', '40 beds', '7', 'Example', ['https://example.invalid/branch']]]
            source.write_text(json.dumps({'columns': columns, 'rows': rows}), encoding='utf-8')
            with patch.dict('os.environ', {'DATATHON_FACILITY_EXPORT': str(source)}):
                inputs = load_inputs('export', read_config(ROOT))
            self.assertEqual(inputs['facilities'].columns.tolist(), columns)
            self.assertEqual(inputs['facilities'].iloc[0]['source_urls'], rows[0][-1])
            self.assertTrue(inputs['pins'].empty)
            self.assertTrue(inputs['nfhs'].empty)

    def test_import_estimates_validates_units_and_never_accepts_inferred_contacts(self):
        with TemporaryDirectory() as tmp:
            estimate = Path(tmp) / 'overlay.csv'
            header = 'facility_id,field,method,value,unit,lower,upper,provenance,detail\n'
            estimate.write_text(header + 'a,capacity,regression,30,beds,20,40,Fixture,Test\n', encoding='utf-8')
            imported = load_estimates(estimate, ['a'], services=['dialysis'])
            self.assertEqual(imported.iloc[0]['value'], 30)
            for field in ['phone_numbers', 'latitude', 'service:unknown']:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    estimate.write_text(header + f'a,{field},ml,1,,,,Fixture,Test\n', encoding='utf-8')
                    load_estimates(estimate, ['a'], services=['dialysis'])

    def test_cited_result_follow_up_and_moderation(self):
        config = read_config(ROOT)
        inputs = demo_inputs()
        records = load_records(ROOT, inputs['facilities'], include_public=False)
        entities, links = link_entities(records)
        postal = build_postal_areas(inputs['pins'])
        _, locations, _ = audit_locations(records, entities, postal, links=links)
        entities = attach_routing_review(entities, locations)
        estimates, _ = demo_overlays(entities, config['as_of'])
        claims = extract_claims(records, links, config['service_aliases'], config['as_of'])
        verification = verify_services(claims, config['as_of'])
        _, profiles = build_quality_profiles(records, links, claims, verification, config['as_of'])
        with TemporaryDirectory() as tmp:
            store = Store(ROOT, 'demo', config['as_of'], tmp)
            sources = {
                'entities': entities, 'postal_areas': postal, 'location_entity_review': locations,
                'verified_claims': claims, 'service_verification': verification, 'quality_profiles': profiles,
                'patient_experience': prepare_patient_experience(entities, pd.DataFrame(), config['as_of']),
                'field_updates': estimates, 'contact_evidence': contact_evidence(records, links),
                'pressure_scenarios': demo_pressure(entities),
            }
            for key, frame in sources.items():
                store.write(key, frame)
            prepare_lookup_assets(store, config)
            # A normal search has no dependency on upstream input tables or geographic audits.
            for key in sources:
                (store.path / f'{key}.jsonl').unlink()
            with patch('facility_tool.lookup.estimate_travel') as routing:
                settings = dict(concern='I need dialysis', location='Jaipur, Rajasthan', traveltime_enabled=True)
                result = lookup(store, config, **settings)
                routing.assert_not_called()
                self.assertEqual((result['transport_mode'], result['route_sort']), ('driving', 'distance'))
                self.assertEqual(result['facility_sort'], 'balanced')
                self.assertEqual(lookup(store, config, **settings, facility_sort='closest')['facility_sort'], 'closest')
                transit = lookup(store, config, **settings, travel_mode='public_transport')
                self.assertEqual((transit['transport_mode'], transit['route_sort']), ('public_transport', 'time'))
                with self.assertRaisesRegex(ValueError, 'Choose time'):
                    lookup(store, config, **settings, travel_mode='public_transport', route_sort='distance')
            recommended = result['rows'].query("mode == 'confirmed' and kind == 'recommendation'")
            with_phone = recommended.loc[recommended['phone_number'].notna()].iloc[0]
            self.assertTrue(with_phone['phone_source_urls'][0].startswith('https://example.invalid/'))
            self.assertTrue(with_phone['location_source_urls'][0].startswith('https://example.invalid/'))
            self.assertIn('Heuristic fit score', with_phone['fit_score_basis'])
            self.assertEqual(with_phone['verification_status'], 'single_direct_source')
            self.assertEqual(with_phone['quality_assessment_status'], 'unknown')
            self.assertEqual(with_phone['patient_experience_status'], 'unknown')
            self.assertTrue(with_phone['patient_reviews_search_url'].startswith('https://www.google.com/maps/search/'))

            first = recommended.iloc[0]['facility_id']
            feedback = record_call((), 'dialysis', first, confirmed=False)
            revised = lookup(store, config, **settings, feedback=feedback)
            self.assertEqual(revised['excluded_by_caller'], [first])
            self.assertNotIn(first, set(revised['rows']['facility_id']))
            alternatives = new_alternatives(result['rows'], revised['rows'])
            self.assertEqual(set(alternatives['mode']), {'confirmed', 'ml', 'regression'})
            stack = ModerationStack(Path(tmp) / 'reports.sqlite3', allowed_services=config['service_aliases'])
            report = report_unconfirmed_call(feedback, 'dialysis', first, moderation_stack=stack)
            self.assertEqual(stack.list_reports(status='open')[0].report_id, report.report_id)
            self.assertEqual(stack.list_reports(status='open')[0].service, 'dialysis')

            another_caller = lookup(store, config, **settings)
            self.assertEqual(another_caller['excluded_by_caller'], [])
            self.assertIn(first, set(another_caller['rows']['facility_id']))
            positive = record_call(feedback, 'dialysis', first, confirmed=True)
            with self.assertRaises(ValueError):
                report_unconfirmed_call(positive, 'dialysis', first, moderation_stack=stack)

            dialysis_ids = claims.loc[claims['service'].eq('dialysis') & claims['stance'].eq('supports'),
                                      'facility_id'].drop_duplicates()
            ratings = pd.DataFrame([
                {'facility_id': facility, 'source': 'Example survey',
                 'source_url': 'https://survey.example.invalid/clinics', 'metric': 'Overall satisfaction',
                 'period_start': '2026-01-01', 'period_end': '2026-06-30',
                 'rating': 4.5, 'rating_min': 1, 'rating_max': 5, 'response_count': 70}
                for facility in dialysis_ids
            ])
            sources['patient_experience'] = prepare_patient_experience(entities, ratings, config['as_of'])
            for key, frame in sources.items():
                store.write(key, frame)
            prepare_lookup_assets(store, config)
            rated = lookup(store, config, **settings)['rows'].query("mode == 'confirmed' and kind == 'recommendation'")
            self.assertTrue(rated['patient_ranking_status'].eq('applied').all())
            self.assertTrue(rated['patient_experience_credit'].gt(0).all())
            self.assertTrue(rated['patient_rating_response_count'].eq(70).all())


if __name__ == '__main__':
    unittest.main()
