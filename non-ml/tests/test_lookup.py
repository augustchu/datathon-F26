from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from facility_tool.data import extract_claims, link_entities, load_records
from facility_tool.examples import demo_inputs, demo_overlays, demo_pressure
from facility_tool.geo_audit import audit_locations
from facility_tool.geography import build_postal_areas
from facility_tool.lookup import lookup
from facility_tool.moderation import ModerationStack
from facility_tool.response import new_alternatives, record_call, report_unconfirmed_call
from facility_tool.travel import estimate_travel
from facility_tool.updates import contact_evidence, load_estimates
from facility_tool.workspace import Store, read_config


ROOT = Path(__file__).resolve().parents[2]


class LookupWorkflowTest(unittest.TestCase):
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
        estimates, _ = demo_overlays(entities, config['as_of'])
        with TemporaryDirectory() as tmp:
            store = Store(ROOT, 'demo', config['as_of'], tmp)
            sources = {
                'entities': entities, 'postal_areas': postal, 'location_entity_review': locations,
                'claims': extract_claims(records, links, config['service_aliases'], config['as_of']),
                'field_updates': estimates, 'contact_evidence': contact_evidence(records, links),
                'pressure_scenarios': demo_pressure(entities),
            }
            for key, frame in sources.items():
                store.write(key, frame)
            with patch('facility_tool.lookup.estimate_travel', wraps=estimate_travel) as routing:
                settings = dict(concern='I need dialysis', location='Jaipur, Rajasthan', traveltime_enabled=True)
                result = lookup(store, config, **settings)
                self.assertFalse(routing.call_args.kwargs['enabled'])
            recommended = result['rows'].query("mode == 'confirmed' and kind == 'recommendation'")
            with_phone = recommended.loc[recommended['phone_number'].notna()].iloc[0]
            self.assertTrue(with_phone['phone_source_urls'][0].startswith('https://example.invalid/'))
            self.assertTrue(with_phone['location_source_urls'][0].startswith('https://example.invalid/'))
            self.assertIn('Heuristic fit score', with_phone['fit_score_basis'])

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


if __name__ == '__main__':
    unittest.main()
